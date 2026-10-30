#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP 方向（mcp03 工具描述投毒等）离线靶子验收。

为什么独立于 tests/smoke_ai_fixtures.py：那边的目录名解析规则是
`ASI + part[-2:]`，`mcp03` 会被误标成 `ASI03`（编号体系不同源），所以 MCP 靶子
放 tests/fixtures/ai_mcp/，用本文件验收。防自欺契约与 smoke_ai_fixtures 同源：

  - 目录结构完整（task.md / server.py / solve.py / expected.txt）；
  - 答案是自然语言值（不是 FLAG{...}）；
  - solve.py 不读 expected.txt、必须用 LocalTools、必须调 http_request(，
    且源码里出现 `tools/list`——证明真的枚举了 MCP 工具面，而不是猜路径；
  - server.py 只用标准库；
  - 子进程真跑两遍：exit 0 + stdout 含期望值与 `[mcp] <编号>` 标记 + 无 FLAG 诱饵；
  - 每道题的 MCP 编号（mcpXX）互不重复。

并入方式（等并行会话的 tests/smoke.py 改动落定后再接线，先可独立运行）：
  1. smoke.py 顶部 `from smoke_ai_mcp import case_ai_mcp`；
  2. cases 列表里加 `("ai_mcp", case_ai_mcp)`。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ai_mcp"
FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,}\{[^}\s]{4,}\}")
EXPECTED_IDS = {"mcp03"}


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def case_ai_mcp(tmp: Path) -> None:  # noqa: ARG001 - 签名与其它用例统一
    """MCP 靶子：结构 / 防作弊 / 工具面真枚举 / 双跑确定性 / 编号对齐。"""
    if not FIXTURES.is_dir():
        _ok("MCP 靶子：目录为空 → 跳过")
        return

    dirs = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    assert len(dirs) >= len(EXPECTED_IDS), f"MCP 靶子应至少 {len(EXPECTED_IDS)} 道，实有 {len(dirs)}"

    checked: list[str] = []
    seen: set[str] = set()
    for case_dir in dirs:
        name = case_dir.name
        part = name.split("-")[1] if "-" in name else ""
        assert part in EXPECTED_IDS, f"{name} 编号 {part!r} 不在预期 {sorted(EXPECTED_IDS)}"
        assert part not in seen, f"编号 {part} 重复"
        seen.add(part)

        solve_py = case_dir / "solve.py"
        server_py = case_dir / "server.py"
        task_md = case_dir / "task.md"
        expected_py = case_dir / "expected.txt"
        for required in (solve_py, server_py, task_md, expected_py):
            assert required.is_file(), f"{name} 缺 {required.name}"

        expected = expected_py.read_text(encoding="utf-8").strip()
        assert expected, f"{name}/expected.txt 为空"
        assert not FLAG_RE.search(expected), f"{name} 用了 FLAG{{...}} 形态"

        source = solve_py.read_text(encoding="utf-8")
        assert "expected.txt" not in source, f"{name}/solve.py 读了 expected.txt"
        assert "LocalTools" in source, f"{name}/solve.py 没用框架工具层"
        assert "http_request(" in source, f"{name}/solve.py 没调 http_request"
        assert "tools/list" in source, f"{name}/solve.py 没枚举 MCP 工具面（tools/list）"

        server_src = server_py.read_text(encoding="utf-8")
        for banned in ("import requests", "import openai", "from openai", "import httpx", "import litellm"):
            assert banned not in server_src, f"{name}/server.py 引了第三方包：{banned}"

        for run in (1, 2):
            proc = subprocess.run(
                [sys.executable, "solve.py"],
                cwd=str(case_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            assert proc.returncode == 0, f"{name} 第 {run} 遍退出码 {proc.returncode}：{proc.stderr[-400:]}"
            assert expected in proc.stdout, f"{name} 第 {run} 遍没输出期望值：{proc.stdout[-300:]}"
            assert f"[mcp] {part}" in proc.stdout, f"{name} 第 {run} 遍编号标记不对（应 [mcp] {part}）"
            assert not FLAG_RE.search(proc.stdout), f"{name} 第 {run} 遍输出了 FLAG 诱饵"
        checked.append(name)

    assert seen == EXPECTED_IDS, f"编号覆盖不全：缺 {sorted(EXPECTED_IDS - seen)}"
    _ok(f"MCP 靶子：{len(dirs)} 道全绿（工具面枚举+链路坐实+双跑确定性：{', '.join(checked)}）")


if __name__ == "__main__":
    import traceback

    try:
        case_ai_mcp(ROOT / "tests" / "_tmp_ai_mcp")
        print("OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
