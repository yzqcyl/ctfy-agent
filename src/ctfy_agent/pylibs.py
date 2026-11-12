#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把工作区外挂依赖库（`tools/pylibs`）接进**当前进程**，供 agent 自己的工具使用。

背景
----
镜像只装了 capstone（`Dockerfile` 有意不动）。pycryptodome / sympy / gmpy2 / z3 /
pwntools 由 `tools/fetch_pylibs.py` 装到 `tools/pylibs/`（git 忽略、不进镜像）。
在此之前只有 `shell()` 子进程会把该目录塞进 `PYTHONPATH`（`tools_local.py`），
**进程内工具看不到**——所以 z3/ROP/大整数只能靠 shell 里现写脚本，还常被引号守卫拦。

本模块把同一份目录接进 `sys.path`，并提供 `available()` / `import_or_none()` /
`summary()` 给工具与启动日志用；目录不存在或库缺失时**静默退回纯 stdlib**，
与「镜像内没有这些库」的旧行为完全一致（绝不因为缺库把 run 弄挂）。

搜索顺序（先命中先用）
--------------------
1. `$CTFY_PYLIBS`（显式指定：平台/测试/将来镜像内置都能指过来）；
2. `/ctfy/pylibs`（将来若随镜像内置，放这里）；
3. `<项目根>/tools/pylibs`（本地 `fetch_pylibs.py` 的目标）。

注意：目录追加到 `sys.path` **末尾**（不是开头），避免外挂库遮住 stdlib 或包内模块。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import threading
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LOCK = threading.RLock()
_ADDED: list[str] = []
_IMPORT_CACHE: dict[str, object | None] = {}
_IMPORT_ERRORS: dict[str, str] = {}

# 模块名 → 人类可读标签（只用于日志/摘要，不参与导入）
_LABELS = {
    "Crypto": "pycryptodome",
    "Cryptodome": "pycryptodome",
    "z3": "z3",
    "pwn": "pwntools",
    "sympy": "sympy",
    "gmpy2": "gmpy2",
    "capstone": "capstone",
}


def candidate_dirs() -> list[Path]:
    """按优先级返回候选目录（含不存在的，方便诊断输出）。"""
    rows: list[Path] = []
    explicit = os.environ.get("CTFY_PYLIBS", "").strip()
    if explicit:
        rows.append(Path(explicit))
        strict = os.environ.get("CTFY_PYLIBS_STRICT", "").strip().lower()
        if strict in ("1", "true", "yes"):
            # 严格模式：只用显式目录（跨平台验证时避免把另一平台的 tools/pylibs 也挂进来，
            # 例如 WSL 里跑 Linux 版库时不该再去 import Windows 的 .pyd 包）
            return rows
    rows.append(Path("/ctfy/pylibs"))
    rows.append(_PROJECT_ROOT / "tools" / "pylibs")
    # 去重（不解析符号链接，保持顺序稳定）
    seen: set[str] = set()
    unique: list[Path] = []
    for path in rows:
        key = str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _add_windows_dll_dirs(base: Path) -> None:
    """Windows 上 gmpy2 这类包把 DLL 放在 `<pkg>.libs/`：进程内导入前要登记。"""
    if os.name != "nt":
        return
    add = getattr(os, "add_dll_directory", None)
    if add is None:  # Python < 3.8
        return
    for libs in sorted(base.glob("*.libs")):
        if not libs.is_dir():
            continue
        try:
            add(str(libs))
        except OSError:
            pass


def ensure_on_path(force: bool = False) -> list[str]:
    """把存在的候选目录加进 sys.path（幂等）。返回本次/历史新增的目录列表。"""
    with _LOCK:
        if _ADDED and not force:
            return list(_ADDED)
        for path in candidate_dirs():
            if not path.is_dir():
                continue
            text = str(path)
            _add_windows_dll_dirs(path)
            if text not in sys.path:
                sys.path.append(text)  # 追加在末尾：外挂库不许遮 stdlib/包内模块
            if text not in _ADDED:
                _ADDED.append(text)
        return list(_ADDED)


def available(name: str) -> bool:
    """`name` 能否被 finder 找到（不执行包 __init__，也不算「真能 import」）。"""
    ensure_on_path()
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def import_or_none(name: str):
    """尝试导入；任何异常都返回 None（缺库 → 调用方走退化路径）。

    结果按进程缓存（成功/失败都缓存）：pwntools 首次 import 要 1-3s，
    失败也会重复尝试成本高；缓存后工具调用不再反复触发。
    失败原因记进 `_IMPORT_ERRORS`，供 `unavailable_hint()` 区分「没装」和「装了但加载失败」。
    """
    if name in _IMPORT_CACHE:
        return _IMPORT_CACHE[name]
    if not available(name):
        return None  # 不缓存「找不到」：同进程后续可能 ensure_on_path 到新目录
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - 缺 DLL / 版本冲突都按「不可用」处理
        _IMPORT_ERRORS[name] = "%s: %s" % (type(exc).__name__, exc)
        _IMPORT_CACHE[name] = None
        return None
    _IMPORT_CACHE[name] = module
    _IMPORT_ERRORS.pop(name, None)
    return module


def usable(name: str) -> bool:
    """真能 import（含原生模块加载）才算可用——`available()` 只看 finder，不够。"""
    return import_or_none(name) is not None


def import_error(name: str) -> str:
    """上次导入失败的原因（没有失败记录返回空串）。"""
    return _IMPORT_ERRORS.get(name, "")


def summary(full: bool = False) -> str:
    """一行摘要，启动时打日志、doctor/排障时看「到底挂上了哪些库」。

    默认 `full=False` **只做 finder 探测、不 import**：`agent_main` 每次起 run 都会打这行日志，
    真 import sympy/pwntools 会拖慢 3-5 秒并带来无谓副作用。加载失败（如 Windows 拦截原生 DLL）
    会在**首次工具调用**时由 `unavailable_hint/blocked_hint` 明确报出；要启动时就看清失败原因用 `full=True`。
    """
    dirs = ensure_on_path()
    if not dirs:
        return "外挂库未启用（tools/pylibs 不存在；纯 stdlib 模式）"
    if not full:
        found: list[str] = []
        for mod, label in _LABELS.items():
            if available(mod) and label not in found:
                found.append(label)
        return "外挂库 %s：%s（加载状态延迟到首次使用）" % (
            dirs[0], "、".join(found) if found else "（目录在但没有可识别的库）")
    loaded: list[str] = []
    failed: list[str] = []
    for mod, label in _LABELS.items():
        if not available(mod):
            continue
        if import_or_none(mod) is not None:
            if label not in loaded:
                loaded.append(label)
        elif label not in failed:
            failed.append("%s（%s）" % (label, _IMPORT_ERRORS.get(mod, "加载失败")))
    text = "外挂库 %s：%s" % (dirs[0], "、".join(loaded) if loaded else "（目录在但没有可用库）")
    if failed:
        text += "；加载失败：%s" % "、".join(failed)
    return text


def module_dir(name: str) -> str:
    """返回模块所在目录（诊断用；找不到返回空串）。"""
    ensure_on_path()
    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError, AttributeError):
        return ""
    if spec is None:
        return ""
    if spec.origin and spec.origin not in ("namespace", "built-in", "frozen"):
        return str(Path(spec.origin).parent)
    # namespace 包（无 __init__.py）用搜索路径
    return str((spec.submodule_search_locations or [""])[0] or "")


def missing_hint(what: str) -> str:
    """缺库时给模型/人看的统一提示（本地怎么装、镜像里是什么状态）。"""
    return (
        f"本环境没有 {what}：本地可 `python tools/fetch_pylibs.py {what}` 装进 tools/pylibs"
        "（git 忽略、不进镜像）；镜像内默认只带 capstone，缺库时按纯 stdlib 路线解题，"
        "或用 shell 里的 fallback 脚本。"
    )


def unavailable_hint(what: str, module: str = "") -> str:
    """库不可用时的提示：区分「没装」与「装了但加载失败」（后者常是 Windows 应用控制策略）。

    Windows Smart App Control（VerifiedAndReputablePolicyState=1）会拦截 pip 装进来的
    未签名 `.pyd`/`.dll`（WinError 4551 / 0x11c7），表现为 import 直接失败；
    同一份库在 Linux 镜像里是正常的。提示里必须说清，否则模型会误以为「库不存在」而放弃。
    """
    err = import_error(module) if module else ""
    if err:
        return (
            f"{what} 已安装但加载失败：{err}"
            "（常见原因：Windows Smart App Control/应用控制策略拦截未签名的原生 .pyd/.dll，"
            "WinError 4551；Linux 镜像无此限制。可在本机核对 `python tools/fetch_pylibs.py --list`，"
            "或改用纯 stdlib 工具/在 WSL 里跑需要原生库的脚本）"
        )
    return missing_hint(what)


def blocked_hint(what: str, exc: BaseException) -> str:
    """把「原生模块被系统策略拦截」的真实异常翻译成可操作提示；不是这类错误返回空串。

    import 阶段能过、真正 new 一个 cipher 时才加载 `.pyd` 的库（pycryptodome 就是），
    必须在调用点兜住 OSError，否则裸异常会穿透到模型层。
    """
    text = "%s: %s" % (type(exc).__name__, exc)
    low = text.lower()
    markers = ("4551", "0x11c7", "应用程序控制策略", "application control policy",
               "cannot load native module", "dll load failed", "could not find module")
    if any(marker in low for marker in markers):
        return (
            f"{what} 的原生模块被系统策略拦截：{text}"
            "（Windows Smart App Control/应用控制策略会拦未签名 .pyd/.dll；Linux 镜像无此限制。"
            "本机可改用纯 stdlib 工具，或在 WSL 里跑依赖原生库的脚本）"
        )
    return ""