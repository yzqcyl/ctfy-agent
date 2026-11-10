#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_pylibs.py — 把这些依赖装进工作区私有目录 tools/pylibs/（不进 git、不改运行时装机）。

为什么需要（2026-11-10 接手核实）
----------------------------------
镜像（Dockerfile）只装了 capstone，**没有** pycryptodome / gmpy2 / sympy / pwntools / z3。
于是 runs/plat 26 题里 CRYPTO 0/5、PWN 0/4，收尾结论反复是「本地没有库，只能纯 stdlib」。
平台赛镜像不该动（改镜像要重新出包），但**本地 GUI/CLI 跑**可以外挂一份只在工作区里的库：
  * 只写 tools/pylibs/（git 忽略，二进制不该进 git）；
  * shell() 子进程会把该目录加进 PYTHONPATH（tools_local.shell）；
  * 容器里没有这个目录 → 自动退回「纯 stdlib」，与旧行为一致。

本机为什么不能直接 pip install
-----------------------------
这台 Windows 上的 Python 3.12 会**真的按 POSIX mode 建目录**：`tempfile.mkdtemp()` 用的是
`os.mkdir(..., 0o700)`，建出来的目录连创建者自己都读不了、也 chmod 不回来（WinError 5）。
pip 解包时必须往自己的临时目录里写 `.whl.metadata` → 直接 `Errno 13 Permission denied`
（历史上 runs/ 下那 31 个「打不开」的 pip-* 目录就是这么来的，不是沙箱坏了）。
所以这里：① 把 TEMP/TMP 指到工作区内；② 把 tempfile.mkdtemp 换成 0o777 建目录。

用法
----
    python tools/fetch_pylibs.py                 # 默认装 CRYPTO/PWN 常用集
    python tools/fetch_pylibs.py pycryptodome z3 # 只装指定包
    python tools/fetch_pylibs.py --list          # 看当前已装了什么
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

DEFAULT_PACKAGES = ["pycryptodome", "sympy", "gmpy2", "pwntools"]


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


def main(argv: list[str]) -> int:
    if "--list" in argv:
        rows = installed()
        print("tools/pylibs 已装：" + ("、".join(rows) if rows else "（空）"))
        return 0
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
        print("验证：PYTHONPATH=tools/pylibs python -c \"import Crypto, sympy, gmpy2; print('ok')\"")
    else:
        print(f"\npip 退出码 {code}；可看上面日志定位是哪个包不支持本机 Python 3.12。", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))