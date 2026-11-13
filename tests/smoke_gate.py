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
    _needs_flag_wrapper,
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

    # [fix 2026-11-13 P6] 候选被平台判错（refuted）后不再逼提交，改逼「换证据」；
    # 未判错的候选仍走候选闸门（逼先 verify/submit）。
    text, stop, no_cand = _done_gate(
        False, 1, ["FLAG{o}"], 0, calls_left=200, tokens_left=300_000,
        candidates=["flag{A}"], refuted=["flag{A}"],
    )
    assert stop is False and no_cand is False and "判错" in text, (stop, no_cand, text)
    text, stop, no_cand = _done_gate(
        False, 1, [], 0, calls_left=200, tokens_left=300_000,
        candidates=["flag{B}"], refuted=["flag{A}"],
    )
    assert stop is False and "候选 flag" in text, (stop, text)

    # [fix 2026-11-13 P8] 零候选但预算充足：上限后仍要继续拦（最多 2×max_blocks），压住早停。
    text, stop, _ = _done_gate(False, 0, [], MAX_DONE_BLOCKS, calls_left=200, tokens_left=300_000)
    assert stop is False and "预算闸门" in text, (stop, text)
    text, stop, no_cand = _done_gate(
        False, 0, [], 2 * MAX_DONE_BLOCKS, calls_left=200, tokens_left=300_000
    )
    assert stop is True and no_cand is True, (stop, no_cand)

    # [fix 2026-11-13 P7] 裸 token 一次性补 flag{} 的判定：只认机器 token，不认普通单词。
    assert _needs_flag_wrapper("2f1d580c50468481f3082c6b5bfd869d") is True
    assert _needs_flag_wrapper("AAAAAAAAAAAAAAAAAAAA") is True  # base64/大写 hex 形态
    assert _needs_flag_wrapper("guess_one") is False
    assert _needs_flag_wrapper("flag{abc}") is False
    assert _needs_flag_wrapper("short") is False

    # [fix 2026-11-13 P9] 占位/诱饵候选过滤：不再把假 flag 当真候选逼提交。
    from ctfy_agent.crew import _is_placeholder_candidate  # noqa: PLC0415
    for decoy in ("flag{AAAAAAAAAAAAAAAA}", "DASCTF{fakeFAKEfAkeFakeflag}",
                  "FLAG{you_may_need_this}", "SCTF{fake...}", "flag{test_test_test}"):
        assert _is_placeholder_candidate(decoy) is True, decoy
    for real in ("flag{office_easy_cracked}", "DASCTF{Hello_glbONaSci!}",
                 "flag{mrctf_vigenere_crypto_crack_man}", "CTF2{9f3a1c}"):
        assert _is_placeholder_candidate(real) is False, real

    label = "候选闸门：零候选拦 done 至多 2 次；判错候选改逼换证据；裸 token 补包裹判定；占位候选过滤"
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
