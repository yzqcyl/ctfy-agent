#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""comp_auto.py — 比赛自动跑：到点抓题 → 按难度矩阵选题 → easy 优先批量解 → 监控 + 统计。

用法（在容器内，repo 挂 /work）：
    # 选题预览（不跑）
    python tools/comp_auto.py plan --competition-id <id>
    # 到点自动跑（会先等到 start-at，再重新抓题，写 manifest 并调 run_batch）
    python tools/comp_auto.py run --competition-id <id> --start-at 2026-11-19T02:00:00Z \
        --concurrency 3 --budget-minutes easy=15,medium=25,hard=40

矩阵（用户 2026-11-18 指定）：
    easy : pwn×2, rev×2
    medium: pwn×2, rev×2, crypto×2, misc×2
    hard : web×4, pwn×2, rev×2, crypto×2, misc×2
执行顺序：easy → medium → hard（同难度内 pwn,rev,crypto,misc,web）。

选题策略：同 (category, difficulty) 内优先未解、有实例/附件；按 solves_count 降序（越多人解出越标准）。
某桶不足时按「同类别相邻难度 → 同难度其它类别」补齐，并在报告里标注缺口。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MATRIX = [
    ("easy", "pwn", 2), ("easy", "rev", 2),
    ("medium", "pwn", 2), ("medium", "rev", 2), ("medium", "crypto", 2), ("medium", "misc", 2),
    ("hard", "web", 4), ("hard", "pwn", 2), ("hard", "rev", 2),
    ("hard", "crypto", 2), ("hard", "misc", 2),
]
ORDER = {"easy": 0, "medium": 1, "hard": 2}
CAT_ORDER = {"pwn": 0, "rev": 1, "crypto": 2, "misc": 3, "web": 4}
DIFF_WALL = {"easy": 15, "medium": 25, "hard": 40}


def _platform():
    from ctfy_agent.config import load  # noqa: PLC0415
    from ctfy_agent.gui import profiles  # noqa: PLC0415
    from ctfy_agent.platform import CtfyPlatform  # noqa: PLC0415

    cfg = load()
    data = profiles.load()
    name = data.get("active") or "CTFY"
    prof = (data.get("profiles") or {}).get(name) or {}
    profiles.apply(cfg, prof)
    p = CtfyPlatform(cfg.mcp_url, cfg.token, timeout=60, connect_timeout=25,
                     tool_map=cfg.extra.get("tool_map") or {})
    p.connect()
    return p


def _unwrap(res):
    if isinstance(res, dict):
        if "structuredContent" in res:
            return res["structuredContent"]
        for it in res.get("content") or []:
            t = it.get("text") if isinstance(it, dict) else None
            if t:
                try:
                    return json.loads(t)
                except ValueError:
                    continue
    return res


def fetch_challenges(platform, competition_id: str) -> list[dict]:
    data = _unwrap(platform.call("list_competition_challenges",
                                 {"competition_id": competition_id, "limit": 200}))
    items = data.get("result") if isinstance(data, dict) else data
    items = items or []
    rows = []
    for c in items:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        rows.append({
            "id": str(c["id"]),
            "name": str(c.get("name") or c["id"]),
            "category": str(c.get("category") or "").lower(),
            "difficulty": str(c.get("difficulty") or "").lower(),
            "solves": int(c.get("solves_count") or 0),
            "solved": bool(c.get("my_fully_solved")),
            "attachments": [a.get("name") for a in (c.get("attachments") or [])],
            "q_n": len(c.get("questions") or []),
            "qa_only": bool(c.get("is_qa_only")),
        })
    return rows


def build_plan(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """按矩阵选题；返回 (选中列表, 缺口说明)。"""
    picked: list[dict] = []
    used: set[str] = set()
    gaps: list[str] = []

    def pick(cat: str, diff: str, n: int) -> list[dict]:
        cands = [r for r in rows if r["category"] == cat and r["difficulty"] == diff
                 and r["id"] not in used]
        # 未解优先 → 通过率高的（solves 降序）→ 有附件/多题号优先
        cands.sort(key=lambda r: (r["solved"], -r["solves"], -len(r["attachments"]), -r["q_n"]))
        out = cands[:n]
        for r in out:
            used.add(r["id"])
        return out

    for diff, cat, n in MATRIX:
        got = pick(cat, diff, n)
        if len(got) < n:
            gaps.append(f"{diff}/{cat}: {len(got)}/{n}")
            # 补齐：同类别相邻难度
            for alt in ("easy", "medium", "hard"):
                if len(got) >= n:
                    break
                if alt == diff:
                    continue
                extra = pick(cat, alt, n - len(got))
                got += extra
            # 再补：同难度其它类别
            if len(got) < n:
                for alt_cat in ("pwn", "rev", "crypto", "misc", "web"):
                    if len(got) >= n:
                        break
                    if alt_cat == cat:
                        continue
                    got += pick(alt_cat, diff, n - len(got))
        picked += got

    picked.sort(key=lambda r: (ORDER.get(r["difficulty"], 9), CAT_ORDER.get(r["category"], 9), -r["solves"]))
    return picked, gaps


def write_manifest(picked: list[dict], competition_id: str, out: Path) -> Path:
    m = {"ground": {"id": competition_id, "name": "TPCTF VPS"}, "phase": "competition",
         "challenges": [{"id": r["id"], "name": r["name"], "category": r["category"],
                         "difficulty": r["difficulty"], "has_attachments": bool(r["attachments"]),
                         "solve_count": r["solves"]} for r in picked]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def monitor(run_dir: Path, log: Path, stop_when) -> None:
    """每 30s 记录一次进度（题目数/已解/最近转录大小），直到 stop_when() 为真。"""
    st_path = run_dir / "state.json"
    with log.open("a", encoding="utf-8") as fh:
        while not stop_when():
            try:
                solved = 0
                total = 0
                if st_path.exists():
                    st = json.loads(st_path.read_text(encoding="utf-8-sig"))
                    for ch in (st.get("challenges") or {}).values():
                        total += 1
                        if ch.get("submitted_ok") or ch.get("flag_value"):
                            solved += 1
                trans = list((run_dir / "transcripts").glob("*.jsonl")) if (run_dir / "transcripts").is_dir() else []
                recent = max((p.stat().st_mtime for p in trans), default=0)
                line = (f"[{time.strftime('%H:%M:%S')}] 题={total} 解出={solved} "
                        f"转录={len(trans)} 最近活动={time.strftime('%H:%M:%S', time.localtime(recent)) if recent else '-'}")
                print(line, flush=True)
                fh.write(line + "\n")
                fh.flush()
            except Exception as exc:  # noqa: BLE001
                fh.write(f"[monitor] {type(exc).__name__}: {exc}\n")
                fh.flush()
            time.sleep(30)


def cmd_plan(args) -> int:
    platform = _platform()
    rows = fetch_challenges(platform, args.competition_id)
    print(f"抓到 {len(rows)} 题")
    for r in rows:
        print(f"  {r['difficulty']:<7}{r['category']:<8}solved={str(r['solved']):<5}"
              f"solves={r['solves']:<5}{r['id']:<20}{r['name'][:36]}")
    picked, gaps = build_plan(rows)
    print(f"\n选中 {len(picked)} 题：")
    for r in picked:
        print(f"  {r['difficulty']:<7}{r['category']:<8}{r['id']:<20}{r['name'][:40]}")
    if gaps:
        print("缺口：", "；".join(gaps))
    out = write_manifest(picked, args.competition_id, ROOT / "runs" / "comp-plan" / "manifest.json")
    print("manifest ->", out)
    return 0


def cmd_run(args) -> int:
    # 1) 等到开赛（留 30s 余量）
    if args.start_at:
        from datetime import datetime, timezone  # noqa: PLC0415
        target = datetime.fromisoformat(args.start_at.replace("Z", "+00:00")).timestamp()
        while time.time() < target + 30:
            left = target - time.time()
            print(f"[wait] 距开赛 {left/60:.1f} 分钟", flush=True)
            time.sleep(min(120, max(5, left)))
    # 2) 重新抓题（题目开盘才公布）
    platform = _platform()
    rows = []
    for attempt in range(10):
        rows = fetch_challenges(platform, args.competition_id)
        if rows:
            break
        print(f"[wait] 题目还没放出（第 {attempt+1} 次）", flush=True)
        time.sleep(30)
    if not rows:
        print("[fatal] 开盘 5 分钟仍无题目，退出", file=sys.stderr)
        return 2
    picked, gaps = build_plan(rows)
    run_id = args.run_id or f"comp-{time.strftime('%m%d-%H%M')}"
    man = write_manifest(picked, args.competition_id, ROOT / "runs" / run_id / "manifest.json")
    print(f"[plan] {len(picked)} 题 → {man}")
    if gaps:
        print("[plan] 缺口：", "；".join(gaps))
    # 3) 起 run_batch（并发 ≤3；平台每队最多 3 实例）
    run_dir = ROOT / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-u", "tools/run_batch.py", "--manifest", str(man.relative_to(ROOT)),
           "--run-id", run_id, "--profile", args.profile, "--concurrency", str(args.concurrency)]
    if args.dry_run:
        print("[dry-run] 不启动 run_batch；plan 已写入 manifest：", man, flush=True)
        return 0
    print("[run]", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, cwd=str(ROOT))
    monitor(run_dir, run_dir / "monitor.log", lambda: proc.poll() is not None)
    rc = proc.wait()
    # 4) 统计
    try:
        subprocess.run([sys.executable, "-u", "tools/analyze_batch.py", f"runs/{run_id}"], cwd=str(ROOT))
    except Exception as exc:  # noqa: BLE001
        print("[analyze] 失败:", exc)
    print(f"[done] rc={rc} run_id={run_id}")
    return rc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("plan", "run"):
        p = sub.add_parser(name)
        p.add_argument("--competition-id", required=True)
        p.add_argument("--concurrency", type=int, default=3)
        p.add_argument("--profile", default="CTFY")
        if name == "run":
            p.add_argument("--start-at", default="")
            p.add_argument("--run-id", default="")
            p.add_argument("--budget-minutes", default="")
            p.add_argument("--dry-run", action="store_true", help="只出计划，不启动批处理")
    args = ap.parse_args(argv)
    return cmd_plan(args) if args.mode == "plan" else cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())