#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_batch.py — 无人值守批量跑平台题（复用 GUI 的生产路径 gui/platform_worker.solve_many）。

为什么单独有这个脚本：GUI 里点「批量跑」的事件流、实例预检、附件落沙盒、按难度配墙钟
都在 `platform_worker` 里；CLI 的 `auto` 只认本地 state、不装平台适配层（DASCTF 上会缺参数）。
这个脚本就是「无 GUI 的 GUI」，事件照原样落盘，方便事后统计思考/浪费。

用法：
    python tools/run_batch.py --manifest runs/batch-easy/manifest.json --run-id batch-easy-2026-11-18
    python tools/run_batch.py --manifest ... --phase easy --limit 1        # 只跑前 1 题
    python tools/run_batch.py --manifest ... --profile ctf2 --concurrency 1

manifest 格式：
    {"ground": {"id": "...", "name": "BUUCTF"},
     "challenges": [{"id": "...", "name": "...", "category": "PWN", "difficulty": "Easy"}, ...]}

产物（都在 runs/<run-id>/ 下）：
    driver.log      人读日志（每题开始/结束/提交/超时）
    events.jsonl    platform_worker 的原始事件流（复盘用）
    transcripts/<challenge_id>.jsonl   Crew 的逐步思考（本来就写这里）
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.gui import platform_worker, profiles  # noqa: E402


def _field(payload, *keys) -> str:
    """事件 payload 字段名不统一（id / challenge_id / name），容错取一个。"""
    if isinstance(payload, dict):
        for k in keys:
            if payload.get(k):
                return str(payload[k])
    return "?"


def _safe_console() -> None:
    """Windows 控制台默认 GBK：事件里有 ▶ / · / 中文标点就会 UnicodeEncodeError 把整批带崩。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - 老重定向对象没有 reconfigure
            pass


def _log(fh, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    fh.write(line + "\n")
    fh.flush()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--profile", default="", help="平台档案名（默认用 platforms.json 的 active）")
    ap.add_argument("--ground", default="", help="覆盖 practice_ground_id / competition_id")
    ap.add_argument("--phase", default="", help="只跑 manifest 里某个 phase（可选）")
    ap.add_argument("--limit", type=int, default=0, help="最多跑几题（0=全部）")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--max-tokens-per-challenge", type=int, default=0, help="0=用 config 默认")
    ap.add_argument("--stop-after-minutes", type=int, default=0, help="整批墙钟上限（0=不限）")
    args = ap.parse_args(argv)
    _safe_console()

    manifest = json.loads((ROOT / args.manifest).read_text(encoding="utf-8-sig"))
    run_id = args.run_id or f"batch-{time.strftime('%m%d-%H%M')}"
    ground = args.ground or str((manifest.get("ground") or {}).get("id") or "")
    rows = list(manifest.get("challenges") or [])
    if args.phase:
        rows = [r for r in rows if str(r.get("phase") or "") == args.phase]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("manifest 里没有要跑的题", file=sys.stderr)
        return 2

    from ctfy_agent.config import load  # noqa: PLC0415

    config = load()
    data = profiles.load()
    prof_name = args.profile or data.get("active") or "ctf2"
    prof = (data.get("profiles") or {}).get(prof_name) or {}
    if not prof:
        print(f"找不到平台档案 {prof_name!r}（候选：{list(data.get('profiles') or {})}）", file=sys.stderr)
        return 2
    profiles.apply(config, prof)  # 内存里换成 ctf2（不动 platforms.json 的 active）
    if args.max_tokens_per_challenge:
        config.max_tokens_per_challenge = args.max_tokens_per_challenge

    out = ROOT / "runs" / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(
        json.dumps({"ground": ground, "profile": prof_name, "challenges": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    q: queue.Queue = queue.Queue()
    cancel = threading.Event()
    stop_after = threading.Event()

    log_path = out / "driver.log"
    ev_path = out / "events.jsonl"
    started = time.time()
    done = threading.Event()

    def driver() -> None:
        try:
            platform_worker.solve_many(
                config, rows, run_id, ground, q, cancel,
                concurrency=args.concurrency, stop_after=stop_after,
                force_ids=frozenset(),
                budget_tokens=None,
            )
        except Exception as exc:  # noqa: BLE001
            q.put(("error", "driver", f"{type(exc).__name__}: {exc}"))
        finally:
            q.put(("done", {"batch": True}))

    threading.Thread(target=driver, name="batch-driver", daemon=True).start()

    with log_path.open("a", encoding="utf-8") as fh, ev_path.open("a", encoding="utf-8") as ev:
        _log(fh, f"批次 {run_id}：{len(rows)} 题，平台 {prof_name} {config.mcp_url}，"
                 f"ground={ground or '(无)'}")
        for r in rows:
            _log(fh, f"  题单: {r.get('category')}/{r.get('difficulty')} "
                     f"{r.get('name')} ({r.get('id')})")
        while not done.is_set() or not q.empty():
            try:
                item = q.get(timeout=0.5)
            except queue.Empty:
                if args.stop_after_minutes and (time.time() - started) > args.stop_after_minutes * 60:
                    if not stop_after.is_set():
                        _log(fh, f"整批超时 {args.stop_after_minutes} 分钟，停止开新题")
                        stop_after.set()
                continue
            ev.write(json.dumps({"t": round(time.time(), 3), "item": list(item)}, ensure_ascii=False) + "\n")
            ev.flush()
            kind = item[0]
            if kind == "task_start":
                p = item[1] if len(item) > 1 else {}
                _log(fh, f">> 开始 {_field(p, 'name', 'challenge_id', 'id')} "
                         f"({p.get('category')}/{p.get('difficulty')})")
            elif kind == "task_done":
                p = item[1] if len(item) > 1 else {}
                _log(fh, f"<< 结束 {_field(p, 'name', 'challenge_id', 'id')} "
                         f"ok={p.get('ok')} flag={str(p.get('flag') or '')[:80]!r} "
                         f"note={str(p.get('note') or '')[:80]}")
            elif kind == "batch_note":
                # payload 可能是 dict（GUI 里）也可能是纯字符串（solve_many 里），两种都要接
                p = item[1] if len(item) > 1 else {}
                note = p.get("note") if isinstance(p, dict) else p
                _log(fh, f"  · {note}")
            elif kind == "error":
                _log(fh, f"  ! 错误 {item[1:]}")
            elif kind == "done":
                break
        _log(fh, f"批次结束，用时 {(time.time() - started) / 60:.1f} 分钟")
    if cancel.is_set():
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())