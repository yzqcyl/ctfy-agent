#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_ps1.py — 仓库里 .ps1 / .cmd 的编码与换行契约（Windows 专用陷阱的守卫）。

背景（2026-11-10 接手时踩到）
---------------------------
本机 `powershell` 是 Windows PowerShell **5.1**：它按 ANSI（简中系统 = GBK）读 **无 BOM** 的
.ps1 文件。Gui 的 OCR 脚本里本来就有中文注释，某次编辑把 UTF-8 BOM 弄丢了 → 5.1 把注释按
GBK 解码成乱码 → 语法直接报错（"`,` 后面缺少表达式" / "Try 语句缺少自己的 Catch"），
而 Python 侧只能看到一句「OCR 没有产出结果」，排查成本极高。

另一条：`.gitattributes` 里已把 `*.ps1` / `*.cmd` 固定成 CRLF（LF 检出会让 .cmd 的
`&&` 链接换行错位、`ModuleNotFoundError` 之类的假故障）。

这个测试把这些隐性契约钉住：所有 .ps1 必须是 UTF-8 **带 BOM**；.ps1/.cmd 必须是 CRLF。

运行: PYTHONPATH=src python tests/smoke_ps1.py
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOM = b"\xef\xbb\xbf"
SKIP_DIRS = {"runs", ".git", "tools/pylibs", "__pycache__", "node_modules", ".venv"}

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def _walk(suffix: str) -> list[pathlib.Path]:
    out = []
    for path in ROOT.rglob(f"*{suffix}"):
        rel = path.relative_to(ROOT).as_posix()
        if any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        if path.is_file():
            out.append(path)
    return sorted(out)


def main() -> int:
    scripts = _walk(".ps1")
    cmds = _walk(".cmd")
    check("仓库里有 .ps1 需要校验", bool(scripts), f"找到 {len(scripts)} 个")
    check("仓库里有 .cmd 需要校验", bool(cmds), f"找到 {len(cmds)} 个")

    for path in scripts:
        raw = path.read_bytes()
        rel = path.relative_to(ROOT).as_posix()
        check(f"UTF-8 BOM：{rel}", raw.startswith(BOM),
              "缺 BOM → Windows PowerShell 5.1 会按 GBK 读，中文注释直接语法错")
        check(f"无裸 LF：{rel}", b"\n" not in raw.replace(b"\r\n", b""),
              "存在裸 LF，需 CRLF")

    for path in cmds:
        raw = path.read_bytes()
        rel = path.relative_to(ROOT).as_posix()
        check(f"无裸 LF：{rel}", b"\n" not in raw.replace(b"\r\n", b""), "存在裸 LF，需 CRLF")

    # 本机有 powershell 时再让 PS 5.1 实际 parse 一遍（BOM/语法双保险）
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps and scripts:
        runner = ROOT / "runs" / f"_t_ps1_parse_{__import__('os').getpid()}.ps1"
        runner.parent.mkdir(parents=True, exist_ok=True)
        lines = ["$ErrorActionPreference = 'Stop'", "$bad = 0"]
        for path in scripts:
            lines.append(f"$errs = $null; $tok = $null")
            lines.append(f"[System.Management.Automation.Language.Parser]::ParseFile("
                         f"'{path}', [ref]$tok, [ref]$errs) | Out-Null")
            lines.append(f"if ($errs -and $errs.Count) {{ Write-Host 'PARSE-FAIL {path}'; "
                         f"$errs | ForEach-Object {{ Write-Host ('  line ' + $_.Extent.StartLineNumber + "
                         f"': ' + $_.Message) }}; $bad = 1 }}")
        lines.append("if ($bad -eq 0) { Write-Host 'PS-PARSE-OK' } else { exit 1 }")
        runner.write_text(("\r\n".join(lines) + "\r\n"), encoding="utf-8-sig")
        try:
            proc = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  timeout=120)
            out = (proc.stdout or "") + (proc.stderr or "")
            check("PowerShell 实际 parse 通过", "PS-PARSE-OK" in out and proc.returncode == 0, out[-600:])
        finally:
            runner.unlink(missing_ok=True)
    else:
        print("[skip] 本机没有 powershell，跳过实际 parse")

    print()
    if FAILS:
        print(f"smoke_ps1 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("smoke_ps1 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())