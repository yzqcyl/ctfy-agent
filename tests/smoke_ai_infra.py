#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 基建（AI infrastructure）离线靶子验收。

背景：M7（docs/archive/ai-plan.md §10）把 AI 基建打法注入到 tools_local 指纹 / method_cards /
ai.system 提示词三层，但 §10.4 诚实清单承认「结构洞打法无任何靶子验证过」。本验收
把「指纹定型 → 按 next 打法利用」整条链钉在本地标准库靶子上（无外网、无模型调用）。

与 tests/smoke_ai_fixtures.py（ASI 应用层夹具）的关系：
  - 那边管对话诱导题（ASI01/02/06、陷阱页），目录在 tests/fixtures/ai/；
  - 这边管 AI 基建题（组件指纹 + 结构洞），目录在 tests/fixtures/ai_infra/——**刻意分开**，
    因为 case_ai_fixtures 按目录名解析 ASI 编号，基建靶子混进去会把它打红。

每道靶子 = server.py（靶子形状，纯标准库）+ solve.py（确定性参考解，必须走框架工具层）。
判定项（防自欺）：
  - 目录结构完整（task.md / server.py / solve.py / expected.txt）；
  - 答案是自然语言值（不是 FLAG{...}）：与真实 AI 题判分形态一致；
  - solve.py 不读 expected.txt、必须用 LocalTools、且 **http_map( 与 http_request( 都要出现**
    ——指纹层和利用层缺一不可，只测一边等于没验证 M7 的链路；
  - server.py 只用标准库（镜像里没有第三方包）；
  - 子进程真跑两遍都 exit 0、stdout 含期望值与 `[infra] <component>` 标记（确定性）；
  - 输出里不许带 FLAG{...} 形态的诱饵；
  - 四个靶子必须覆盖四个**不同**组件（指纹规则的覆盖守卫，不许两道题打同一组件）。

并入方式（等并行会话的 tests/smoke.py 改动落定后再接线，本文件先可独立运行）：
  1. smoke.py 顶部 `from smoke_ai_infra import case_ai_infra`；
  2. cases 列表里加 `("ai_infra", case_ai_infra)`。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "ai_infra"
FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,}\{[^}\s]{4,}\}")
# 目录名第二段 = 组件标识（01-ollama-… → ollama），并映射到指纹规则里的组件名
EXPECTED_COMPONENTS = {"ollama": "Ollama", "comfyui": "ComfyUI",
                       "openwebui": "Open WebUI", "litellm": "vLLM/LiteLLM"}


def _ok(label: str) -> None:
    """复用 smoke.py 的 ok()，让本用例计入 PASSED 总数；单独运行时退化成 print。"""
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def case_ai_infra(tmp: Path) -> None:  # noqa: ARG001 - 签名与其它用例统一
    """四道 AI 基建靶子：结构 / 防作弊 / 指纹定型 / 打法链路 / 双跑确定性 / 组件覆盖。"""
    if not FIXTURES.is_dir():
        _ok("AI 基建靶子：目录为空 → 跳过")
        return

    dirs = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    assert len(dirs) >= len(EXPECTED_COMPONENTS), (
        f"AI 基建靶子应至少 {len(EXPECTED_COMPONENTS)} 道（每组件一道），实有 {len(dirs)}"
    )

    checked: list[str] = []
    seen_parts: list[str] = []
    for case_dir in dirs:
        name = case_dir.name
        part = name.split("-")[1] if "-" in name else ""
        assert part in EXPECTED_COMPONENTS, (
            f"{name} 目录名第二段 {part!r} 不在预期组件 {sorted(EXPECTED_COMPONENTS)}"
        )
        assert part not in seen_parts, f"组件 {part} 被两道靶子重复覆盖"
        seen_parts.append(part)

        solve_py = case_dir / "solve.py"
        server_py = case_dir / "server.py"
        task_md = case_dir / "task.md"
        expected_py = case_dir / "expected.txt"
        for required in (solve_py, server_py, task_md, expected_py):
            assert required.is_file(), f"{name} 缺 {required.name}"

        # 1) 答案是自然语言值（真实 AI 题不判 FLAG{...} 形态）
        expected = expected_py.read_text(encoding="utf-8").strip()
        assert expected, f"{name}/expected.txt 为空"
        assert not FLAG_RE.search(expected), f"{name} 用了 FLAG{{...}} 形态，与真实 AI 题判分不符"

        # 2) 防作弊 + 链路完整：不读答案文件；指纹层(http_map)与利用层(http_request)都必须真调
        source = solve_py.read_text(encoding="utf-8")
        assert "expected.txt" not in source, f"{name}/solve.py 读了 expected.txt（等于没验证）"
        assert "LocalTools" in source, f"{name}/solve.py 没用框架工具层"
        assert "http_map(" in source, f"{name}/solve.py 没调 http_map（指纹层没被验证）"
        assert "http_request(" in source, f"{name}/solve.py 没调 http_request（利用层没被验证）"

        # 3) server.py 只用标准库（镜像里没有第三方包）
        server_src = server_py.read_text(encoding="utf-8")
        for banned in ("import requests", "import openai", "from openai", "import httpx",
                       "import litellm", "import flask"):
            assert banned not in server_src, f"{name}/server.py 引了第三方包：{banned}"

        # 4) 子进程真跑两遍：确定性 + 退出码 + 期望值 + 组件标记 + 无 FLAG 诱饵
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
            assert expected in proc.stdout, (
                f"{name} 第 {run} 遍没输出期望值 {expected}：{proc.stdout[-300:]}"
            )
            assert f"[infra] {part}" in proc.stdout, (
                f"{name} 第 {run} 遍组件标记不对（应 [infra] {part}）：{proc.stdout[:200]}"
            )
            assert not FLAG_RE.search(proc.stdout), (
                f"{name} 第 {run} 遍输出了 FLAG{{...}} 诱饵：{proc.stdout[:300]}"
            )
        checked.append(f"{name}→{EXPECTED_COMPONENTS[part]}")

    assert set(seen_parts) == set(EXPECTED_COMPONENTS), (
        f"组件覆盖不全：缺 {sorted(set(EXPECTED_COMPONENTS) - set(seen_parts))}"
    )
    _ok(f"AI 基建靶子：{len(dirs)} 道全绿（指纹定型+打法链路+双跑确定性，组件覆盖：{', '.join(checked)}）")


if __name__ == "__main__":
    import traceback

    try:
        case_ai_infra(ROOT / "tests" / "_tmp_ai_infra")
        print("OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
