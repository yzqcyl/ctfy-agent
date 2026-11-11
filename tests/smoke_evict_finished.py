#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_evict_finished.py — 团队并发实例名额被「已解出题的旧实例」占满时按 state 回收（离线）。

背景（2026-11-11 本地 plat 第 5 轮实测，SAMPLE-003）：start_instance 回
`Rate limited by upstream API, please retry later`；mock 的 `list_instances` 又返回空，
模型无法定位占用者（前几轮遗留的实例大多属于**已经解出**的题），只能收口 → 整题 stalled。

修法：`Solver._evict_finished_instances()` 按 state 里各题登记的 instance_id 回收
**状态为 submitted/done** 的题的实例（不碰并行中的兄弟题），回收后重试一次 start；
回收动作计入转录，重试那次 start 仍受 instance_ops_limit 约束。

钉住：
  1) 只停 submitted/done 的实例，instance_up / stalled 的一律不动（不误杀并行题）；
  2) 回收后 start 重试被真正发出，且最终拿到新实例并登记；
  3) 没有 stop_instance 工具 / 没有可回收实例时，维持旧行为（只追加说明）；
  4) 纯函数 _is_start_rate_limited 命中本地 plat 的限流文案。

运行: python tests/smoke_evict_finished.py
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
CID = "SAMPLE-CUR"
A_ID = "aaaaaaaa-0000-4000-8000-000000000001"
B_ID = "bbbbbbbb-0000-4000-8000-000000000002"
C_ID = "cccccccc-0000-4000-8000-000000000003"
D_ID = "dddddddd-0000-4000-8000-000000000004"
NEW_ID = "eeeeeeee-0000-4000-8000-000000000005"
HOST = "freshhost.node4.ctfy.run"


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
    def __init__(self, *, start_queue=None, with_stop=True):
        self.calls: list[tuple[str, dict]] = []
        self.start_queue = list(start_queue or [])
        self.tool_map = {
            "submit_flag": "submit_answer",
            "start_instance": "start_instance",
            "list_instances": "list_instances",
        }
        if with_stop:
            self.tool_map["stop_instance"] = "stop_instance"

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        return self.tool_map[kind]

    def model_tools(self):
        return [{"name": name} for name in self.tool_map.values()]

    def prop_names(self, name):
        if name == "start_instance":
            return {"challenge_id": {"type": "string"}, "competition_id": {"type": "string"}}
        if name == "stop_instance":
            return {"instance_id": {"type": "string"}}
        return {}

    def call_text(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "list_instances":
            return '----- 工具返回 -----\n{"result": []}'
        if name == "stop_instance":
            return '----- 工具返回 -----\n{"ok": true}'
        if name == "start_instance":
            if self.start_queue:
                item = self.start_queue.pop(0)
                if isinstance(item, dict):
                    return "----- 工具返回 -----\n" + json.dumps(item, ensure_ascii=False)
                return str(item)
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


def _config() -> Config:
    cfg = Config()
    cfg.instance_ops_limit = 5
    cfg.extra.setdefault("competition_id", "ground-1")
    return cfg


def _store(root: pathlib.Path) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    rows = [
        (CID, "new", ""),
        ("solved-a", "submitted", A_ID),
        ("done-b", "submitted", B_ID),
        ("running-c", "instance_up", C_ID),
        ("stalled-d", "stalled", D_ID),
    ]
    for cid, status, iid in rows:
        store.upsert({"id": cid, "name": cid, "category": "web", "description": "d", "points": 100})
        item = store.require(cid)
        item.status = status
        item.instance_id = iid
        item.instance = {"id": iid} if iid else {}
    store.save()
    return store


def _solver(store: Store, platform: FakePlatform) -> Solver:
    class DummyLLM:
        budget = None

    return Solver(_config(), platform, DummyLLM(), store)


def main() -> int:
    tmp = ROOT / "runs" / f"_t_evict_{os.getpid()}"
    stop_calls = lambda p: [c for c in p.calls if c[0] == "stop_instance"]  # noqa: E731
    start_calls = lambda p: [c for c in p.calls if c[0] == "start_instance"]  # noqa: E731

    # ---- 1) 纯函数：本地 plat 的限流文案要命中 ----
    check("限流文案：Rate limited by upstream",
          Solver._is_start_rate_limited("Rate limited by upstream API, please retry later") is True)
    check("限流文案：429 team_capacity_exceeded",
          Solver._is_start_rate_limited("429 team_capacity_exceeded") is True)
    check("限流文案：普通报错不误判",
          Solver._is_start_rate_limited("instance_not_found: Instance x not found") is False)

    # ---- 2) 单元：只回收 submitted/done 的实例 ----
    store = _store(tmp / "unit")
    platform = FakePlatform()
    solver = _solver(store, platform)
    freed = solver._evict_finished_instances(CID)
    stopped_ids = {c[1].get("instance_id") for c in stop_calls(platform)}
    check("回收数量 = 2 个已解出题", freed == 2, str(freed))
    check("只停 solved-a / done-b", stopped_ids == {A_ID, B_ID}, str(stopped_ids))
    check("submitted 题登记已清空",
          store.require("solved-a").instance_id == "" and store.require("done-b").instance_id == "",
          f"{store.require('solved-a').instance_id!r}/{store.require('done-b').instance_id!r}")
    check("instance_up 的并行题不动（不误杀）", store.require("running-c").instance_id == C_ID)
    check("stalled 的题不动", store.require("stalled-d").instance_id == D_ID)
    check("当前题不动", store.require(CID).instance_id == "")
    check("没有 stop 工具时回收 0 个",
          _solver(_store(tmp / "nostop"), FakePlatform(with_stop=False))._evict_finished_instances(CID) == 0)

    # ---- 3) 端到端：团队配额被占满 → 回收已解出题的实例 → 重试 start 成功 ----
    store = _store(tmp / "e2e")
    platform = FakePlatform(start_queue=[
        "Rate limited by upstream API, please retry later",
        "Rate limited by upstream API, please retry later",
        "Rate limited by upstream API, please retry later",
    ])
    solver = _solver(store, platform)
    # 瞬时失败重试里有 (5,15)s 的退避等待；测试里把它变成 0s，只为让断言跑得快。
    import ctfy_agent.solver as solver_mod  # noqa: PLC0415
    real_sleep, solver_mod.time.sleep = solver_mod.time.sleep, lambda _s: None
    try:
        text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    finally:
        solver_mod.time.sleep = real_sleep
    check("端到端：回收了 2 个旧实例", sorted(c[1].get("instance_id") for c in stop_calls(platform))
          == sorted([A_ID, B_ID]), str(stop_calls(platform)))
    check("端到端：start 重试被真正发出", len(start_calls(platform)) >= 2, str(len(start_calls(platform))))
    check("端到端：最终登记新实例", store.require(CID).instance_id == NEW_ID, store.require(CID).instance_id)
    check("端到端：回执含新实例", NEW_ID in text, text[:200])
    check("端到端：转录有实例守卫留痕",
          any("实例守卫" in line for line in
              store.transcript_path(CID).read_text(encoding="utf-8").splitlines()))
    check("端到端：重试后配额只算 1 次", store.call_count(CID, "start_instance") == 1,
          str(store.call_count(CID, "start_instance")))

    # ---- 4) 端到端：没有可回收实例时只追加说明，不瞎重试 ----
    store = _store(tmp / "nothing")
    store.challenges.clear()
    store.upsert({"id": CID, "name": "x", "category": "web", "description": "d", "points": 100})
    store.save()
    waiting = "Rate limited by upstream API, please retry later"
    platform = FakePlatform(start_queue=[waiting, waiting, waiting, waiting, waiting])
    solver = _solver(store, platform)
    real_sleep, solver_mod.time.sleep = solver_mod.time.sleep, lambda _s: None
    try:
        text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    finally:
        solver_mod.time.sleep = real_sleep
    check("无残留：不误杀", len(stop_calls(platform)) == 0, str(stop_calls(platform)))
    check("无残留：等一个窗口后重试了一次", len(start_calls(platform)) == 4, str(len(start_calls(platform))))
    check("无残留：给模型确定性说明", "团队并发实例" in text, text[:200])

    # ---- 5) 端到端：没有可回收实例，但等一个窗口后重试就拿到实例（SAMPLE-003 的真实场景）----
    store = _store(tmp / "waitwin")
    store.challenges.clear()
    store.upsert({"id": CID, "name": "x", "category": "web", "description": "d", "points": 100})
    store.save()
    platform = FakePlatform(start_queue=[waiting, waiting, waiting])
    solver = _solver(store, platform)
    real_sleep, solver_mod.time.sleep = solver_mod.time.sleep, lambda _s: None
    try:
        text, _ = solver._execute("start_instance", {"challenge_id": CID}, LocalTools(store, CID), CID)
    finally:
        solver_mod.time.sleep = real_sleep
    check("等窗口重试：拿到实例并登记", store.require(CID).instance_id == NEW_ID, store.require(CID).instance_id)
    check("等窗口重试：回执含新实例", NEW_ID in text, text[:200])
    check("等窗口重试：配额只算 1 次", store.call_count(CID, "start_instance") == 1,
          str(store.call_count(CID, "start_instance")))

    print()
    if FAILS:
        print(f"smoke_evict_finished 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("smoke_evict_finished 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())