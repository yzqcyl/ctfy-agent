#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_a4_revalue_round.py — 「第二轮换值重交」端到端回归（离线、不连平台）。

审计 A4（CTFY-0001 v3 §3.3）：平台在「wrong 且该题还有次数」时用**同一工作目录重启**我们
（最多 5 轮），让我们**换一个值再交**。本地必须做到、且此前缺端到端用例钉住的：
  1) 第一轮交错的值跨 store 重载后仍在（flags_tried / wrong_by_qid 持久化）——第二轮起跑
     不能再把同一个错值重交（协议「never repeats a submitted value」）；
  2) 记了「错值」不等于「该题号锁死」：换一个新值、同一题号、额度未满 → 仍放行；
  3) 同一值永远拦（duplicate）；该题号 wrong-attempt cap 用尽后，换新值也拦；
  4) 不同题号额度独立，不被别的题号已烧掉的额度锁死。

运行: python tests/smoke_a4_revalue_round.py
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
CID = "SAMPLE-A4"
LIMIT = 3


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _store(root: pathlib.Path) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({"id": CID, "name": "multi", "category": "WEB", "description": "多问题题"})
    store.save()
    return store


# ---------- 单元级：store 的跨轮持久 + 换值放行 ----------
def store_level(tmp: pathlib.Path) -> None:
    store = _store(tmp / "store")
    ok = store.reserve_submit(CID, "flag{wrong_A}", LIMIT, question_id="flag")
    check("store：第一轮交 A 预留成功", ok == "ok", ok)
    store.mark_submitted(CID, "flag{wrong_A}", accepted=False, question_id="flag")

    # 模拟平台重启带来第二轮：重新 load（走 _refresh_flags 单调合并）
    store2 = Store(tmp / "store").load()
    check("store：错交次数跨轮持久", store2.require(CID).wrong_by_qid.get("flag") == 1,
          str(store2.require(CID).wrong_by_qid))
    check("store：同值跨轮仍拦（duplicate）",
          store2.reserve_submit(CID, "flag{wrong_A}", LIMIT, question_id="flag") == "duplicate")
    # 关键断言：记了错值 ≠ 锁死题号，换新值仍放行（第二轮换值重交）
    check("store：第二轮换新值 B 仍放行",
          store2.reserve_submit(CID, "flag{fresh_B}", LIMIT, question_id="flag") == "ok")

    # 用完该题号额度：B、C 各错两次 → 该题号共 3 次
    for val in ("flag{fresh_B}", "flag{fresh_C}"):
        store2.mark_submitted(CID, val, accepted=False, question_id="flag")
    check("store：该题号额度用尽后换新值被拦",
          store2.reserve_submit(CID, "flag{fresh_D}", LIMIT, question_id="flag") == "limit")
    # 不同题号额度独立
    check("store：换题号额度独立（不被 flag 题号锁死）",
          store2.reserve_submit(CID, "flag{slug_1}", LIMIT, question_id="slug") == "ok")


# ---------- 集成级：solver._submit 走完整链路 ----------
class FakePlatform:
    """题目中心提交形态（submit_answer(challenge_id, flag/answer)），回执可控。"""

    def __init__(self, receipts=None):
        self.receipts = list(receipts or [])
        self.payloads: list[tuple[str, dict]] = []
        self.tool_map = {"submit_flag": "submit_answer"}

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        if kind not in self.tool_map:
            from ctfy_agent.platform import PlatformError  # noqa: PLC0415

            raise PlatformError(kind)
        return self.tool_map[kind]

    def prop_names(self, name):
        return {"challenge_id", "flag"} if name == "submit_answer" else set()

    def preferred_question_id(self, tool_name="submit_answer"):
        return "flag"

    def call_raw(self, name, arguments=None, *, retries=0):
        self.payloads.append((name, dict(arguments or {})))
        if self.receipts:
            return self.receipts.pop(0)
        return {"accepted": False, "attempt": 0}

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


class DummyLLM:
    budget = None


def solver_level(tmp: pathlib.Path) -> None:
    store = _store(tmp / "solver")
    receipts = [{"accepted": False, "attempt": i + 1} for i in range(6)]
    platform = FakePlatform(receipts)
    cfg = Config()
    cfg.submit_limit = LIMIT
    solver = Solver(cfg, platform, DummyLLM(), store)

    def sub(flag, qid="flag"):
        return solver._submit({"flag": flag, "question_id": qid}, CID)

    # 第一轮：交错 A
    text, accepted = sub("flag{wrong_A}")
    check("solver：第一轮 A 判未通过", accepted is False, text[:120])
    check("solver：A 真的打到平台", platform.payloads and platform.payloads[-1][1].get("flag") == "flag{wrong_A}",
          str(platform.payloads[-1:]))
    n_calls = len(platform.payloads)

    # 先试同值（应被 duplicate 拦，不再打平台）
    text, accepted = sub("flag{wrong_A}")
    check("solver：同值 A 被拦（duplicate）", "之前已经提交过" in text, text[:200])
    check("solver：同值 A 没再打平台", len(platform.payloads) == n_calls, str(platform.payloads[n_calls:]))

    # 换新值 B → 放行（关键）
    text, accepted = sub("flag{fresh_B}")
    check("solver：换新值 B 放行（未误判上限）", "之前已经提交过" not in text and "上限" not in text,
          text[:200])
    check("solver：B 打到平台", len(platform.payloads) == n_calls + 1, str(platform.payloads[n_calls:]))
    check("solver：B 计入错交", store.require(CID).wrong_by_qid.get("flag") == 2,
          str(store.require(CID).wrong_by_qid))

    # 模拟第二轮：重载 store（跨轮），换新值仍能用掉剩余额度
    store3 = Store(tmp / "solver").load()
    solver3 = Solver(cfg, platform, DummyLLM(), store3)
    text, accepted = solver3._submit({"flag": "flag{fresh_C}", "question_id": "flag"}, CID)
    # 注意：C 是第三错 → wrong_by_qid 到 3，额度用尽；平台 receipts 还剩
    check("solver：重载后换新值 C 仍放行", "上限" not in text and "之前已经提交过" not in text, text[:200])
    check("solver：C 用尽该题号额度", store3.require(CID).wrong_by_qid.get("flag") == 3,
          str(store3.require(CID).wrong_by_qid))
    before = len(platform.payloads)
    text, accepted = solver3._submit({"flag": "flag{fresh_D}", "question_id": "flag"}, CID)
    check("solver：该题号满额后换新值被拦", "上限" in text, text[:200])
    check("solver：满额后不再打平台", len(platform.payloads) == before, str(platform.payloads[before:]))
    # 换题号仍可交（独立额度）
    text, accepted = solver3._submit({"flag": "flag{slug_1}", "question_id": "slug"}, CID)
    check("solver：换题号仍可提交（独立额度）", "上限" not in text, text[:200])


def main() -> int:
    tmp = ROOT / "runs" / f"_t_a4round_{os.getpid()}"
    try:
        store_level(tmp)
        solver_level(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"smoke_a4_revalue_round 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_a4_revalue_round 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())