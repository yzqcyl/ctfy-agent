#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：候选闸门 + 假设收敛（v3 P0 纯函数）。

背景（实测三臂）：SAMPLE-009 crew 806k / AGENTBENCH-002 crew 804k / SAMPLE-009 solo 642k
全部 attempts=0、oracle_calls=0、observations=0 —— agent 从不进入「验证/提交」层。
这两条纯函数是确定性收口的判定逻辑；本用例只锁「决策表」，不依赖网络/LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.crew import (  # noqa: E402
    CONVERGENCE_STALL_ROUNDS,
    MAX_DONE_BLOCKS,
    _convergence_stalled,
    _done_gate,
)


def case_done_gate(tmp: Path) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)

    # 零候选：第 0/1 次 done 被拦（stop=False），到上限放行并记 no_candidate
    text, stop, no_cand = _done_gate(False, 0, [], 0)
    assert stop is False and no_cand is False and "候选闸门" in text, (stop, no_cand, text)
    text, stop, no_cand = _done_gate(False, 0, [], MAX_DONE_BLOCKS - 1)
    assert stop is False and no_cand is False, (stop, no_cand)
    text, stop, no_cand = _done_gate(False, 0, [], MAX_DONE_BLOCKS)
    assert stop is True and no_cand is True and "no_candidate" in text, (stop, no_cand, text)

    # 已有提交 / 已有尝试 / 已有逐字证据：正常放行，不拦、不记 no_candidate
    assert _done_gate(True, 0, [], 0) == ("协调结束。", True, False)
    assert _done_gate(False, 1, [], 0) == ("协调结束。", True, False)
    assert _done_gate(False, 0, ["FLAG{x}"], 0) == ("协调结束。", True, False)

    label = "候选闸门：零候选拦 done 至多 2 次；有提交/有尝试/有证据则放行"
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def case_convergence_stalled(tmp: Path) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)

    # 零证据 + 假设只增 + 证伪不变 → 累加
    stalls, opn, ref = _convergence_stalled({"observations": 0, "open": 4, "refuted": 0}, 3, 0, 0)
    assert (stalls, opn, ref) == (1, 4, 0), (stalls, opn, ref)

    # 出现证据 → 清零
    stalls, _, _ = _convergence_stalled({"observations": 1, "open": 5, "refuted": 0}, 4, 0, 2)
    assert stalls == 0, stalls

    # 假设没涨 → 清零
    stalls, _, _ = _convergence_stalled({"observations": 0, "open": 5, "refuted": 0}, 5, 0, 2)
    assert stalls == 0, stalls

    # 有新证伪 → 清零
    stalls, _, _ = _convergence_stalled({"observations": 0, "open": 6, "refuted": 1}, 5, 0, 2)
    assert stalls == 0, stalls

    # 连续 3 轮「只增不驳零证据」→ 达到阈值
    stalls = 0
    for opn in (6, 7, 8):
        stalls, _, _ = _convergence_stalled(
            {"observations": 0, "open": opn, "refuted": 0}, opn - 1, 0, stalls
        )
    assert stalls >= CONVERGENCE_STALL_ROUNDS, stalls

    label = "假设收敛：零证据+只增不驳连续累加；有证据/证伪/不增则清零"
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


if __name__ == "__main__":
    import traceback

    try:
        case_done_gate(Path("."))
        case_convergence_stalled(Path("."))
        print("OK")
    except Exception:
        traceback.print_exc()
