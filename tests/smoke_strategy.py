#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_strategy.py — 2026-11-17 反思后加的执行纪律（实验优先/分析桶/事实可复现）。

覆盖：
  1) Solver._classify_call：shell 命令分类（静态阅读 vs 实验 vs 其他）、平台工具分类；
  2) Solver._role_context：沙盒清单/状态尾部注入 + "执行优先"硬规则；
  3) Crew._brief：执行纪律块（实验优先/事实可复现/搜索委托/阶段门禁）。
"""
from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import Challenge  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def test_classify() -> None:
    shell = lambda cmd: Solver._classify_call("shell", json.dumps({"command": cmd}))  # noqa: E731
    check("ls/cat/grep/objdump=read",
          shell("ls -la") == "read" and shell("cat main.c") == "read"
          and shell("grep -n flag main.c") == "read"
          and shell("objdump -d challenge") == "read")
    check("python3/gdb/nc/./bin=experiment",
          shell("python3 exploit.py") == "experiment"
          and shell("gdb -batch -q ./checker") == "experiment"
          and shell("timeout 60 python3 search.py") == "experiment"
          and shell("./checker 0011") == "experiment"
          and shell("nc 127.0.0.1 1234") == "experiment")
    check("mkdir/echo=other", shell("mkdir -p out") == "other" and shell("echo hi") == "other")
    check("平台工具分类",
          Solver._classify_call("tcp", "{}") == "experiment"
          and Solver._classify_call("elfpeek", "{}") == "read"
          and Solver._classify_call("write_file", "{}") == "other")
    check("坏 JSON 不炸", Solver._classify_call("shell", "{not json") == "other")


def test_role_context() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        (root / "challenge").write_bytes(b"\x7fELF")
        (root / "main.c").write_text("int main(){}", encoding="utf-8")
        (root / "B_STATE.md").write_text("已确认：点数为 68；下一步跑批量搜索脚本。\n" * 3,
                                         encoding="utf-8")
        challenge = Challenge(id="REV-01", name="demo", category="reverse", points=100,
                              difficulty="medium", tags=["reverse", "stripped"])
        ctx = Solver._role_context(challenge, sandbox=str(root))
    check("上下文含沙盒清单", "沙盒现状" in ctx and "challenge" in ctx and "main.c" in ctx, ctx[-300:])
    check("上下文含状态文件尾部", "B_STATE.md 尾部" in ctx and "批量搜索脚本" in ctx)
    check("上下文含执行优先规则", "执行优先" in ctx and "不要连续两次 grep" in ctx)


def test_crew_brief() -> None:
    from ctfy_agent.crew import Crew  # noqa: PLC0415

    crew = Crew.__new__(Crew)
    crew.roles = {"pwn": {}, "rev": {}}
    crew._access_lines = []
    crew._question_info = {}
    challenge = Challenge(id="PWN-01", name="demo", category="pwn", points=100,
                          difficulty="hard", tags=["heap"], description="demo 题面")
    text = crew._brief(challenge)
    for kw in ("执行纪律", "实验优先", "事实可复现", "搜索委托", "阶段门禁"):
        check(f"brief 含 {kw}", kw in text)


def main() -> int:
    test_classify()
    test_role_context()
    test_crew_brief()
    if FAILS:
        print(f"\nSTRATEGY smoke FAIL：{FAILS}")
        return 1
    print("\nSTRATEGY smoke OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())