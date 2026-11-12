#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_platform_attempt.py — 平台提交次数（attempt）回写的回归（离线、不连平台）。

背景（HANDOFF 2026-11-10 §8 P2）：平台回执 `{"accepted":false,"attempt":N,…}` 里的 attempt 是
「用户+题」**全局**累计（跨 run、最多 5 轮），本地此前完全没读，于是换 run 目录重跑时本地以为
还有额度、平台其实已经用满，白交一次就是一次浪费。

离线钉住：
  1) 能从三种回执形态解析 attempt：返回值顶层 / structuredContent / 文本里的 JSON；
  2) 单调并入 `challenge.platform_attempts`（只增不减，跨 store 重载存活）；
  3) 平台额度用满 → 提交被本地**硬停**，不再打平台；
  4) 决不影响 `challenge.attempts`（本地解题轮次口径）与既有 per-question 闸；
  5) `platform_submit_limit=0` 可关掉这个硬停（老行为逃生口）。

运行: python tests/smoke_platform_attempt.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import Config  # noqa: E402
from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []
CID = "SAMPLE-ATTEMPT"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _wrap(payload) -> str:
    if isinstance(payload, str):
        return payload
    return "----- 工具返回 -----\n" + json.dumps(payload, ensure_ascii=False)


class FakePlatform:
    """按「题目中心」提交形态建模（submit_answer(challenge_id, flag)），回执可控。"""

    def __init__(self, receipts=None):
        self.receipts = list(receipts or [])
        self.payloads: list[tuple[str, dict]] = []
        self.tool_map = {"submit_flag": "submit_answer"}

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        if kind not in self.tool_map:
            from ctfy_agent.platform import PlatformError  # noqa: PLC0415

            raise PlatformError(f"没有可解析的工具：{kind}")
        return self.tool_map[kind]

    def prop_names(self, name):
        return {"challenge_id", "flag"} if name == "submit_answer" else set()

    def preferred_question_id(self, tool_name="submit_answer"):
        return "flag"

    def call_raw(self, name, arguments=None, *, retries=0):
        self.payloads.append((name, dict(arguments or {})))
        if self.receipts:
            return self.receipts.pop(0)
        return {"accepted": False, "attempt": 1, "submission_id": "sub-x"}

    @staticmethod
    def _as_json(text):
        raw = str(text or "")
        if raw.startswith("-----"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.strip()
        try:
            return json.loads(raw)
        except ValueError:
            return None


def _config(**extra) -> Config:
    cfg = Config()
    cfg.submit_limit = 3
    cfg.extra.update(extra)
    return cfg


def _store(root: pathlib.Path, **fields) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({"id": CID, "name": "attempt", "category": "MISC", "description": "d"})
    item = store.require(CID)
    for key, value in fields.items():
        setattr(item, key, value)
    store.save()
    return store


class DummyLLM:
    budget = None


def _submit(store, platform, cfg=None):
    solver = Solver(cfg or _config(), platform, DummyLLM(), store)
    return solver._submit({"flag": "flag{guess}"}, CID)


def main() -> int:
    tmp = ROOT / "runs" / f"_t_attempt_{os.getpid()}"

    # ---- 1) 顶层 attempt 解析 + 回显 ----
    store = _store(tmp / "r1")
    platform = FakePlatform([{"accepted": False, "attempt": 2, "submission_id": "s1"}])
    text, accepted = _submit(store, platform)
    check("回执被判为未通过", accepted is False, text[:120])
    check("顶层 attempt 落库", store.require(CID).platform_attempts == 2,
          str(store.require(CID).platform_attempts))
    check("回执回显平台计数", "第 2/5 次提交" in text, text[-200:])
    check("本地 attempts 与平台 attempt 互不覆盖",
          store.require(CID).attempts == 1 and store.require(CID).platform_attempts == 2,
          f"attempts={store.require(CID).attempts} platform={store.require(CID).platform_attempts}")

    # ---- 2) 单调：旧回执（attempt 更小）不回退 ----
    platform = FakePlatform([{"accepted": False, "attempt": 1}])
    _submit(store, platform)
    check("attempt 单调不回退", store.require(CID).platform_attempts == 2,
          str(store.require(CID).platform_attempts))

    # ---- 3) 跨 store 重载存活 ----
    reloaded = Store(tmp / "r1").load()
    check("重载后仍记得平台计数", reloaded.require(CID).platform_attempts == 2,
          str(reloaded.require(CID).platform_attempts))

    # ---- 4) structuredContent 形态 ----
    store2 = _store(tmp / "r2")
    platform = FakePlatform([{"isError": False, "structuredContent": {"accepted": False, "attempt": 3}}])
    text, _ = _submit(store2, platform)
    check("structuredContent attempt 解析", store2.require(CID).platform_attempts == 3,
          str(store2.require(CID).platform_attempts))

    # ---- 5) content 文本里的 JSON（MCP 真实返回形态：dict + content[].text）----
    store3 = _store(tmp / "r3")
    platform = FakePlatform([{
        "content": [{"type": "text",
                     "text": '{"accepted": false, "attempt": 4, "submission_id": "s"}'}],
    }])
    text, _ = _submit(store3, platform)
    check("文本 JSON attempt 解析", store3.require(CID).platform_attempts == 4,
          str(store3.require(CID).platform_attempts))
    check("文本 JSON 形态回显计数", "第 4/5 次提交" in text, text[-200:])

    # ---- 6) 平台额度用满 → 本地硬停，不再打平台 ----
    store4 = _store(tmp / "full", platform_attempts=5)
    platform = FakePlatform([{"accepted": True, "attempt": 6}])
    text, accepted = _submit(store4, platform)
    check("额度用满：提交被拦回", accepted is False and "提交被拦截" in text, text[:200])
    check("额度用满：说明平台累计次数", "平台已累计本题 5 次提交" in text, text[:240])
    check("额度用满：一次都没打平台", platform.payloads == [], str(platform.payloads))

    # ---- 7) 逃生口：platform_submit_limit=0 关闭硬停 ----
    store5 = _store(tmp / "off", platform_attempts=9)
    platform = FakePlatform([{"accepted": False, "attempt": 10}])
    text, accepted = _submit(store5, platform, _config(platform_submit_limit=0))
    check("逃生口：关闭硬停后仍会真的提交", len(platform.payloads) == 1, str(platform.payloads))
    check("逃生口：计数继续单调并入", store5.require(CID).platform_attempts == 10,
          str(store5.require(CID).platform_attempts))

    # ---- 8) 判通过路径不受影响 ----
    store6 = _store(tmp / "accepted")
    platform = FakePlatform([{"accepted": True, "attempt": 1, "challenge_fully_solved": True}])
    text, accepted = _submit(store6, platform)
    check("判通过仍返回 True", accepted is True, text[:200])
    check("判通过仍落 submitted", store6.require(CID).status == "submitted",
          str(store6.require(CID).status))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"smoke_platform_attempt 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_platform_attempt 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())