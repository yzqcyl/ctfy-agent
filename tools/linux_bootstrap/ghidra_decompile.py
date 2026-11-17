#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ghidra_decompile：Ghidra headless 反编译包装器（把二进制函数变成可读伪代码）。

解决 2026-11-16 rev 彩排的卡点：checker 被 strip 后只能 objdump/capstone 逐段读，
gdb 在 ASan checker 上插不进断点——用 Ghidra 反编译直接看校验逻辑。

用法::

    ghidra_decompile <binary>                     # 反编译前 40 个函数
    ghidra_decompile <binary> --function verify   # 只反编译名字含 verify 的函数
    ghidra_decompile <binary> --limit 10 --timeout 600

依赖：Ghidra（默认找 /opt/ghidra/INSTALL_DIR.txt 或 GHIDRA_HOME）+ JDK25
（默认 /opt/ghidra/JAVA_HOME25.txt 或 JAVA_HOME）。安装见 ghidra_setup.sh。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile


def _read_first_line(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        return ""


def find_ghidra() -> pathlib.Path:
    env = os.environ.get("GHIDRA_HOME", "").strip()
    if env and pathlib.Path(env).is_dir():
        return pathlib.Path(env)
    marker = pathlib.Path("/opt/ghidra/INSTALL_DIR.txt")
    if marker.is_file():
        path = pathlib.Path(_read_first_line(marker))
        if path.is_dir():
            return path
    for candidate in sorted(pathlib.Path("/opt/ghidra").glob("ghidra_*")):
        if (candidate / "support" / "analyzeHeadless").is_file():
            return candidate
    raise SystemExit(
        "找不到 Ghidra：设置 GHIDRA_HOME，或先跑 tools/linux_bootstrap/ghidra_setup.sh")


def find_java() -> str:
    env = os.environ.get("JAVA_HOME", "").strip()
    if env and (pathlib.Path(env) / "bin" / "java").is_file():
        return env
    marker = pathlib.Path("/opt/ghidra/JAVA_HOME25.txt")
    if marker.is_file():
        candidate = _read_first_line(marker)
        if (pathlib.Path(candidate) / "bin" / "java").is_file():
            return candidate
    raise SystemExit(
        "找不到 JDK25：设置 JAVA_HOME，或先跑 tools/linux_bootstrap/ghidra_setup.sh")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ghidra headless 反编译（输出伪代码）")
    parser.add_argument("binary", help="待反编译的二进制路径")
    parser.add_argument("--function", default="", help="函数名子串过滤（默认全部）")
    parser.add_argument("--limit", type=int, default=40, help="最多输出多少个函数（默认 40）")
    parser.add_argument("--timeout", type=int, default=900, help="分析+反编译总超时秒数")
    args = parser.parse_args(argv)

    binary = pathlib.Path(args.binary).resolve()
    if not binary.is_file():
        print(f"!! 文件不存在: {binary}", file=sys.stderr)
        return 2
    ghidra = find_ghidra()
    java_home = find_java()
    script_dir = pathlib.Path(__file__).resolve().parent
    script = script_dir / "DecompileFunc.java"
    if not script.is_file():
        print(f"!! 缺少 Ghidra 脚本: {script}", file=sys.stderr)
        return 2

    project_dir = pathlib.Path(tempfile.mkdtemp(prefix="ghidra_decomp_"))
    out_file = project_dir / "decompiled.txt"
    env = dict(os.environ)
    env["JAVA_HOME"] = java_home
    env["PATH"] = f"{java_home}/bin:" + env.get("PATH", "")
    analyzer = ghidra / "support" / "analyzeHeadless"
    cmd = [
        str(analyzer), str(project_dir), "decomp_proj",
        "-import", str(binary),
        "-scriptPath", str(script_dir),
        "-postScript", "DecompileFunc.java", args.function, str(args.limit), str(out_file),
        "-analysisTimeoutPerFile", str(max(60, args.timeout // 2)),
        "-deleteProject",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout, env=env)
        out = (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        out = ((exc.stdout or "") if isinstance(exc.stdout, str) else "")
        print(out[-4000:])
        print(f"\n!! 超时（{args.timeout}s）——加大 --timeout 或换小一点的二进制", file=sys.stderr)
        shutil.rmtree(project_dir, ignore_errors=True)
        return 124

    # 优先读脚本直接写的输出文件（Ghidra 分析日志太吵，不解析它）
    text = ""
    try:
        if out_file.is_file():
            text = out_file.read_text(encoding="utf-8", errors="replace")
    finally:
        shutil.rmtree(project_dir, ignore_errors=True)
    if text.strip():
        print(text)
        return 0
    # 兜底：从日志里找脚本输出段
    lines = out.splitlines()
    start = next((i for i, line in enumerate(lines) if "===== " in line), None)
    if start is None:
        print("\n".join(lines[-40:]))
        print("\n!! 没拿到反编译输出（看上面日志：可能是 JDK/Ghidra 路径或文件格式问题）",
              file=sys.stderr)
        return 1
    print("\n".join(lines[start:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())