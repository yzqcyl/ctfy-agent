#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_solved_scope.py — 「已解出」事实源必须按比赛收窄（CTFY 实测）。

背景（2026-11-13 CTFY 接入测试，真实回执）：
  练习场上 `get_my_solves` 不带 competition_id 回 **232 条**（CTFARENA / SAMPLE / SQLILABS… 全局）；
  带练习场 id 只回 **1 条**；带 TPCTF（没打过）回 **0 条**。
  题 id 会跨比赛重复（SAMPLE-016 同时在 SAMPLE 套题与练习场）。旧版 my_solved_ids() 永远查全局，
  于是「在别的比赛解过」= 「本场已解」→ 预检直接短路，整场可能一个 flag 都不交。
  crew 的 4 处预检/兜底全部改走 _platform_solved_ids()：比赛上下文未知时返回空集合（宁可不短路）。

覆盖：
  1) my_solved_ids(scope) 真的把 competition_id 传给工具；
  2) 列表 / {"result": [...]} 两种回执都能解析；
  3) 缓存按比赛分键，且显式 scope 覆盖 active_competition_id；
  4) call_text 会记住带过的 competition_id（零参调用也能收窄）；
  5) crew._platform_solved_ids：无上下文→空集合；有 meta→按 meta 收窄；有 L1 缓存→用 active。

运行: python tests/smoke_solved_scope.py
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.crew import Crew  # noqa: E402
from ctfy_agent.platform import CtfyPlatform  # noqa: E402
from ctfy_agent.config import Config  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []
PLAY = "bc60dc1764f34413964a547102f17261"
TPCTF = "c0d8b4e3dd3b42c095d63d7dfa66431f"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def fake_platform(table: dict[str, list[dict]]) -> CtfyPlatform:
    """最小平台替身：只实现 call / call_text / _as_json / my_solved_ids 需要的属性。"""
    platform = CtfyPlatform.__new__(CtfyPlatform)
    platform.tool_map = {}
    platform._by_name = {"get_my_solves": {}}
    platform._resolved = {}
    platform.active_competition_id = ""
    platform.seen_args = []

    def call(name: str, arguments=None, *, retries: int = 0):
        args = arguments or {}
        platform.seen_args.append(dict(args))
        scope = str(args.get("competition_id") or "")
        rows = table.get(scope, table.get("", []))
        return {"content": [{"type": "text", "text": json.dumps(rows, ensure_ascii=False)}]}

    platform.call = call  # type: ignore[assignment]
    return platform


def main() -> int:
    # ---- 1) scope 真的传下去；两种回执形状都能解析 ----
    table = {
        PLAY: [{"challenge_id": "SAMPLE-016"}],
        TPCTF: [],
        "": [{"challenge_id": "SAMPLE-016"}, {"challenge_id": "CTFARENA-000"}],
    }
    p = fake_platform(table)
    got_play = p.my_solved_ids(PLAY)
    check("带 scope 查询：只拿到本场的 1 条", got_play == {"SAMPLE-016"}, str(got_play))
    check("scope 真的进了工具参数", p.seen_args[-1] == {"competition_id": PLAY}, str(p.seen_args[-1]))
    got_tp = p.my_solved_ids(TPCTF)
    check("另一个比赛查询得到空集合（不串场）", got_tp == set(), str(got_tp))
    # 没有任何比赛上下文时才查全局（兼容旧平台）
    fresh = fake_platform(table)
    check("无任何上下文时才查全局", fresh.my_solved_ids() == {"SAMPLE-016", "CTFARENA-000"},
          str(fresh.my_solved_ids()))

    # dict 包裹形状
    p2 = fake_platform({})
    p2.call = lambda name, arguments=None, retries=0: {  # type: ignore[assignment]
        "content": [{"type": "text", "text": json.dumps({"result": [{"challenge_id": "SAMPLE-016"}]})}]}
    check('{"result": [...]} 包裹可解析', p2.my_solved_ids(PLAY) == {"SAMPLE-016"})

    # ---- 2) 缓存按比赛分键；显式 scope 覆盖 active ----
    p3 = fake_platform(table)
    p3.my_solved_ids(PLAY)
    calls_after_first = len(p3.seen_args)
    p3.my_solved_ids(PLAY)
    check("同 scope 10s 内命中缓存（不重复打平台）", len(p3.seen_args) == calls_after_first)
    p3.my_solved_ids(TPCTF)
    check("换 scope 会重新查（缓存分键）", len(p3.seen_args) == calls_after_first + 1)
    p3.active_competition_id = PLAY
    p3.my_solved_ids(TPCTF)
    check("显式 scope 覆盖 active_competition_id",
          p3.seen_args[-1] == {"competition_id": TPCTF}, str(p3.seen_args[-1]))
    # 显式调用会更新上下文（它也是「最近一次带过的比赛」）；单独验证零参收窄
    fresh2 = fake_platform(table)
    fresh2.active_competition_id = PLAY
    fresh2.my_solved_ids()
    check("零参调用用 active_competition_id 收窄",
          fresh2.seen_args[-1] == {"competition_id": PLAY}, str(fresh2.seen_args[-1]))

    # ---- 3) call_text 记住带过的 competition_id ----
    p4 = fake_platform(table)
    p4.call_text("get_challenge", {"challenge_id": "SAMPLE-016", "competition_id": PLAY})
    check("call_text 记录 competition_id", p4.active_competition_id == PLAY, p4.active_competition_id)
    p4.call_text("get_me", {})
    check("不带 competition_id 的调用不清空上下文", p4.active_competition_id == PLAY)

    # ---- 4) crew._platform_solved_ids ----
    crew = Crew.__new__(Crew)
    crew.config = Config()
    crew.store = Store(ROOT / "runs" / f"_t_scope_{__import__('os').getpid()}" / "store").load()
    crew.platform = fake_platform(table)
    check("无比赛上下文 → 空集合（宁可不短路，也不误判已解）", crew._platform_solved_ids() == set())
    crew.store.meta["competition_id"] = PLAY
    check("有 store.meta → 按本场收窄", crew._platform_solved_ids() == {"SAMPLE-016"})
    check("crew 查询把 scope 传给了平台",
          crew.platform.seen_args[-1].get("competition_id") == PLAY,
          str(crew.platform.seen_args[-1]))
    crew.store.meta["competition_id"] = TPCTF
    check("换比赛后按新比赛查询", crew._platform_solved_ids() == set(), str(crew._platform_solved_ids()))

    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())