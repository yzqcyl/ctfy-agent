#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_rerun_recovery_flow.py — 重跑题一次 start_instance 里的「连环自愈」端到端回归（离线）。

复现 2026-11-11 本地 plat 第 5 轮 SAMPLE-003 的真实失败链（并验证三次修复能接起来）：
  1) 团队并发实例名额被前几轮已解出题的旧实例占满 → start 回
     `Rate limited by upstream API, please retry later`（瞬时重试 (5,15)s 后仍失败）；
  2) 按 state 回收 submitted/done 的题实例腾名额 → 重试 start；
  3) 平台又回墓碑 `[已在运行] …instance_id=旧id`，而 get_instance 报 instance_not_found
     → stop 旧 id → 重建一次 → 拿到新实例并登记。

钉住：这一条链走完要 ①真的回收旧实例、②最终登记的是**新**实例、③start 调用总数不超过
instance_ops_limit、④转录里两种 [实例守卫] 留痕都在、⑤全程不抛异常。

运行: python tests/smoke_rerun_recovery_flow.py
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
from ctfy_agent.tools_local import LocalTools  # noqa: E402

FAILS: list[str] = []
CID = "SAMPLE-003"
OLD_ID = "db384bfd-02a8-4db2-b81c-e4ce562c0b42"
NEW_ID = "511c79b0-3e5f-4ae6-9a55-1b2c3d4e5f60"
A_ID = "aaaaaaaa-1111-4111-8111-111111111111"
B_ID = "bbbbbbbb-2222-4222-8222-222222222222"
LIMIT = 5


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _instance_json(iid: str) -> str:
    return "----- 工具返回 -----\n" + json.dumps({
        "id": iid,
        "surface": {"services": [{"service_type": "https", "host": "sdxzeli3yx26oc4qr.node4.ctfy.run",
                                  "port": 443}], "vpn": None},
    }, ensure_ascii=False)


RATE_LIMITED = "Rate limited by upstream API, please retry later"


class FakePlatform:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.start_queue = [RATE_LIMITED, RATE_LIMITED, RATE_LIMITED,
                            "[已在运行] 本题实例已存在（instance_id=%s），无需重复启动。" % OLD_ID]
        self.alive: set[str] = set()
        self.tool_map = {
            "submit_flag": "submit_answer",
            "start_instance": "start_instance",
            "stop_instance": "stop_instance",
            "get_instance": "get_instance",
            "list_instances": "list_instances",
        }

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        return self.tool_map[kind]

    def model_tools(self):
        return [{"name": name} for name in self.tool_map.values()]

    def prop_names(self, name):
        if name == "start_instance":
            return {"challenge_id": {"type": "string"}, "competition_id": {"type": "string"}}
        if name in ("stop_instance", "get_instance"):
            return {"instance_id": {"type": "string"}}
        return {}

    def call_text(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "get_instance":
            iid = str(args.get("instance_id") or "")
            if iid in self.alive:
                return _instance_json(iid)
            return ('----- 工具返回 (isError=true) -----\n'
                    f'{{"code":"instance_not_found","message":"Instance {iid} not found"}}')
        if name == "list_instances":
            return '----- 工具返回 -----\n{"result": []}'
        if name == "stop_instance":
            self.alive.discard(str(args.get("instance_id") or ""))
            return '----- 工具返回 -----\n{"ok": true}'
        if name == "start_instance":
            if self.start_queue:
                item = self.start_queue.pop(0)
                if isinstance(item, dict):
                    self.alive.add(str(item.get("id")))
                    return "----- 工具返回 -----\n" + json.dumps(item, ensure_ascii=False)
                return str(item)
            self.alive.add(NEW_ID)
            return _instance_json(NEW_ID)
        return "----- 工具返回 -----\n{}"

    @staticmethod
    def _as_json(text):
        raw = str(text or "")
        if raw.startswith("-----"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        try:
            return json.loads(raw.strip())
        except ValueError:
            return None

    def my_solved_ids(self):
        return set()


def main() -> int:
    tmp = ROOT / "runs" / f"_t_flow_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    store = Store(tmp).load()
    store.challenges.clear()
    for cid, status, iid in (
        (CID, "stalled", OLD_ID),           # 本题：登记着墓碑 id
        ("solved-a", "submitted", A_ID),    # 已解出：实例该被回收
        ("done-b", "submitted", B_ID),
        ("running-c", "instance_up", "cccccccc-3333-4333-8333-333333333333"),  # 并行题：不能碰
    ):
        store.upsert({"id": cid, "name": cid, "category": "web", "description": "d", "points": 100})
        item = store.require(cid)
        item.status = status
        item.instance_id = iid
        item.instance = {"id": iid} if iid else {}
    store.challenges[CID].instance_id = OLD_ID
    store.save()

    class DummyLLM:
        budget = None

    cfg = Config()
    cfg.instance_ops_limit = LIMIT
    cfg.extra.setdefault("competition_id", "ground-1")
    platform = FakePlatform()
    solver = Solver(cfg, platform, DummyLLM(), store)

    import ctfy_agent.solver as solver_mod  # noqa: PLC0415
    real_sleep, solver_mod.time.sleep = solver_mod.time.sleep, lambda _s: None
    try:
        text, solved = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    finally:
        solver_mod.time.sleep = real_sleep

    starts = [c for c in platform.calls if c[0] == "start_instance"]
    stops = [c for c in platform.calls if c[0] == "stop_instance"]
    stopped_ids = [c[1].get("instance_id") for c in stops]
    notes = store.transcript_path(CID).read_text(encoding="utf-8").splitlines()

    check("不抛异常且未判解出", solved is False)
    check("回收了已解出题的 2 个实例", sorted([A_ID, B_ID]) == sorted(x for x in stopped_ids if x in (A_ID, B_ID)),
          str(stopped_ids))
    check("也 stop 了墓碑旧 id", OLD_ID in stopped_ids, str(stopped_ids))
    check("并行题实例没被碰", "cccccccc-3333-4333-8333-333333333333" not in stopped_ids, str(stopped_ids))
    check("最终登记新实例", store.require(CID).instance_id == NEW_ID, store.require(CID).instance_id)
    check("回执含新实例", NEW_ID in text, text[:240])
    check("start 调用总数不超配额", len(starts) <= LIMIT, str(len(starts)))
    check("本地配额只算 2 次 start", store.call_count(CID, "start_instance") == 2,
          str(store.call_count(CID, "start_instance")))
    check("转录含「回收已解出题旧实例」留痕", any("已解出题的旧实例" in line for line in notes))
    check("转录含「墓碑重建」留痕", any("已被回收" in line for line in notes))

    print()
    if FAILS:
        print(f"smoke_rerun_recovery_flow 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("smoke_rerun_recovery_flow 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())