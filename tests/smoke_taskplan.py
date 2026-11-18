#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_taskplan.py — 赛程级排序 + 按题预算（11-20 正式赛 P0 回归）。

覆盖两块：
  1) `ctfy_agent.taskplan` 纯函数：难度归一、剩余份额、解出概率（0 解降权）、
     EV 排序、难度 cap、near-miss 加成、有限池分摊、slots；
  2) `gui.platform_worker.solve_many` 的**预算接线**：按 EV 重排执行顺序、把每题 quota
     落成 `config.max_tokens_per_challenge`、`budget_tokens` 透传（supervisor 死参数修复）。

不依赖真平台/真模型：FakePlatform + FakeStore + FakeCrew（monkeypatch）。
"""
from __future__ import annotations

import pathlib
import queue
import sys
import threading
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import taskplan  # noqa: E402
from ctfy_agent.gui import platform_worker as pw  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


# ---------- 1) 纯函数 ----------

def test_pure() -> None:
    check("难度归一：Easy/MEDIUM/困难", (
        taskplan.normalize_difficulty("Easy") == "easy"
        and taskplan.normalize_difficulty("MEDIUM") == "medium"
        and taskplan.normalize_difficulty("困难") == "hard"
        and taskplan.normalize_difficulty("?") == ""))

    single = {"id": "A", "points": 100, "difficulty": "easy"}
    check("无 questions：未解=1、已解=0", (
        taskplan.share_fraction(single) == 1.0
        and taskplan.share_fraction({**single, "my_fully_solved": True}) == 0.0))

    multi = {"id": "B", "points": 400, "difficulty": "medium",
             "questions": [{"id": "flag1", "share_percent": 25},
                           {"id": "flag2", "share_percent": 25},
                           {"id": "flag3", "share_percent": 50}],
             "my_solved_question_ids": ["flag1"]}
    check("多问题题：已解一问 → 剩余 75%", abs(taskplan.share_fraction(multi) - 0.75) < 1e-9,
          str(taskplan.share_fraction(multi)))

    check("概率：easy > medium > hard", (
        taskplan.solve_probability({"difficulty": "easy", "solves_count": 30})
        > taskplan.solve_probability({"difficulty": "medium", "solves_count": 30})
        > taskplan.solve_probability({"difficulty": "hard", "solves_count": 30})))

    zero_late = taskplan.solve_probability({"difficulty": "hard", "solves_count": 0},
                                           elapsed_ratio=0.6)
    zero_early = taskplan.solve_probability({"difficulty": "hard", "solves_count": 0},
                                            elapsed_ratio=0.1)
    check("赛程过半仍全服 0 解 → 强降权", zero_late < zero_early and zero_late < 0.15,
          f"{zero_early:.3f}->{zero_late:.3f}")

    rows = [
        {"id": "hard0", "points": 500, "difficulty": "hard", "solves_count": 0},
        {"id": "easy1", "points": 100, "difficulty": "easy", "solves_count": 80},
        {"id": "med2", "points": 300, "difficulty": "medium", "solves_count": 20},
    ]
    ranked = [row["id"] for row in taskplan.rank(rows, elapsed_ratio=0.6)]
    check("EV 排序：hard0 沉底", ranked[-1] == "hard0", str(ranked))

    check("难度 cap：easy/medium/hard/unknown 全部 800k（2026-11-18 全开）", (
        taskplan.token_cap({"difficulty": "easy"}) == 800_000
        and taskplan.token_cap({"difficulty": "medium"}) == 800_000
        and taskplan.token_cap({"difficulty": "hard"}) == 800_000
        and taskplan.token_cap({"difficulty": "?"}) == taskplan.FALLBACK_CAP == 800_000))
    check("near-miss 加注但封顶 800k",
          taskplan.token_cap({"difficulty": "medium"}, near_miss=True) == 800_000)

    unlimited = taskplan.plan(rows)
    check("不限池（默认）：quota=难度 cap、按 EV 排序",
          [p["id"] for p in unlimited][0] == "med2"
          and unlimited[0]["quota"] == 800_000
          and unlimited[-1]["quota"] == 800_000,
          str([(p["id"], p["quota"]) for p in unlimited]))

    limited = taskplan.plan(rows, pool=2_000_000, reserve_ratio=0.1)
    usable = 2_000_000 * 0.9
    check("有限池：总配额 ≤ 可用池", sum(p["quota"] for p in limited) <= usable + 1,
          f"{sum(p['quota'] for p in limited)} vs {usable}")
    check("有限池：单题仍不超难度 cap",
          all(p["quota"] <= taskplan.token_cap({"difficulty": d}) for p, d in
              zip(limited, [["easy", "medium", "hard"][["easy1", "med2", "hard0"].index(p["id"])]
                            for p in limited])))

    slots = taskplan.plan(rows, slots=1)
    check("slots=1 只返回 1 题", len(slots) == 1, str(slots))
    check("空输入 → 空计划", taskplan.plan([]) == [])


# ---------- 2) solve_many 预算接线 ----------

class ChallengeStub:
    def __init__(self, cid: str, **kwargs) -> None:
        self.id = cid
        self.name = kwargs.get("name", cid)
        self.category = kwargs.get("category", "")
        self.points = kwargs.get("points")
        self.description = ""
        self.tags = list(kwargs.get("tags") or [])
        self.difficulty = kwargs.get("difficulty", "")
        self.solves_count = kwargs.get("solves_count")
        self.questions = list(kwargs.get("questions") or [])
        self.is_qa_only = False
        self.has_container = False
        self.requires_running_target = False
        self.status = "new"
        self.answers_by_qid = dict(kwargs.get("answers_by_qid") or {})
        self.verified_qids = {}
        self.submitted_ok = bool(kwargs.get("submitted_ok"))
        self.flag_value = kwargs.get("flag_value", "")
        self.tokens = {}
        self.instance = {}
        self.instance_id = ""


class FakeStore:
    def __init__(self, rows: dict[str, ChallengeStub]) -> None:
        self.meta: dict = {}
        self._rows = rows

    def save(self) -> None:
        return None

    def get(self, cid):  # noqa: ANN001
        return self._rows.get(cid)


class FakePlatform:
    def __init__(self, rows: dict[str, ChallengeStub], comp: dict | None = None) -> None:
        self.rows = rows
        self.comp = comp or {"play_open": True}
        self.connect_calls = 0
        self.challenge_calls = 0

    def connect(self) -> None:
        self.connect_calls += 1

    def get_competition(self, competition_id: str) -> dict:
        return dict(self.comp)

    def get_challenge(self, challenge_id: str) -> dict:
        self.challenge_calls += 1
        return {"id": challenge_id, "closed": False, "scoring": "scored"}


class FakeCrew:
    """记录每题拿到的 config.max_tokens_per_challenge，返回未解出（不花 token）。"""
    seen: list[dict] = []

    def __init__(self, config, platform, store, llm_factory, on_event=None) -> None:
        self.config = config
        self.on_event = on_event

    def pin_access_lines(self, lines) -> None:  # noqa: ANN001
        return None

    def solve(self, challenge_id, cancel_event=None, force_rerun=False):  # noqa: ANN001
        FakeCrew.seen.append({"id": challenge_id,
                              "cap": int(getattr(self.config, "max_tokens_per_challenge", 0)),
                              "cancel": cancel_event is not None})
        return {"submitted": False, "reason": "fake", "tokens": 0}


def drain(q) -> list:  # noqa: ANN001
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def run_batch(rows: dict[str, ChallengeStub], *, budget_tokens=None, deadline=None,
              comp=None):
    import ctfy_agent.cli as cli_mod
    import ctfy_agent.crew as crew_mod

    old = {
        "_platform": pw._platform,
        "open_store": cli_mod.open_store,
        "make_llm": cli_mod.make_llm,
        "ensure": cli_mod.ensure_challenge_meta,
        "Crew": crew_mod.Crew,
        "hydrate": pw._hydrate_attachments,
        "reap": pw._preflight_instances,
    }
    platform = FakePlatform(rows, comp)
    store = FakeStore(rows)
    FakeCrew.seen = []
    pw._COMP_CACHE.clear()
    try:
        pw._platform = lambda config, verbose=False, cancel=None: platform  # type: ignore[assignment]
        cli_mod.open_store = lambda config, run_id: store  # type: ignore[assignment]
        cli_mod.make_llm = lambda config, model=None: None  # type: ignore[assignment]
        cli_mod.ensure_challenge_meta = lambda p, s, cid: None  # type: ignore[assignment]
        crew_mod.Crew = FakeCrew  # type: ignore[assignment]
        pw._hydrate_attachments = lambda *a, **k: ""  # type: ignore[assignment]
        pw._preflight_instances = lambda *a, **k: None  # type: ignore[assignment]

        q: "queue.Queue" = queue.Queue()
        config = types.SimpleNamespace(extra={}, policy={}, max_tokens_per_challenge=800_000)
        challenges = [{"id": cid, "name": rows[cid].name} for cid in rows]
        pw.solve_many(config, challenges, "run-tp", "comp-tp-1", q, threading.Event(),
                      concurrency=1, budget_tokens=budget_tokens, deadline=deadline)
        return drain(q), platform
    finally:
        pw._platform = old["_platform"]  # type: ignore[assignment]
        cli_mod.open_store = old["open_store"]  # type: ignore[assignment]
        cli_mod.make_llm = old["make_llm"]  # type: ignore[assignment]
        cli_mod.ensure_challenge_meta = old["ensure"]  # type: ignore[assignment]
        crew_mod.Crew = old["Crew"]  # type: ignore[assignment]
        pw._hydrate_attachments = old["hydrate"]  # type: ignore[assignment]
        pw._preflight_instances = old["reap"]  # type: ignore[assignment]
        pw._COMP_CACHE.clear()


def test_wiring() -> None:
    rows = {
        "CH-HARD": ChallengeStub("CH-HARD", points=500, difficulty="hard", solves_count=0),
        "CH-EASY": ChallengeStub("CH-EASY", points=100, difficulty="easy", solves_count=90),
        "CH-MED": ChallengeStub("CH-MED", points=300, difficulty="medium", solves_count=10),
    }
    events, platform = run_batch(rows)
    order = [e[1]["id"] for e in events if e[0] == "task_start"]
    check("solve_many 按 EV 重排执行顺序", order == ["CH-MED", "CH-EASY", "CH-HARD"], str(order))

    caps = {item["id"]: item["cap"] for item in FakeCrew.seen}
    check("每题 quota 落成 config.max_tokens_per_challenge",
          caps == {"CH-EASY": 800_000, "CH-MED": 800_000, "CH-HARD": 800_000}, str(caps))
    check("GUI 批量单题上限 = 800k（2026-11-18 全开）", all(c == 800_000 for c in caps.values()), str(caps))

    batches = [e[1] for e in events if e[0] == "batch_note"]
    check("batch_note 报告 EV 排序与上限",
          any("按 EV 排序" in str(b) and "800k" in str(b) for b in batches), str(batches)[:300])

    done = [e[1] for e in events if e[0] == "task_done"]
    check("3 题都回调 task_done", len(done) == 3, str(len(done)))

    # supervisor 路径：显式 budget_tokens 优先，不再做内部分摊/改顺序
    order2_events, _ = run_batch(rows, budget_tokens=123_456, deadline=None)
    order2 = [e[1]["id"] for e in order2_events if e[0] == "task_start"]
    caps2 = {item["id"]: item["cap"] for item in FakeCrew.seen}
    check("supervisor 传 quota：每题 cap == quota", set(caps2.values()) == {123_456}, str(caps2))
    check("supervisor 传 quota：保持传入顺序（不再内部 EV 重排）",
          order2 == ["CH-HARD", "CH-EASY", "CH-MED"], str(order2))

    # near-miss（已拿到部分题号）→ 该题 quota 加注 50%
    part = {"CH-PART": ChallengeStub(
        "CH-PART", points=200, difficulty="medium", solves_count=12,
        questions=[{"id": "flag1", "share_percent": 50}, {"id": "flag2", "share_percent": 50}],
        answers_by_qid={"flag1": "x"})}
    run_batch(part)
    caps_part = [item["cap"] for item in FakeCrew.seen]
    check("near-miss 题 quota 加注后仍封顶 800k", caps_part == [800_000], str(caps_part))

    # deadline 透传：单题仍拿得到 cancel_event（计时器装上）
    run_batch(rows, budget_tokens=50_000, deadline=None)
    check("预算接线后每题仍装 cancel_event",
          all(item["cancel"] for item in FakeCrew.seen), str(FakeCrew.seen))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    test_pure()
    test_wiring()
    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())