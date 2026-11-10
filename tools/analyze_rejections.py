# -*- coding: utf-8 -*-
"""复盘「答案被拒」：平台判错时，模型交上去的到底是什么、来自哪里（离线只读）。

动机（HANDOFF-2026-11-10 §8.2 曾有 P1 待办「7 题答案被拒根因＝前缀混用」）：
接盘轮用本脚本复核 2026-11-10 那批 `runs/plat`，结论与当初的猜测不同——
被拒的候选**全部**来自专家结论（`delegate`/`report` 回执），不是运笔幻觉，也不是前缀混用；
平台回执是 `{"accepted": false, "attempt": 1, ...}`（有 submission_id，判错而非拒收格式）。
因此「加前缀闸门」在本批数据上没有依据（前缀与来源 CTF 一致：dice{}/L3AK{}/SUN{}）。

判定口径（每条被拒答案）：
  tool   : 该字面串出现在**真实工具回执**里（`{role:<专家>, tool, result}` 且 tool 不是
           delegate/report/submit/note 这类「模型自己写的」动作）
  report : 只出现在专家/协调者的结论文本里（模型推导或凭记忆写出，没有工具逐字证据）
  none   : transcript 里找不到（例如跨会话/被清理）

用法：
    PYTHONPATH=src python tools/analyze_rejections.py [runs/plat]
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 这些「工具名」其实是模型产出的文本被包成一条回执，不算真实证据
_MODEL_AUTHORED = {"delegate", "report", "report_rejected", "submit", "note", "ledger",
                   "gate", "instance", "coordinator", "budget"}


def _provenance(transcript: pathlib.Path, needle: str) -> dict:
    hits = {"real_tool": 0, "model": 0, "tools": [], "steps": []}
    try:
        handle = transcript.open("r", encoding="utf-8", errors="replace")
    except OSError:
        return hits
    with handle:
        for line in handle:
            line = line.strip()
            if not line or needle not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            # 只认「回执正文」里的字面串：args 里出现（例如模型自己 echo 出来的）不算证据
            result = rec.get("result")
            blob = json.dumps(result, ensure_ascii=False) if result is not None else ""
            if needle not in blob:
                continue
            tool = str(rec.get("tool") or "")
            if tool and tool not in _MODEL_AUTHORED:
                hits["real_tool"] += 1
                if tool not in hits["tools"]:
                    hits["tools"].append(tool)
                hits["steps"].append(rec.get("step"))
            else:
                hits["model"] += 1
    return hits


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    run_dir = pathlib.Path(argv[0]) if argv else ROOT / "runs" / "plat"
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        print(f"没有 {state_path}（先跑一次真实平台批次再复盘）")
        return 2
    state = json.loads(state_path.read_text(encoding="utf-8-sig"))
    challenges = state.get("challenges") or {}

    rows = []
    for cid, ch in challenges.items():
        for answer in (ch.get("rejected_answers") or []):
            prov = _provenance(run_dir / "transcripts" / f"{cid}.jsonl", str(answer))
            if prov["real_tool"]:
                kind = "真实工具回执"
            elif prov["model"]:
                kind = "模型结论"
            else:
                kind = "查无出处"
            rows.append({
                "name": str(ch.get("name") or cid)[:24],
                "category": str(ch.get("category") or "-"),
                "answer": str(answer)[:56],
                "kind": kind,
                "tools": ",".join(prov["tools"][:3]),
                "attempts": ch.get("attempts"),
                "solved": bool(ch.get("submitted_ok") or ch.get("flag_value")),
            })

    if not rows:
        print(f"{run_dir}：没有记录到被拒答案（要么全解出，要么这批没提交过）")
        return 0

    print(f"扫描 {run_dir}：{len(rows)} 条被拒答案，涉及 "
          f"{len({(r['name'], r['category']) for r in rows})} 道题\n")
    print(f"{'题型':<10}{'题目':<26}{'被拒答案':<58}{'来源':<12}{'真实工具'}")
    for r in rows:
        print(f"{r['category']:<10}{r['name']:<26}{r['answer']:<58}{r['kind']:<12}{r['tools']}")
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    print(f"\n来源统计：{counts}")
    print("说明：状态里没有记录平台回执；要看逐条回执请翻 "
          f"{run_dir / 'transcripts'}（`\"accepted\": false` 表示平台判错，不是拒收格式）。")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())