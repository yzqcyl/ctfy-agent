#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_playability.py — 「不可玩」硬阻塞的 fail-fast（2026-11-16 关闭场次事故回归）。

背景（真事故）：练习场关闭后（`play_open=false`、题目 `closed=true` / `scoring=closed`）
在 GUI 里批量解题，5 道题每题仍开满 20 分钟预算、烧 41~79 万 token，最后界面显示
「未解出 / 时间预算到」——把「题不可玩」误报成「解题失败」，合计浪费 ~338 万 token。

本测试锁三件事：
  1) `access_facts.playability_blockers` 纯函数判定（暂停/关闭/停计分/平台已解）；
  2) `platform_worker.preflight_playable` 真正问平台、比赛信息只拉一次（缓存）；
  3) `solve_many` 在整批不可玩时 0 派活、0 预算，直接给每题写「不可玩」结论。
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import access_facts  # noqa: E402
from ctfy_agent.gui import platform_worker as pw  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


class FakePlatform:
    """只实现预检会用到的方法；get_challenge 被调用即视为失败（整批暂停时不该拉题）。"""

    def __init__(self, comp: dict | None = None, challenge: dict | None = None,
                 forbid_challenge: bool = False) -> None:
        self.comp = comp or {}
        self.challenge = challenge or {}
        self.forbid_challenge = forbid_challenge
        self.comp_calls = 0
        self.challenge_calls = 0

    def connect(self) -> None:
        return None

    def get_competition(self, competition_id: str) -> dict:
        self.comp_calls += 1
        return dict(self.comp)

    def get_challenge(self, challenge_id: str) -> dict:
        if self.forbid_challenge:
            raise AssertionError("整批不可玩时不该再拉题")
        self.challenge_calls += 1
        return dict(self.challenge)


class FakeStore:
    def __init__(self) -> None:
        self.meta: dict = {}

    def save(self) -> None:
        return None

    def get(self, cid):  # noqa: ANN001 - 预检路径用不到
        return None


def drain(q: "queue.Queue") -> list:
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def test_blockers() -> None:
    check("开局正常：空竞赛/空题目 → 无阻塞",
          access_facts.playability_blockers(None, None) == [])
    check("正常可玩：play_open=true / closed=false → 无阻塞",
          access_facts.playability_blockers(
              {"play_open": True}, {"closed": False, "scoring": "scored"}) == [])

    paused = access_facts.playability_blockers(
        {"play_open": False, "next_session_starts_at": "2026-11-20T10:00:00+00:00"}, {})
    check("比赛暂停 → 1 条阻塞且带下一局时间",
          len(paused) == 1 and "暂停" in paused[0] and "2026-11-20" in paused[0], str(paused))

    closed = access_facts.playability_blockers({}, {"closed": True})
    check("题目关闭 → 1 条阻塞", len(closed) == 1 and "closed=true" in closed[0], str(closed))

    scoring = access_facts.playability_blockers({}, {"scoring": "closed"})
    check("停止计分 → 1 条阻塞", len(scoring) == 1 and "scoring=closed" in scoring[0], str(scoring))

    solved = access_facts.playability_blockers({}, {"my_fully_solved": True})
    check("平台已解 → 1 条阻塞", len(solved) == 1 and "已解出" in solved[0], str(solved))

    both = access_facts.playability_blockers(
        {"play_open": False}, {"closed": True, "scoring": "closed"})
    check("暂停+关闭+停计分 → 3 条阻塞", len(both) == 3, str(both))
    check("非 dict 入参不炸", access_facts.playability_blockers("x", 3) == [])


def test_preflight_helper() -> None:
    fake = FakePlatform(comp={"play_open": False}, forbid_challenge=True)
    reasons = pw.preflight_playable(fake, "comp-preflight-1", "CH-1")
    check("preflight_playable 命中比赛暂停", len(reasons) == 1 and "暂停" in reasons[0], str(reasons))
    check("整批暂停时不拉题目详情", fake.challenge_calls == 0, str(fake.challenge_calls))

    fake2 = FakePlatform(comp={"play_open": True}, challenge={"closed": True})
    reasons2 = pw.preflight_playable(fake2, "comp-preflight-2", "CH-2")
    check("preflight_playable 命中题目关闭", len(reasons2) == 1 and "已关闭" in reasons2[0], str(reasons2))
    check("题目信息只拉一次即可判定", fake2.challenge_calls == 1, str(fake2.challenge_calls))

    # 缓存：同一比赛第二次预检不再拉 get_competition
    before = fake2.comp_calls
    pw.preflight_playable(fake2, "comp-preflight-2", "CH-2")
    check("比赛信息按 competition 缓存（只拉一次）", fake2.comp_calls == before, f"{before}->{fake2.comp_calls}")


def test_solve_many_fail_fast() -> None:
    import ctfy_agent.cli as cli_mod

    old_platform, old_store, old_make_llm = pw._platform, cli_mod.open_store, cli_mod.make_llm
    old_ensure = cli_mod.ensure_challenge_meta
    pw._COMP_CACHE.clear()
    fake = FakePlatform(comp={"play_open": False,
                              "next_session_starts_at": "2026-11-20T10:00:00+00:00"},
                        forbid_challenge=True)
    try:
        pw._platform = lambda config, verbose=False, cancel=None: fake  # type: ignore[assignment]
        cli_mod.open_store = lambda config, run_id: FakeStore()  # type: ignore[assignment]
        cli_mod.make_llm = lambda config, model=None: None  # type: ignore[assignment]
        cli_mod.ensure_challenge_meta = lambda platform, store, cid: None  # type: ignore[assignment]

        q: "queue.Queue" = queue.Queue()
        config = types.SimpleNamespace(extra={})
        pw.solve_many(config, [{"id": "CH-A", "name": "a"}, {"id": "CH-B", "name": "b"}],
                      "run-x", "comp-many-1", q, threading.Event(), concurrency=1)
        events = drain(q)
        kinds = [e[0] for e in events]
        check("整批暂停：不再开任何单题预算（无 event 事件）",
              "event" not in kinds, str(kinds))
        done_rows = [e[1] for e in events if e[0] == "task_done"]
        check("两题都回调 task_done", len(done_rows) == 2, str(len(done_rows)))
        check("两题都被标记不可玩且 0 flag",
              all("不可玩" in (r.get("note") or "") and not r.get("flag") for r in done_rows),
              str(done_rows))
        check("不会给平台拉题目详情（0 次）", fake.challenge_calls == 0, str(fake.challenge_calls))
        final = [e[1] for e in events if e[0] == "done"]
        check("批量 done 汇总为 0 成功",
              bool(final) and final[-1].get("batch") and not any(r.get("ok") for r in final[-1]["results"]),
              str(final[-1] if final else None))
    finally:
        pw._platform, cli_mod.open_store, cli_mod.make_llm = old_platform, old_store, old_make_llm
        cli_mod.ensure_challenge_meta = old_ensure
        pw._COMP_CACHE.clear()


def test_solve_many_single_challenge_blocked() -> None:
    """比赛正常但本题 closed：整批不拦，单题在 work() 里 0 token 跳过。"""
    import ctfy_agent.cli as cli_mod

    old_platform, old_store, old_make_llm = pw._platform, cli_mod.open_store, cli_mod.make_llm
    old_ensure = cli_mod.ensure_challenge_meta
    pw._COMP_CACHE.clear()
    open_comp = FakePlatform(comp={"play_open": True},
                             challenge={"closed": True, "scoring": "closed"})
    try:
        pw._platform = lambda config, verbose=False, cancel=None: open_comp  # type: ignore[assignment]
        cli_mod.open_store = lambda config, run_id: FakeStore()  # type: ignore[assignment]
        cli_mod.make_llm = lambda config, model=None: None  # type: ignore[assignment]
        cli_mod.ensure_challenge_meta = lambda platform, store, cid: None  # type: ignore[assignment]

        q: "queue.Queue" = queue.Queue()
        config = types.SimpleNamespace(extra={})
        pw.solve_many(config, [{"id": "CH-A", "name": "a"}],
                      "run-y", "comp-many-2", q, threading.Event(), concurrency=1)
        events = drain(q)
        done_rows = [e[1] for e in events if e[0] == "task_done"]
        check("单题 closed：task_done 标记不可玩",
              len(done_rows) == 1 and "不可玩" in (done_rows[0].get("note") or ""),
              str(done_rows))
        check("单题 closed：也没起任何题内事件（0 token）", "event" not in [e[0] for e in events],
              str([e[0] for e in events]))
    finally:
        pw._platform, cli_mod.open_store, cli_mod.make_llm = old_platform, old_store, old_make_llm
        cli_mod.ensure_challenge_meta = old_ensure
        pw._COMP_CACHE.clear()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    test_blockers()
    test_preflight_helper()
    test_solve_many_fail_fast()
    test_solve_many_single_challenge_blocked()
    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())