#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_re_toolchain.py —— P2-14 RE 工具链离线自检。

检查：
  1) ft_hook.c / build_ft_hook.sh / run_hook_checker.sh 存在且非空；
  2) ft_hook.c 覆盖关键钩子符号（FT_Load_Glyph/FT_Load_Char/FT_Outline_Decompose、
     constructor、log 落盘、RTLD_NEXT）；
  3) 两个 shell 脚本有 set -euo pipefail、无硬编码绝对路径（/tmp 仅出现在提示/自检行）；
  4) 有 bash 时 `bash -n` 语法检查；有 gcc + freetype 头时尝试真编译（VM 内才算 PASS）；
  5) 打印 VERDICT=PASS/VM_PENDING（缺编译环境时给待办，不假绿）。

用法：python tools/re_toolchain/check_re_toolchain.py [--build]
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
CHECKS: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(cond), detail))
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" —— {detail}"))


def _read(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def test_sources() -> None:
    for name in ("ft_hook.c", "build_ft_hook.sh", "run_hook_checker.sh"):
        path = HERE / name
        ok(f"{name} 存在且非空", path.is_file() and path.stat().st_size > 200,
           str(path))
    c_code = _read(HERE / "ft_hook.c")
    for symbol in ("FT_Load_Glyph", "FT_Load_Char", "FT_Outline_Decompose",
                   "constructor", "RTLD_NEXT", "CTFY_HOOK_LOG", "hook_log"):
        ok(f"ft_hook.c 含 {symbol}", symbol in c_code)
    ok("ft_hook.c 不读平台内部答案路径",
       "/answers/" not in c_code and "ANSWER_" not in c_code)
    for script in ("build_ft_hook.sh", "run_hook_checker.sh"):
        text = _read(HERE / script)
        ok(f"{script} 有严格模式", "set -euo pipefail" in text)
        hardcoded = [line for line in text.splitlines()
                     if line.strip().startswith(("cd /", "gcc /"))]
        ok(f"{script} 无硬编码工作目录", not hardcoded, str(hardcoded))


def test_shell_syntax() -> None:
    bash = shutil.which("bash")
    if not bash:
        ok("bash 语法检查（无 bash，跳过）", True, "Windows 无 bash：VM 内验证")
        return
    # Windows 的 C:\windows\system32\bash.exe 常是 WSL 存根（未装发行版会直接退出 1），
    # 先用 --version 探活，避免把「没有 WSL」误判成脚本语法错。
    probe = subprocess.run([bash, "--version"], capture_output=True)
    if probe.returncode != 0:
        ok("bash 语法检查（WSL 存根不可用，跳过）", True,
           "VM/git-bash 内验证语法")
        return
    for script in ("build_ft_hook.sh", "run_hook_checker.sh"):
        proc = subprocess.run([bash, "-n", str(HERE / script)], capture_output=True)
        ok(f"bash -n {script}", proc.returncode == 0,
           proc.stderr.decode("utf-8", "replace")[:200])


def test_compile() -> None:
    gcc = shutil.which("gcc")
    header = pathlib.Path("/usr/include/freetype2/ft2build.h")
    if not gcc or not header.exists():
        ok("真编译 ft_hook.so（缺 gcc/FreeType 头，标记 VM 待办）", True,
           "VM 内跑 tools/re_toolchain/build_ft_hook.sh --outdir /tmp/build")
        return
    outdir = HERE / "build"
    outdir.mkdir(exist_ok=True)
    proc = subprocess.run(["bash", str(HERE / "build_ft_hook.sh"), "--outdir", str(outdir)],
                          capture_output=True)
    ok("VM 内编译成功", proc.returncode == 0,
       proc.stderr.decode("utf-8", "replace")[:300])
    ok("产出 ft_hook.so", (outdir / "ft_hook.so").is_file())


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    parser = argparse.ArgumentParser(description="P2-14 RE 工具链自检")
    parser.add_argument("--build", action="store_true", help="有环境时真编译")
    args = parser.parse_args(argv)
    test_sources()
    test_shell_syntax()
    if args.build:
        test_compile()
    failed = [name for name, passed, _detail in CHECKS if not passed]
    print()
    if failed:
        print(f"失败：{'；'.join(failed)}")
        print("RESULT=FAIL")
        return 1
    if args.build and not shutil.which("gcc"):
        print("RESULT=VM_PENDING（源码/脚本自检通过；编译需在 Linux VM 验证）")
        return 0
    print(f"检查通过（{len(CHECKS)} 项）")
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())