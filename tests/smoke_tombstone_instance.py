#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_tombstone_instance.py — 「平台回执说已在运行、实例其实已被回收」的自愈回归（离线、不连平台）。

背景（2026-11-11 本地 plat 第 5 轮实测，SAMPLE-019 / SAMPLE-016）：
  - 实例早被平台回收，`get_instance` 明确报 `instance_not_found`，端点只剩平台边缘的
    Go 式 404；
  - 但 `start_instance` 永远回 `[已在运行] 本题实例已存在（instance_id=…）`（墓碑回执）；
  - 旧实现里还有一层本地幂等短路：只要本地登记了 instance_id，模型再调 start 就直接回
    「已在运行」，连平台都不问 —— 于是死 id 永远挡住重建，整题 stalled。

本文件钉住：
  1) 平台**明确说实例不存在**（get_instance 报 not_found）时，本地短路放行 → 真 start，
     且 start 回执仍是墓碑时自动 stop 旧 id + 重新 start，登记新 id；
  2) 实例**真的活着**时，本地短路/零平台 start 调用的旧行为不变（不多烧配额）；
  3) 平台没有 get_instance 工具时维持旧行为（无从判断就不动）；
  4) 重建那一次 start 也计入 instance_ops_limit（配额烧不穿，用尽时只收口）。

运行: python tests/smoke_tombstone_instance.py
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
from ctfy_agent.state import ALLOWED, Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools  # noqa: E402

FAILS: list[str] = []
CID = "chall-tombstone"
OLD_ID = "db384bfd-02a8-4db2-b81c-e4ce562c0b42"
NEW_ID = "4107fb55-fe11-491f-8ead-7984b3ec3fac"
HOST = "seajrkeuhlbb65f33.node4.ctfy.run"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _instance_json(iid: str) -> str:
    return "----- 工具返回 -----\n" + json.dumps({
        "id": iid,
        "surface": {"services": [{"service_type": "https", "host": HOST, "port": 443}], "vpn": None},
    }, ensure_ascii=False)


class FakePlatform:
    """只实现 _execute / 探活会碰到的能力面。alive=实例是否真的在平台上存在。"""

    def __init__(self, *, alive=None, start_queue=None, with_get_instance=True):
        self.calls: list[tuple[str, dict]] = []
        self.alive = set(alive or ())
        self.start_queue = list(start_queue or [])
        self.tool_map = {
            "submit_flag": "submit_answer",
            "start_instance": "start_instance",
            "stop_instance": "stop_instance",
            "verify_exploit": "verify_exploit",
            "verify_answer": "verify_answer",
        }
        if with_get_instance:
            self.tool_map["get_instance"] = "get_instance"

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
        if name == "start_instance":
            if self.start_queue:
                item = self.start_queue.pop(0)
                if isinstance(item, dict):
                    self.alive.add(str(item.get("id")))
                    return "----- 工具返回 -----\n" + json.dumps(item, ensure_ascii=False)
                return str(item)
            self.alive.add(NEW_ID)
            return _instance_json(NEW_ID)
        if name == "stop_instance":
            self.alive.discard(str(args.get("instance_id") or ""))
            return '----- 工具返回 -----\n{"ok": true}'
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


def _config(*, limit: int = 5) -> Config:
    cfg = Config()
    cfg.instance_ops_limit = limit
    cfg.extra.setdefault("competition_id", "ground-1")
    return cfg


def _store(root: pathlib.Path, *, instance_id: str) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({"id": CID, "name": "tomb", "category": "web", "description": "d", "points": 100})
    item = store.require(CID)
    if instance_id:
        item.instance_id = instance_id
        item.instance = {"id": instance_id, "surface": {"services": [{"service_type": "https", "host": HOST, "port": 443}]}}
        if "instance_up" in ALLOWED.get(item.status, set()):
            store.set_status(CID, "instance_up")
    store.save()
    return store


def _solver(store: Store, platform: FakePlatform, *, limit: int = 5) -> Solver:
    class DummyLLM:
        budget = None

    return Solver(_config(limit=limit), platform, DummyLLM(), store)


def main() -> int:
    tmp = ROOT / "runs" / f"_t_tomb_{os.getpid()}"
    start_calls = lambda p: [c for c in p.calls if c[0] == "start_instance"]  # noqa: E731

    # ---- 1) 回归：实例真活着 → 本地短路，不打扰平台、不多烧配额 ----
    store = _store(tmp / "live", instance_id=OLD_ID)
    platform = FakePlatform(alive=[OLD_ID])
    solver = _solver(store, platform)
    text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    check("活实例：仍回「已在运行」", "[已在运行]" in text, text[:120])
    check("活实例：未真的调 start", not start_calls(platform), str(platform.calls))
    check("活实例：登记不变", store.require(CID).instance_id == OLD_ID)

    # ---- 2) 回归：平台没有 get_instance 工具 → 维持旧行为（无从判断就不动）----
    store = _store(tmp / "noquery", instance_id=OLD_ID)
    platform = FakePlatform(alive=[], with_get_instance=False)
    solver = _solver(store, platform)
    text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    check("无探活工具：维持「已在运行」短路", "[已在运行]" in text and not start_calls(platform))

    # ---- 3) 墓碑：本地登记的死 id 不再挡住重建（探活 absent → 放行 + 自动 stop/start）----
    store = _store(tmp / "tomb", instance_id=OLD_ID)
    platform = FakePlatform(
        alive=[],  # OLD_ID 在平台上已经不存在
        start_queue=["[已在运行] 本题实例已存在（instance_id=%s），无需重复启动。" % OLD_ID],
    )
    solver = _solver(store, platform)
    text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    check("墓碑：真的向平台调了 start", len(start_calls(platform)) >= 1, str(platform.calls))
    check("墓碑：先探活再 stop 旧 id", ("get_instance", {"instance_id": OLD_ID}) in platform.calls
          and ("stop_instance", {"instance_id": OLD_ID}) in platform.calls, str(platform.calls))
    check("墓碑：最终 start 了两次（原回执 + 重建）", len(start_calls(platform)) == 2, str(start_calls(platform)))
    check("墓碑：登记换成新实例", store.require(CID).instance_id == NEW_ID, store.require(CID).instance_id)
    check("墓碑：回执带新 UUID", NEW_ID in text, text[:200])
    check("墓碑：回执带确定性说明", "[实例守卫]" in text, text[:200])
    notes = [line for line in store.transcript_path(CID).read_text(encoding="utf-8").splitlines()
             if "实例守卫" in line]
    check("墓碑：转录留痕", len(notes) >= 1, str(notes[:1]))

    # ---- 4) 配额：重建那次 start 也计数（limit=2，已用 1 → 原调用用掉第 2 次 → 重建被拦）----
    store = _store(tmp / "quota", instance_id=OLD_ID)
    store.record_call(CID, "start_instance", 2)
    platform = FakePlatform(
        alive=[],
        start_queue=["[已在运行] 本题实例已存在（instance_id=%s），无需重复启动。" % OLD_ID],
    )
    solver = _solver(store, platform, limit=2)
    text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    check("配额：重建被明确拒绝并收口", "配额已用尽" in text, text[:240])
    check("配额：没有再发第二次 start", len(start_calls(platform)) == 1, str(start_calls(platform)))

    # ---- 5) 纯函数：UUID 抠取 ----
    solver = _solver(_store(tmp / "uuid", instance_id=""), FakePlatform())
    check("UUID：从「已在运行」回执抠出 id",
          solver._instance_id_in_text("[已在运行] 本题实例已存在（instance_id=%s）" % OLD_ID) == OLD_ID)
    check("UUID：无 UUID → 空串", solver._instance_id_in_text("Rate limited by upstream API") == "")

    print()
    if FAILS:
        print(f"smoke_tombstone_instance 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("smoke_tombstone_instance 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())