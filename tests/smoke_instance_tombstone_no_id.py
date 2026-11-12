#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_instance_tombstone_no_id.py — 没有实例 id 的平台（ctf2）的墓碑自愈回归（离线）。

背景（HANDOFF 2026-11-10 §8 P2）：DASCTF ctf2 的 start 回执只有 `access_url` / `environment_id`，
**没有 instance_id**；本地 state 的 instance_id 也是空。平台回「已在运行」但端点已失效时，
`solver._maybe_rebuild_tombstone` 用 `_instance_id_in_text()` 抠不到 UUID → 直接原样返回，
自愈永远不触发，模型只能拿旧端点反复 404。

修法：没有 id 时改用平台唯一可用的**按题原语**（ctf2_get_environment / 按题删环境）：
  1) 环境仍可用 → 不删不重启（删了等于白扔一次 start 配额），把端点补进本地；
  2) 环境明确不存在 → 按 challenge_id 回收一次 → 重新 start 一次（计入题配额）；
  3) 状态不明 / 平台 get_instance 只认 instance_id → 什么都不做（不乱删）；
  4) 配额用尽 → 不重建，直接给收口提示；
  5) 有 UUID 的平台走原路径（行为不变）。

运行: python tests/smoke_instance_tombstone_no_id.py
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
CID = "SAMPLE-TOMB"

ALREADY_TEXT = ('----- 工具返回 -----\n'
                '[已在运行] 本题实例已存在（instance_id=）')  # 关键：没有 UUID
NEW_ACCESS = "new-node.tcp-ctf2.dasctf.com:9999"
OLD_ACCESS = "old-node.tcp-ctf2.dasctf.com:9999"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


class FakePlatform:
    """按 ctf2 能力面建模：get_instance/stop_instance 都是**按 challenge_id** 的原语。"""

    def __init__(self, *, env=None, start_replies=(), challenge_keyed=True):
        self.env = env if env is not None else {"status": "none"}
        self.start_replies = list(start_replies) or [{"access_ready": True, "access_url": NEW_ACCESS}]
        self.challenge_keyed = challenge_keyed
        self.calls: list[tuple[str, dict]] = []
        self.tool_map = {"start_instance": "start_instance"}
        if challenge_keyed:
            self.tool_map["get_instance"] = "ctf2_get_environment"
            self.tool_map["stop_instance"] = "ctf2_delete_environment"
        else:
            self.tool_map["get_instance"] = "get_instance"
            self.tool_map["stop_instance"] = "stop_instance"
        self.tool_map["submit_flag"] = "submit_answer"

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        if kind not in self.tool_map:
            from ctfy_agent.platform import PlatformError  # noqa: PLC0415

            raise PlatformError(kind)
        return self.tool_map[kind]

    def prop_names(self, name):
        if name in ("ctf2_get_environment", "ctf2_delete_environment"):
            return {"challenge_id", "practice_ground_id"}
        if name == "get_instance":
            return {"instance_id"}  # 只认 UUID 的平台
        if name == "start_instance":
            return {"challenge_id", "competition_id"}
        return {"challenge_id", "flag"}

    def model_tools(self):
        return [{"name": name} for name in self.tool_map.values()]

    def call_text(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "ctf2_get_environment":
            return "----- 工具返回 -----\n" + json.dumps({"environment": self.env})
        if name == "ctf2_delete_environment":
            return "----- 工具返回 -----\n{}"
        if name == "start_instance":
            reply = self.start_replies.pop(0) if self.start_replies else {"access_ready": True,
                                                                          "access_url": NEW_ACCESS}
            return "----- 工具返回 -----\n" + json.dumps(reply)
        return "----- 工具返回 -----\n{}"

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


def _config(**extra) -> Config:
    cfg = Config()
    cfg.instance_ops_limit = 5
    cfg.extra["competition_id"] = "ground-1"
    cfg.extra.update(extra)
    return cfg


def _store(root: pathlib.Path, *, with_old_instance: bool = True) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({"id": CID, "name": "tomb", "category": "MISC", "description": "d"})
    item = store.require(CID)
    if with_old_instance:
        # ctf2 形态：只有 access_url，没有 instance_id
        item.instance = {"access_ready": True, "access_url": OLD_ACCESS}
    store.save()
    return store


def _solver(store: Store, platform: FakePlatform) -> Solver:
    return Solver(_config(), platform, DummyLLM(), store)


def _names(platform) -> list[str]:
    return [name for name, _ in platform.calls]


def main() -> int:
    tmp = ROOT / "runs" / f"_t_tomb_{os.getpid()}"

    # ---- 0) 前置：确实没有实例 id（否则这条路径没有意义）----
    store = _store(tmp / "pre")
    check("前置：ctf2 形态实例无 instance_id", store.require(CID).instance_id == "")

    # ---- 1) 环境明确不存在 → 按题回收一次 + 重新 start ----
    store = _store(tmp / "rebuild")
    platform = FakePlatform(env={"status": "none"})
    solver = _solver(store, platform)
    text = solver._maybe_rebuild_tombstone(CID, "start_instance", {"challenge_id": CID}, ALREADY_TEXT)
    check("自愈：按 challenge_id 查了环境", "ctf2_get_environment" in _names(platform), str(_names(platform)))
    check("自愈：按题回收了旧环境", "ctf2_delete_environment" in _names(platform), str(_names(platform)))
    check("自愈：重新 start 了一次", _names(platform).count("start_instance") == 1, str(_names(platform)))
    check("自愈：新端点已落库", store.require(CID).instance.get("access_url") == NEW_ACCESS,
          str(store.require(CID).instance))
    check("自愈：回执说明按 challenge_id 回收重建", "旧环境已按 challenge_id 回收" in text, text[:400])
    check("自愈：转录留痕", "按 challenge_id 回收旧环境" in
          store.transcript_path(CID).read_text(encoding="utf-8"))

    # ---- 2) 环境仍可用 → 不删不重启，只把端点补进本地 ----
    store = _store(tmp / "live")
    platform = FakePlatform(env={"status": "running", "access_ready": True,
                                 "access_url": "live-node.tcp-ctf2.dasctf.com:9999"})
    solver = _solver(store, platform)
    text = solver._maybe_rebuild_tombstone(CID, "start_instance", {"challenge_id": CID}, ALREADY_TEXT)
    check("复用：没有删除环境", "ctf2_delete_environment" not in _names(platform), str(_names(platform)))
    check("复用：没有重新 start", "start_instance" not in _names(platform), str(_names(platform)))
    check("复用：端点回灌本地", store.require(CID).instance.get("access_url")
          == "live-node.tcp-ctf2.dasctf.com:9999", str(store.require(CID).instance))
    check("复用：回执明确「仍可用、不要重建」", "仍可用" in text and "不要 stop/重建" in text, text[-300:])

    # ---- 3) 状态不明 → 什么都不做（不乱删）----
    store = _store(tmp / "unknown")
    platform = FakePlatform(env={})
    solver = _solver(store, platform)
    text = solver._maybe_rebuild_tombstone(CID, "start_instance", {"challenge_id": CID}, ALREADY_TEXT)
    check("未知：没有删除、没有 start",
          "ctf2_delete_environment" not in _names(platform) and "start_instance" not in _names(platform),
          str(_names(platform)))
    check("未知：回执原样保留", text == ALREADY_TEXT, text[:200])

    # ---- 4) 实例操作配额用尽 → 不重建，给收口提示 ----
    store = _store(tmp / "quota")
    for _ in range(4):
        store.record_call(CID, "start_instance", 5)  # 第 5 次留给本次 start 之前的记账
    platform = FakePlatform(env={"status": "none"})
    solver = _solver(store, platform)
    solver.store.record_call(CID, "start_instance", 5)  # 用满 5 次
    text = solver._maybe_rebuild_tombstone(CID, "start_instance", {"challenge_id": CID}, ALREADY_TEXT)
    check("配额：没有重新 start", "start_instance" not in _names(platform), str(_names(platform)))
    check("配额：回执提示配额用尽/收口", "配额用尽" in text and "report/done" in text, text[-300:])

    # ---- 5) 只认 instance_id 的平台 → 维持旧行为（不乱调按题原语）----
    store = _store(tmp / "uuid-platform")
    platform = FakePlatform(env={"status": "none"}, challenge_keyed=False)
    solver = _solver(store, platform)
    text = solver._maybe_rebuild_tombstone(CID, "start_instance", {"challenge_id": CID}, ALREADY_TEXT)
    check("UUID 平台：原样返回", text == ALREADY_TEXT, text[:200])
    check("UUID 平台：没有误调按题原语", platform.calls == [], str(platform.calls))

    # ---- 6) 端到端：_execute 的 start 回执走完整自愈链 ----
    from ctfy_agent.tools_local import LocalTools  # noqa: PLC0415

    store = _store(tmp / "e2e")
    platform = FakePlatform(env={"status": "none"})
    platform.start_replies = []

    class StartThenRebuild(FakePlatform):
        """第一次 start 回「已在运行」（墓碑），重建那次回真端点。"""

        def call_text(self, name, arguments=None, *, retries=0):
            if name == "start_instance":
                self.calls.append((name, dict(arguments or {})))
                if not getattr(self, "_first_done", False):
                    self._first_done = True
                    return ALREADY_TEXT
                return "----- 工具返回 -----\n" + json.dumps(
                    {"access_ready": True, "access_url": NEW_ACCESS})
            return super().call_text(name, arguments, retries=retries)

    platform = StartThenRebuild(env={"status": "none"})
    solver = _solver(store, platform)
    local = LocalTools(store, CID)
    text, _ = solver._execute("start_instance", {"challenge_id": CID}, local, CID)
    check("端到端：回执带自愈说明", "实例守卫" in text, text[:400])
    check("端到端：本地换成新端点", store.require(CID).instance.get("access_url") == NEW_ACCESS,
          str(store.require(CID).instance))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"smoke_instance_tombstone_no_id 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_instance_tombstone_no_id 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())