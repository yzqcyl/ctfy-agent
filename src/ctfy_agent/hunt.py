#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线解题引擎：`python -m ctfy_agent.hunt ...`（无界面）与 GUI 共用同一套后端。

不接平台接口：靶场由人工启动、flag 由人工提交；agent 只负责攻击并输出 flag 候选。
纯增量文件，不 import 时无副作用，现有 solve/crew/auto/agent 行为零改动。

公开接口：
  run_hunt(config, *, urls, category, difficulty, name, brief, run_id,
           on_event, on_answer, cancel_event) -> dict       # 新建并跑
  resume_run(config, *, run_id, on_event, on_answer, cancel_event) -> dict  # 续跑历史任务
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .cli import make_llm, normalize_challenge
from .config import load
from .offline import OfflinePlatform
from .retry import extract_candidates
from .state import Store

_MARKER = ".offline"


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", str(s or "")).strip("-").lower()
    return s or "target"


def _normalize_url(u: str) -> str:
    u = str(u).strip()
    if u and "://" not in u:
        u = "http://" + u
    return u


def _seed_run(config, *, urls, category, difficulty, name, brief, run_id) -> str:
    """在 run store 里播种一条离线挑战，返回 challenge_id。写 .offline 标记供历史列表识别。"""
    desc_lines = ["【离线靶场（人工已启动，目标如下，直接攻击，不要调用 start_instance）】"]
    desc_lines += [f"- {u}" for u in urls]
    if brief:
        desc_lines += ["", brief]
    desc = "\n".join(desc_lines)

    cid = "HUNT-" + _slug(name or urls[0].split("://")[-1].split("/")[0])
    store = Store(config.run_dir(run_id)).load()
    store.upsert(normalize_challenge({
        "id": cid, "name": name, "category": category,
        "points": 0, "description": desc, "difficulty": difficulty,
        "tags": ["offline"],
    }))
    item = store.require(cid)
    # allow_many 会递归解析 url 字符串 → 登记白名单（tools_local.allow_many/_allow_url）
    item.instance = {"offline_targets": urls}
    item.instance_id = ""
    store.save()

    marker = Path(config.run_dir(run_id)) / _MARKER
    marker.write_text(json.dumps({"run_id": run_id, "challenge_id": cid, "name": name}, ensure_ascii=False),
                      encoding="utf-8")
    return cid


def _write_flag_file(config, run_id: str, candidates: list[str]) -> None:
    out = Path(config.run_dir(run_id)) / "flag.txt"
    out.write_text("\n".join(candidates) if candidates else "(未发现候选)\n", encoding="utf-8")


def _rank_candidates(flag_value: str, answers: list[str], txts: list[str]) -> list[str]:
    """候选排序 + 去噪：已记录通过的值最优先；转录里的花括号候选次之。

    M1 实测：extract_candidates 会把模型自己复述的 "nctfy{...}"、"flag{...}" 也当候选。
    - 命中值优先且已去重；
    - 转录候选若「包含」某个命中值（如 nctfy{...} 包含 ctfy{...}）→ 视为噪声，丢弃；
    - 花括号内为纯省略号/太短的 → 丢弃。
    """
    primary: list[str] = []
    for v in [flag_value] + list(answers):
        v = str(v).strip()
        if v and v not in primary:
            primary.append(v)

    others: list[str] = []
    for t in txts or []:
        for c in extract_candidates([t], limit=12):
            c = str(c).strip()
            if not c or c in primary or c in others:
                continue
            if any(p in c for p in primary):  # 含命中值的更长/错拼版本 → 噪声
                continue
            body = c[c.find("{") + 1: c.rfind("}")]
            if len(body) < 6 or set(body) <= {".", "-", "_"}:
                continue
            others.append(c)

    return primary + others


def _run_crew(config, store: Store, cid: str, *, on_event, on_answer, cancel_event) -> dict:
    from .crew import Crew  # noqa: PLC0415

    platform = OfflinePlatform(record_answer=on_answer)
    crew = Crew(config, platform, store,
                lambda model=None: make_llm(config, model=model), on_event=on_event)
    if cancel_event is not None:
        # 单任务 + 可停止：crew.solve 与 solve_clusterb(k=1) 逐字等价（本配置无集群参数），
        # 且 solve 现有 cancel_event 轮边界让出（crew.py:995），零改动接入。
        summary = crew.solve(cid, cancel_event=cancel_event)
    else:
        summary = crew.solve_clusterb(cid)

    ch = store.get(cid)
    flag_value = getattr(ch, "flag_value", "") if ch else ""
    answers = [str(v) for v in (getattr(ch, "answers_by_qid", {}) or {}).values()] if ch else []

    txts = list(answers)
    if ch:
        txts.extend(getattr(ch, "notes", []) or [])
        tpath = store.transcript_path(cid)
        if tpath is not None and tpath.exists():
            txts.append(tpath.read_text(encoding="utf-8"))

    candidates = _rank_candidates(flag_value, answers, txts)

    return {
        "challenge_id": cid,
        "summary": summary,
        "flag_value": flag_value,
        "candidates": candidates,
        "submitted": bool(summary.get("submitted")) or bool(flag_value),
    }


def run_hunt(config, *, urls, category="web", difficulty="medium", name="", brief="",
             run_id="hunt", on_event=None, on_answer=None, cancel_event=None) -> dict:
    """新建离线任务并跑完整解题。urls: list[str]。返回结果 dict。"""
    urls = [_normalize_url(u) for u in urls]
    cid = _seed_run(config, urls=urls, category=category, difficulty=difficulty,
                    name=name or _slug(urls[0].split("://")[-1].split("/")[0]),
                    brief=brief, run_id=run_id)
    store = Store(config.run_dir(run_id)).load()
    result = _run_crew(config, store, cid, on_event=on_event, on_answer=on_answer,
                       cancel_event=cancel_event)
    result["run_id"] = run_id
    _write_flag_file(config, run_id, result["candidates"])
    return result


def resume_run(config, *, run_id, on_event=None, on_answer=None, cancel_event=None) -> dict:
    """续跑历史离线任务：不重播种，沿用 store 里累计用量与状态。"""
    store = Store(config.run_dir(run_id)).load()
    if not store.challenges:
        raise RuntimeError(f"run {run_id!r} 没有题目，无法续跑")
    marker = Path(config.run_dir(run_id)) / _MARKER
    cid = ""
    if marker.exists():
        try:
            cid = str(json.loads(marker.read_text(encoding="utf-8")).get("challenge_id") or "")
        except Exception:  # noqa: BLE001
            cid = ""
    cid = cid or next(iter(store.challenges))
    result = _run_crew(config, store, cid, on_event=on_event, on_answer=on_answer,
                       cancel_event=cancel_event)
    result["run_id"] = run_id
    _write_flag_file(config, run_id, result["candidates"])
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="离线解题：给靶场地址 + 题型 + 难度，自动攻击并输出 flag 候选（不接平台）"
    )
    ap.add_argument("--url", action="append", required=True,
                    help="靶场地址，可多次（http://host:port 或 host:port）")
    ap.add_argument("--category", default="web", help="题型 web/pwn/rev/crypto/misc/ai")
    ap.add_argument("--difficulty", default="medium", help="难度 easy/medium/hard")
    ap.add_argument("--name", default="", help="题名（用于 run 内 id，默认取第一个 host）")
    ap.add_argument("--desc", default="", help="题面文字（与 --brief 二选一或都用）")
    ap.add_argument("--brief", default="", help="题面 md/txt 文件路径")
    ap.add_argument("--run-id", default="hunt", help="run 目录名（默认 hunt）")
    ap.add_argument("--config", default=None, help="config 路径（默认 config/agent.json）")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    config = load(Path(args.config) if args.config else None)
    config.token = ""  # 离线：不接平台，永不触碰 CTFY_TOKEN
    config.mcp_url = "offline://"

    brief = args.desc
    if args.brief:
        brief = Path(args.brief).read_text(encoding="utf-8")

    def on_event(kind, payload=None):
        p = payload or {}
        if kind == "delegate":
            print(f"[协调] 派活 -> {p.get('role')}：{str(p.get('task'))[:120]}")
        elif kind == "report":
            print(f"[协调] {p.get('role')} 交结论：{str(p.get('report'))[:160]}")
        elif kind == "flag_accepted":
            print(f"           [!!] 记录候选通过：{p.get('challenge_id')}")
        elif kind == "budget":
            print(f"           [预算] token={p.get('total_tokens')} calls={p.get('calls')}")
        elif kind == "budget_exhausted":
            print(f"           [预算] 耗尽：{p.get('note') or p}")
        elif kind == "instance_guard":
            print(f"           {str(p.get('note') or '')[:200]}")

    def on_answer(cid, answer):
        if answer:
            print(f"           [候选] {answer}")

    result = run_hunt(
        config, urls=args.url, category=args.category, difficulty=args.difficulty,
        name=args.name, brief=brief, run_id=args.run_id,
        on_event=on_event, on_answer=on_answer,
    )

    print("\n== flag 候选 ==")
    for c in result["candidates"]:
        print(f"  {c}")
    print(f"\n已写入 {Path(config.run_dir(args.run_id)) / 'flag.txt'}")
    return 0 if result["submitted"] else 5


if __name__ == "__main__":
    raise SystemExit(main())