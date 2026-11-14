#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：提交额度口径对齐 CTFY 正赛平台（P22）。

取证（runs/**/transcripts，CTFY 回执原文）：
  * `questions[]` 里出现 `"attempts_cap":10,"attempts_remaining":10`（CYBENCH-013 / CYBENCH-004 等）；
  * 提交回执里 `attempts_remaining` 会递减（10 → 9 … 1），部分题为 null（不限额）；
  * CTFY 回执**没有** ctf2 的 `attempt` 字段 → 只能用 cap-remaining 反推已用次数。
本用例锁：
  1) state.note_platform_remaining 正确反推已用次数并落盘；
  2) Solver._platform_quota_from 能解析三种承载位置（顶层 / structuredContent / 文本 JSON）；
  3) config/agent.json 的提交上限 = 10（不再沿用 ctf2 的 5）。
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.solver import Solver  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("[ok] " if cond else "[FAIL] ") + name + ("" if cond or not detail else "  " + detail))
    if not cond:
        FAILS.append(name)


class FakePlatform:
    @staticmethod
    def _as_json(text):  # noqa: ANN001
        return None


def case_store(tmp: pathlib.Path) -> None:
    store = Store(tmp / "run").load()
    if "c-1" not in store.challenges:
        from ctfy_agent.state import Challenge

        store.challenges["c-1"] = Challenge(id="c-1", name="CTFY 样例")
        store.save()

    # 10 次上限、剩 9 → 已用 1
    used = store.note_platform_remaining("c-1", 9, 10)
    item = store.require("c-1")
    check("remaining=9/cap=10 → 已用 1", used == 1, str(used))
    check("attempts_cap/attempts_remaining 落盘", item.attempts_cap == 10 and item.attempts_remaining == 9,
          f"{item.attempts_cap}/{item.attempts_remaining}")

    # 单调：再读到更大的 remaining 不会把已用次数写小
    used = store.note_platform_remaining("c-1", 10, 10)
    check("remaining 回升不会把已用次数写小", used == 1, str(used))

    # 用尽
    used = store.note_platform_remaining("c-1", 0, 10)
    check("remaining=0 → 已用 10", used == 10, str(used))

    # null（不限额）→ 忽略，不改已用次数
    used = store.note_platform_remaining("c-1", -1, 0)
    check("remaining=null → 不改已用次数", used == 10, str(used))

    # 重新加载后仍保持（跨进程可见）
    again = Store(tmp / "run").load().require("c-1")
    check("重新加载后额度字段仍在", again.attempts_cap == 10 and again.attempts_remaining == 0)
    check("重新加载后已用次数仍在", again.platform_attempts == 10)


def case_parser() -> None:
    fake = object.__new__(Solver)
    fake.platform = FakePlatform()
    parse = Solver._platform_quota_from

    got = parse(fake, {"attempts_cap": 10, "attempts_remaining": 3}, "")
    check("顶层字段解析", got == (10, 3), str(got))

    got = parse(fake, {"structuredContent": {"attempts_cap": 10, "attempts_remaining": 0}}, "")
    check("structuredContent 解析", got == (10, 0), str(got))

    text = '----- 工具返回 -----\n{"id":"x","correct":false,"attempts_remaining":7,"attempts_cap":10}'
    got = parse(fake, {}, text)
    check("文本 JSON 解析", got == (10, 7), str(got))

    got = parse(fake, {"attempts_remaining": None, "attempts_cap": None}, "")
    check("null → 未知 (0,-1)", got == (0, -1), str(got))

    got = parse(fake, {"correct": True}, "")
    check("无字段 → 未知 (0,-1)", got == (0, -1), str(got))


def case_config() -> None:
    cfg = json.loads((ROOT / "config/agent.json").read_text(encoding="utf-8"))
    extra = cfg.get("extra") or {}
    limit = extra.get("platform_submit_limit", cfg.get("platform_submit_limit"))
    check("config.platform_submit_limit = 10（CTFY 口径）", limit == 10, str(limit))
    check("config.submit_limit = 10", cfg.get("submit_limit") == 10, str(cfg.get("submit_limit")))


def main() -> int:
    tmp = ROOT / "runs" / "_capcheck-tmp" / "ctfy_quota"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        case_parser()
        case_store(tmp)
        case_config()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_ctfy_quota 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())