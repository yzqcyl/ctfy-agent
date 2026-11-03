#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线统计一个 run_dir 的解题开销与关键事件（不联网、不发 LLM、不花钱）。

用途：量化 checkpoint/rescue/P0-2 等改动在真实 run 里的命中率与成本，给「要不要再优化」提供依据。

    python tools/bench_stats.py [run_dir] [--window 32768]

默认 run_dir = runs/default；window 用于判断「峰值 prompt 是否逼近压缩窗口」（逼近 → 压缩在丢东西）。

注：本工具原先只在 gitignore 的 `runs/` 下，导致干净 clone 跑总入口单测时 `case_bench_stats`
必红（HANDOFF 缺口 #11）。11-05 挪进 `tools/`（入库）后该用例恢复全绿。

每个题目输出一行：状态 / 轮数 / LLM 调用数 / prompt&completion token / 峰值 prompt / 救援次数 /
续跑次数 / 账本重复拦截数 / 被证伪方向数。末尾给跨题聚合。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.ledger import Ledger  # noqa: E402

DEFAULT_RUN_DIR = ROOT / "runs" / "default"
DEFAULT_WINDOW = 32768


def _ledger_from_transcript(records: list[dict]) -> Ledger:
    """按真实转录回放账本，得到重复拦截/证伪方向等验收数字（与 runs/ledger_replay.py 同口径）。"""
    ledger = Ledger.from_config(type("C", (), {"extra": {}})())
    for rec in records:
        name = rec.get("tool")
        args = rec.get("args")
        if not name or args is None:
            continue
        hit = ledger.seen(name, args)
        if hit is not None:
            ledger.note_duplicate(hit.key)
            continue
        ledger.record(name, args, str(rec.get("result") or ""), role=str(rec.get("role") or ""))
    return ledger


def analyze_challenge(cid: str, records: list[dict], state: dict) -> dict:
    calls = 0
    prompt = completion = 0
    peak_prompt = 0
    max_step = 0
    rescue = resume = checkpoint_save_fail = 0

    for rec in records:
        step = rec.get("step")
        if isinstance(step, int):
            max_step = max(max_step, step)

        usage = rec.get("usage")
        if isinstance(usage, dict):
            calls += 1
            pt = int(usage.get("prompt_tokens") or 0)
            ct = int(usage.get("completion_tokens") or 0)
            prompt += pt
            completion += ct
            peak_prompt = max(peak_prompt, pt)

        if step == "rescue":
            rescue += 1
        if rec.get("role") == "checkpoint":
            note = str(rec.get("note") or rec.get("content") or "")
            if "断点续跑" in note:
                resume += 1
            elif "快照失败" in note:
                checkpoint_save_fail += 1

    ledger = _ledger_from_transcript(records)
    lstats = ledger.stats()
    return {
        "challenge_id": cid,
        "status": state.get("status"),
        "reason": state.get("reason"),
        "rounds": max_step,
        "llm_calls": calls,
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "peak_prompt": peak_prompt,
        "rescue": rescue,
        "resume": resume,
        "checkpoint_save_fail": checkpoint_save_fail,
        "duplicates": lstats["duplicates_intercepted"],
        "refuted": lstats["refuted"],
        "records": len(records),
    }


def analyze_run(run_dir: Path) -> list[dict]:
    run_dir = Path(run_dir)
    state = {}
    state_path = run_dir / "state.json"
    if state_path.is_file():
        try:
            state = (json.loads(state_path.read_text(encoding="utf-8")).get("challenges") or {})
        except (OSError, ValueError):
            state = {}

    rows: list[dict] = []
    tdir = run_dir / "transcripts"
    if tdir.is_dir():
        for tf in sorted(tdir.glob("*.jsonl")):
            try:
                records = [
                    json.loads(line)
                    for line in tf.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, ValueError):
                records = []
            rows.append(analyze_challenge(tf.stem, records, state.get(tf.stem, {})))
    return rows


def _fmt(rows: list[dict], window: int) -> str:
    header = (
        f"{'题目':<18} {'状态':<12} {'轮':>3} {'调用':>4} {'prompt':>9} {'compl':>8} "
        f"{'峰值':>8} {'窗口比':>6} {'救援':>4} {'续跑':>4} {'重复':>4} {'证伪':>4}"
    )
    lines = [header, "-" * len(header)]
    for r in rows:
        ratio = f"{r['peak_prompt'] / window:.2f}" if window else "-"
        lines.append(
            f"{r['challenge_id']:<18} {str(r['status'] or '-'):<12} {r['rounds']:>3} "
            f"{r['llm_calls']:>4} {r['prompt_tokens']:>9,} {r['completion_tokens']:>8,} "
            f"{r['peak_prompt']:>8,} {ratio:>6} {r['rescue']:>4} {r['resume']:>4} "
            f"{r['duplicates']:>4} {r['refuted']:>4}"
        )
    return "\n".join(lines)


def _aggregate(rows: list[dict]) -> str:
    if not rows:
        return "（无题目）"
    n = len(rows)
    total_tokens = sum(r["total_tokens"] for r in rows)
    total_calls = sum(r["llm_calls"] for r in rows)
    solved = sum(1 for r in rows if r["status"] == "submitted" or r["reason"] in ("flag_accepted",))
    rescued = sum(r["rescue"] for r in rows)
    resumed = sum(r["resume"] for r in rows)
    dup = sum(r["duplicates"] for r in rows)
    refuted = sum(r["refuted"] for r in rows)
    return (
        f"聚合：{n} 题 | 解出 {solved} | 总调用 {total_calls} | 总 token {total_tokens:,} "
        f"| 救援 {rescued} 次 | 续跑 {resumed} 次 | 重复拦截 {dup} 次 | 证伪方向 {refuted} 个"
    )


def main() -> int:
    args = [a for a in sys.argv[1:]]
    run_dir = Path(args[0]) if args and not args[0].startswith("--") else DEFAULT_RUN_DIR
    window = DEFAULT_WINDOW
    if "--window" in args:
        window = int(args[args.index("--window") + 1])

    rows = analyze_run(run_dir)
    if not rows:
        print(f"{run_dir} 下没有转录文件（transcripts/*.jsonl）")
        return 1

    print(f"run_dir: {run_dir}")
    print(_fmt(rows, window))
    print()
    print(_aggregate(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
