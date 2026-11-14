#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_pylibs.py — 把这些依赖装进工作区私有目录 tools/pylibs/（不进 git、不改运行时装机）。

为什么需要（2026-11-10 接手核实，2026-11-12 更新）
--------------------------------------------------
v6 镜像（Dockerfile）只装了 capstone，**没有** pycryptodome / gmpy2 / sympy / pwntools / z3，
于是 runs/plat 26 题里 CRYPTO 0/5、PWN 0/4，收尾结论反复是「本地没有库，只能纯 stdlib」。
v7 镜像已用 pip 内置 pwntools/pycryptodome/gmpy2/sympy/capstone，2026-11-12 又补了 z3-solver
（见 Dockerfile L194–231），**线上镜像不再需要这份外挂**。本脚本现在的用途是：给**本机
Windows 开发/本机 GUI/CLI 跑**补同一批库（本机不方便装进系统 Python）：
  * 只写 tools/pylibs/（git 忽略，二进制不该进 git）；
  * shell() 子进程会把该目录加进 PYTHONPATH（tools_local.shell）；
  * 容器里没有这个目录 → 自动退回 site-packages / 纯 stdlib，与旧行为一致。

本机为什么不能直接 pip install
-----------------------------
这台 Windows 上的 Python 3.12 会**真的按 POSIX mode 建目录**：`tempfile.mkdtemp()` 用的是
`os.mkdir(..., 0o700)`，建出来的目录连创建者自己都读不了、也 chmod 不回来（WinError 5）。
pip 解包时必须往自己的临时目录里写 `.whl.metadata` → 直接 `Errno 13 Permission denied`
（历史上 runs/ 下那 31 个「打不开」的 pip-* 目录就是这么来的，不是沙箱坏了）。
所以这里：① 把 TEMP/TMP 指到工作区内；② 把 tempfile.mkdtemp 换成 0o777 建目录。

用法
----
    python tools/fetch_pylibs.py                 # 默认装 CRYPTO/PWN 常用集（含 z3-solver / pwntools）
    python tools/fetch_pylibs.py pycryptodome z3-solver   # 只装指定包
    python tools/fetch_pylibs.py --list          # 看当前已装了什么

装完的库怎么进解题链路
----------------------
* `shell()` 子进程：自动挂 PYTHONPATH（tools_local.shell）；
* agent 进程内工具：`src/ctfy_agent/pylibs.py` 把该目录 append 进 sys.path，
  新增的 `z3solve` / `pycrypt` / `pwnrop` 就是用这份库（详见 docs/pylibs-bridge.md）。
注意：Windows 若开了 Smart App Control，会拦未签名 .pyd/.dll（z3 的 libz3.dll、
pycryptodome 的 _raw_ecb 等），表现为「装了但加载失败」——Linux 镜像无此限制。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "pylibs"
TMPROOT = ROOT / "runs" / "_tmp" / "pylibs-install"

DEFAULT_PACKAGES = [
    "pycryptodome", "sympy", "gmpy2", "pwntools", "z3-solver",
    # v8 能力补齐（2026-11-13，与 Dockerfile 的 cap-req 段一致，便于本机复现）
    "pillow", "zxing-cpp", "qrcode", "scapy", "dpkt", "pyzipper", "pycryptodomex",
    "uncompyle6",
]


def _patch_tempfile() -> None:
    """让 mkdtemp 用 0o777 建目录（0o700 在本机会造出进不去的目录）。"""
    real = tempfile.mkdtemp

    def mkdtemp(suffix=None, prefix=None, dir=None):
        suffix = suffix or ""
        prefix = tempfile.gettempprefix() if prefix is None else prefix
        directory = dir or tempfile.gettempdir()
        for _ in range(100):
            name = os.path.join(directory, f"{prefix}{next(tempfile._get_candidate_names())}{suffix}")
            try:
                os.mkdir(name, 0o777)
            except FileExistsError:
                continue
            return name
        return real(suffix=suffix, prefix=prefix, dir=dir)  # 极端情况退回原实现

    tempfile.mkdtemp = mkdtemp  # type: ignore[assignment]


def _prepare_env() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    TMPROOT.mkdir(parents=True, exist_ok=True)
    for key in ("TEMP", "TMP", "TMPDIR"):
        os.environ[key] = str(TMPROOT)
    _patch_tempfile()


def installed() -> list[str]:
    if not TARGET.is_dir():
        return []
    rows = []
    for entry in sorted(TARGET.iterdir()):
        if entry.is_dir() and not entry.name.endswith((".dist-info", ".data", "__pycache__")):
            rows.append(entry.name)
    return rows


# 关键库的真实导入检查。
# [教训 2026-11-10] pip install --target 被中断时目录会「半截」：实测 psutil 缺 _common.py，
# 结果 `from pwn import *` 报 "partially initialized module 'psutil'"，PWN 一上来就废。
# 装完必须真 import 一遍，别只看 pip 的退出码。
# 注意：**在进程内 import**，不要起子进程抓管道——Windows 沙盒下捕获子进程 stdio 会卡死。
_CHECK_LIBS = ("Crypto", "sympy", "gmpy2", "pwn", "z3")


def check_imports() -> int:
    """用「和 agent shell 相同」的 sys.path 真 import 关键库，返回失败数。"""
    sys.path.insert(0, str(TARGET))
    dll_dirs = [str(p) for p in TARGET.glob("*.libs") if p.is_dir()]
    if dll_dirs:
        os.environ["PATH"] = os.pathsep.join(dll_dirs) + os.pathsep + os.environ.get("PATH", "")
    bad = []
    for mod in _CHECK_LIBS:
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001 - 半截安装/缺 DLL 都要报出来
            bad.append((mod, repr(exc)[:200]))
    if not bad:
        print("验证：关键库 import 全部正常（Crypto/sympy/gmpy2/pwn/z3）")
        return 0
    for mod, why in bad:
        print(f"[verify] {mod} 导入失败：{why}", file=sys.stderr)
    print("\n[verify] 有库导入失败：多半是上次 pip --target 半截中断。"
          "重跑 `python tools/fetch_pylibs.py <该包>` 修复。", file=sys.stderr)
    return len(bad)


def main(argv: list[str]) -> int:
    if "--list" in argv:
        rows = installed()
        print("tools/pylibs 已装：" + ("、".join(rows) if rows else "（空）"))
        return 0
    if "--check" in argv:
        return check_imports()
    packages = [a for a in argv if not a.startswith("-")] or DEFAULT_PACKAGES
    _prepare_env()
    print(f"目标目录：{TARGET}")
    print(f"临时目录：{TMPROOT}")
    print("装：" + "、".join(packages))
    from pip._internal.cli.main import main as pip_main  # noqa: PLC0415 - 延迟导入

    code = pip_main([
        "install", "--target", str(TARGET), "--no-cache-dir",
        "--disable-pip-version-check", "--upgrade", *packages,
    ])
    # 清掉 pip 的临时残骸（尽力而为）
    for child in TMPROOT.glob("*"):
        shutil.rmtree(child, ignore_errors=True)
    if code == 0:
        print("\n完成。已装：" + "、".join(installed()))
        code = check_imports() or code
    else:
        print(f"\npip 退出码 {code}；可看上面日志定位是哪个包不支持本机 Python 3.12。", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))