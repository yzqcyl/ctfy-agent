#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 验收：观测去重 / 同目标重复刹车 / 预算阶段线（src/ctfy_agent/progress.py）。

依据 bank-500k-v1 实测（docs/archive/report-large-scale-test.md §10）：
  未解出题 49 次单点请求、同目标 12-13 次重复、预算≈40 次调用无感知。
判定项：
  - 首次观测全文保留、is_new=True；相同 digest 第二次起替换为省略标记（省上下文）；
  - 时变字段（elapsed_ms 等）不破坏重复判定；
  - 同一 (tool,target) 第 4 次注入换法提示、第 8 次再提示一次，第 5-7 次不刷屏；
  - 不同目标互不干扰计数；
  - BudgetPhase：无上限返回 None；20% 探索 / 55% 收敛 / 80% 收口；含剩余调用估算；
  - solver/crew 已接线（run_role 与 _solve_once 用 ObservationTracker，协调者注入预算行）。

并入方式（等并行会话的 tests/smoke.py 改动落定后接线，先可独立运行）：
  smoke.py 顶部 `from smoke_progress import case_progress`；cases 加 `("progress", case_progress)`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.progress import BudgetPhase, ObservationTracker, primary_target  # noqa: E402


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def case_progress(tmp: Path) -> None:  # noqa: ARG001
    # 1) 首次观测：全文保留 + is_new
    t = ObservationTracker()
    body = "HTTP/1.1 200 OK\n\n" + "A" * 3000
    display, is_new, note = t.observe("http_request", {"url": "http://t/api/x"}, body)
    assert is_new and display == body and note is None, (is_new, display[:50], note)

    # 2) 相同 digest：替换为省略标记（保留头部、报省略量），且不再算新
    display2, is_new2, _ = t.observe("http_request", {"url": "http://t/api/x"}, body)
    assert not is_new2 and "重复观察" in display2 and "已省略" in display2, display2[:120]
    assert "A" * 300 not in display2, "重复观测不应再全文注入"
    assert t.repeats_shrunk == 1

    # 3) 时变字段不破坏判定（elapsed_ms 不同仍算重复）
    j1 = '{"status":"error","msg":"denied","elapsed_ms":123}'
    j2 = '{"status":"error","msg":"denied","elapsed_ms":456}'
    d1, n1, _ = t.observe("http_request", {"url": "http://t/api/y"}, j1)
    d2, n2, _ = t.observe("http_request", {"url": "http://t/api/y"}, j2)
    assert n1 and not n2 and "重复观察" in d2, (n1, n2, d2[:80])

    # 4) 同目标第 4 次注入换法提示，第 5-7 次不刷屏，第 8 次再提示
    url = "http://t/js/main.js"
    notes_at = []
    for i in range(1, 9):
        _, _, note_i = t.observe("http_request", {"url": url}, f"variant-{i}-output")
        if note_i:
            notes_at.append(i)
            assert "第 4" not in note_i or i == 4  # 提示里带当前次数
    assert notes_at == [4, 8], f"提示应在第 4、8 次出现，实得 {notes_at}"

    # 5) 不同目标互不干扰：换 URL 后计数从零开始
    _, _, note_other = t.observe("http_request", {"url": "http://t/other.js"}, "fresh-output")
    assert note_other is None

    # 6) primary_target：URL 优先；无已知键退化到参数摘要
    assert primary_target("http_request", {"url": "http://t/a?b=1", "method": "GET"}) == "http://t/a?b=1"
    assert primary_target("chat", {"message": "hello world"}) == "hello world"

    # 7) BudgetPhase：无上限 → None；三档阶段 + 剩余调用估算
    class _B:
        def __init__(self, used, calls, cap):
            self.total_tokens, self.calls, self.max_tokens = used, calls, cap
    assert BudgetPhase.line(None) is None
    assert BudgetPhase.line(_B(0, 0, 0)) is None
    line = BudgetPhase.line(_B(100_000, 8, 500_000))
    assert "探索期" in line and "20%" in line and "约剩" in line, line
    line = BudgetPhase.line(_B(275_000, 22, 500_000))
    assert "收敛期" in line and "55%" in line, line
    line = BudgetPhase.line(_B(400_000, 32, 500_000))
    assert "收口期" in line and "80%" in line and "禁止开辟新攻击面" in line, line

    # 8) 接线仍在（防回退）：solver 两条路径 + crew 协调者都应引用 progress
    solver_src = (ROOT / "src" / "ctfy_agent" / "solver.py").read_text(encoding="utf-8")
    crew_src = (ROOT / "src" / "ctfy_agent" / "crew.py").read_text(encoding="utf-8")
    assert solver_src.count("obs.observe(") >= 2, "solver 应有 ≥2 处观测接线（run_role + _solve_once）"
    assert solver_src.count("BudgetPhase.line(") >= 2, "solver 两条路径都应注入预算行"
    assert "BudgetPhase.line(" in crew_src, "crew 协调者每轮应注入预算行"
    assert "seen_outputs" not in solver_src, "旧的 seen_outputs 机制应已被 ObservationTracker 取代"

    _ok("P0 进度：观测去重 + 同目标重复刹车 + 预算阶段线（单元 + 接线守卫）")


if __name__ == "__main__":
    import traceback

    try:
        case_progress(ROOT / "tests" / "_tmp_progress")
        print("OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
