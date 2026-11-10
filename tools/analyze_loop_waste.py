# -*- coding: utf-8 -*-
"""量化历史 transcript 里「重复被拦动作」烧掉多少调用（离线只读，不连平台、不花钱）。

用途：判断 crew.py 的重复动作断路器（REPEAT_BLOCK_*）到底值不值得存在，以及给
`max_llm_calls` 的取值提供证据（HANDOFF-2026-11-10 §7.1 / §8.1）。

数据形状（实测）：tools/call 结果记录为 `{role:<专家>, tool, args, result}`；
协调者回合记录带 `tool_calls`/`usage`。判定口径与 crew 里的 breaker 完全一致：

* 同一 `(tool, args)` 连续出现、且结果带拦截标记 → 一段「重复块」；
* 第 2 次同类被拦本该摘掉工具、第 4 次本该收口（REPEAT_BLOCK_STREAK / REPEAT_BLOCK_LIMIT），
  所以「可省调用」的下限 = max(0, 块长 - REPEAT_BLOCK_LIMIT)（模型期间换动作会省更多）。

用法：
    PYTHONPATH=src python tools/analyze_loop_waste.py            # 默认扫 runs/plat
    PYTHONPATH=src python tools/analyze_loop_waste.py runs/xxx   # 指定 run 目录
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.crew import (  # noqa: E402
    REPEAT_BLOCK_LIMIT,
    REPEAT_BLOCK_STREAK,
    _looks_blocked,
)


def analyze(run_dir: pathlib.Path) -> list[tuple[str, int, list[int], int]]:
    """返回 [(题名, 调用数, [重复块长度...], 可省调用下限)]。"""
    base = run_dir
    state_path = base / "state.json"
    names: dict[str, str] = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            for cid, ch in (state.get("challenges") or {}).items():
                if isinstance(ch, dict):
                    names[cid] = str(ch.get("name") or cid)
        except (OSError, ValueError):
            pass
    rows = []
    for path in sorted((base / "transcripts").glob("*.jsonl")):
        cid = path.stem
        calls = 0
        last_sig = None
        run_len = 0
        blocks: list[int] = []
        try:
            handle = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if "usage" in rec:
                    calls += 1
                if rec.get("tool") and "args" in rec:
                    sig = (str(rec.get("tool")),
                           json.dumps(rec.get("args") or {}, sort_keys=True, ensure_ascii=False))
                    blocked = _looks_blocked(str(rec.get("result") or ""))
                    if blocked and sig == last_sig:
                        run_len += 1
                    elif blocked:
                        if run_len:
                            blocks.append(run_len)
                        last_sig, run_len = sig, 1
                    else:
                        if run_len:
                            blocks.append(run_len)
                        last_sig, run_len = None, 0
        if run_len:
            blocks.append(run_len)
        saved = sum(max(0, b - REPEAT_BLOCK_LIMIT) for b in blocks if b >= REPEAT_BLOCK_LIMIT)
        if blocks:
            rows.append((names.get(cid, cid[:8]), calls, blocks, saved))
    return sorted(rows, key=lambda r: -r[3])


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    run_dir = pathlib.Path(argv[0]) if argv else ROOT / "runs" / "plat"
    if not run_dir.is_dir():
        print(f"没有这个 run 目录：{run_dir}")
        return 2
    rows = analyze(run_dir)
    if not rows:
        print(f"{run_dir}：没有发现重复被拦动作（要么跑得很健康，要么没有 transcript）")
        return 0
    print(f"扫描 {run_dir}（口径：STREAK={REPEAT_BLOCK_STREAK} 摘工具 / "
          f"LIMIT={REPEAT_BLOCK_LIMIT} 收口）\n")
    print(f"{'challenge':<26} {'calls':>6}  {'重复块长度':<26} {'可省调用下限':>10}")
    total_calls = 0
    total_saved = 0
    for name, calls, blocks, saved in rows:
        total_calls += calls
        total_saved += saved
        print(f"{name[:25]:<26} {calls:>6}  {str(blocks)[:25]:<26} {saved:>10}")
    print(f"\n合计：{len(rows)} 题有重复块，相关题目调用 {total_calls} 次，"
          f"可省调用下限 {total_saved} 次")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())