#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_instance_recovery.py — 实例配额自愈的离线回归（不连平台、不花钱）。

背景（2026-11-11 只读实测）：DASCTF ctf2 **没有 list_instances**，`instance_guard.reap()`
和 `solver._find_live_instance()` 在这条平台上都是空转；它只有两个按题原语：
  - `ctf2_get_environment(practice_ground_id, challenge_id)` → {"environment":{"status":"none"}}
  - `ctf2_user_delete_…_environment(practice_ground_id, challenge_id)`（按题删环境）
全队共享 1 个环境配额，旧环境占位时 start 只报 agent_environment_limit，旧逻辑直接放弃，
模型会反复 start 把每题 5 次硬上限烧光。

本文件钉住新逻辑（solver._recover_instance_slot）：
  1) 环境仍可用        → 直接复用，**不**调 start、**不**删；
  2) 环境明确不存在    → 按题删一次 → 再 start 一次（成功即登记实例）；
  3) 环境状态不明/查不到 → 什么都不做（不乱删）；
  4) start 配额剩 <2    → 不做重试（避免当天作废）；
  5) instance_guard 的三个按题原语 + supports_reap 判定；
  6) GUI 开跑前清扫在「无 list_instances」平台给出确定性提示。

运行: python tests/smoke_instance_recovery.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import Config  # noqa: E402
from ctfy_agent.instance_guard import (  # noqa: E402
    delete_environment,
    environment_absent,
    environment_live,
    query_environment,
    supports_reap,
)
from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []

# 「槽位/配额」类 start 失败（值得回收重试）——真实回执里的 agent_environment_limit 语义
LIMIT_FAIL = "agent_environment_limit: practice and competition environments share one quota"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


# ---------------------------------------------------------------- 假平台
class FakePlatform:
    """按 DASCTF ctf2 的能力面建模：没有 list_instances，只有按题查/删 + start。

    重要细节（2026-11-11 真实回执）：ctf2 的工具 schema 是 `additionalProperties: false`，
    只认 `practice_ground_id` / `challenge_id` / `limit`。硬塞 `competition_id` 会被平台拒绝，
    查询恒失败 → 自愈静默失效（这是第一版实现踩到的真 bug，本假平台会主动断言参数集合）。
    """

    ALLOWED = {"challenge_id", "practice_ground_id", "limit"}

    def __init__(self, *, env=None, start_results=(), delete_ok=True, allow_competition_id=False,
                 delete_soft_error=False):
        self._env = env                        # dict | None；None 表示查询失败
        self._starts = list(start_results)     # 每次 start 的结果（str=失败文本 / dict=成功）
        self._delete_ok = delete_ok
        self._allow_comp = allow_competition_id
        self._delete_soft = delete_soft_error   # 模拟「回执里 isError=true 但不抛异常」
        self.calls: list[tuple[str, dict]] = []
        self.tool_map = {
            "get_instance": "ctf2_get_environment",
            "stop_instance": "ctf2_user_delete_practice_id_challenges_challengeid_environment",
        }

    # --- 语义动作 ---
    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        tool = self.tool_map.get(kind)
        if not tool:
            raise KeyError(kind)
        return tool

    def schema_of(self, name):
        if self._allow_comp:  # CTFY 风格：按 instance_id/challenge_id + competition_id
            props = {"challenge_id": {"type": "string"}, "competition_id": {"type": "string"}}
            required = ["challenge_id", "competition_id"]
        else:                 # DASCTF ctf2 风格：practice_ground_id 必填
            props = {k: {"type": "string"} for k in self.ALLOWED}
            required = ["challenge_id", "practice_ground_id"]
        return {"type": "object", "properties": props,
                "required": required, "additionalProperties": False}

    def call_text(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        schema = self.schema_of(name)
        unknown = set(args) - set(schema["properties"])
        if unknown:
            raise AssertionError(f"{name} 收到 schema 之外的参数 {sorted(unknown)}（平台会拒绝）")
        missing = [k for k in schema["required"] if k not in args]
        if missing:
            raise AssertionError(f"{name} 缺少必填参数 {missing}（服务端会报缺少必填参数）")
        self.calls.append((name, args))
        if name == "ctf2_get_environment":
            if self._env is None:
                raise RuntimeError("upstream 查询失败")
            return "----- 工具返回 -----\n" + __import__("json").dumps({"environment": self._env})
        if name.startswith("ctf2_user_delete"):
            if self._delete_soft:
                # 真实平台行为：工具级错误包在正常回执文本里，call_text 不抛异常
                return ('----- 工具返回 (isError=true) -----\n'
                        '{"code":"NOT_FOUND","message":"The requested resource was not found.",'
                        '"retryable":false}')
            if not self._delete_ok:
                raise RuntimeError("delete 失败")
            return "----- 工具返回 -----\n" + __import__("json").dumps({"deleted": True})
        raise AssertionError(f"unexpected tool {name}")

    @staticmethod
    def _as_json(text):
        import json

        raw = str(text or "")
        if raw.startswith("-----"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.strip()
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def start_instance(self, challenge_id, competition_id=None):
        self.calls.append(("start_instance", {"challenge_id": challenge_id}))
        if not self._starts:
            return "agent_environment_limit: practice and competition environments share one quota"
        return self._starts.pop(0)

    def stop_instance(self, instance_id):
        raise AssertionError("不应在按题回收路径上调 stop_instance(instance_id)")


def _config(**extra) -> Config:
    cfg = Config()
    cfg.instance_ops_limit = 5
    cfg.extra.update(extra)
    cfg.extra.setdefault("competition_id", "ground-1")
    return cfg


def _store(root: pathlib.Path, cid: str):
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({
        "id": cid, "name": "inst-probe", "category": "pwn",
        "description": "d", "points": 100,
    })
    store.save()
    return store


def _solver(store, platform, cfg) -> tuple[Solver, list]:
    events: list = []

    class DummyLLM:
        budget = None

    solver = Solver(cfg, platform, DummyLLM(), store,
                    on_event=lambda kind, payload=None: events.append((kind, payload or {})))
    return solver, events


def main() -> int:
    tmp = ROOT / "runs" / f"_t_instrec_{os.getpid()}"
    cid = "chall-inst-1"

    # ---- 0) 纯函数口径 ----
    check("supports_reap：无 list_instances 为 False", supports_reap(FakePlatform()) is False)
    check("environment_live：status=none 不算活", environment_live({"status": "none"}) is False)
    check("environment_live：access_ready 算活", environment_live({"status": "creating",
                                                                   "access_ready": True}) is True)
    check("environment_live：running 算活", environment_live({"status": "running"}) is True)
    check("environment_absent：none 算明确不存在", environment_absent({"status": "none"}) is True)
    check("environment_absent：查不到不算不存在", environment_absent({}) is False)

    # ---- 1) 环境仍可用 → 复用，不 start 不删 ----
    plat = FakePlatform(env={"status": "running", "access_ready": True, "id": "env-1"})
    store = _store(tmp / "r1", cid)
    solver, events = _solver(store, plat, _config())
    info = solver._recover_instance_slot(cid, "start_instance", LIMIT_FAIL)
    names = [n for n, _ in plat.calls]
    check("环境可用时直接复用", isinstance(info, dict) and info.get("status") == "running", str(info))
    check("复用时没有调 start", "start_instance" not in names, str(names))
    check("复用时没有调 delete", not any(n.startswith("ctf2_user_delete") for n in names), str(names))
    check("复用有事件说明", any("复用" in str(p.get("note")) for _, p in events), str(events))

    # ---- 2) 环境明确不存在 → 删一次 → 重试 start 成功 ----
    plat = FakePlatform(env={"status": "none", "access_ready": False},
                        start_results=[{"id": "env-2", "host": "127.0.0.1", "port": 1234,
                                        "access_ready": True}])
    store = _store(tmp / "r2", cid)
    solver, events = _solver(store, plat, _config())
    info = solver._recover_instance_slot(cid, "start_instance", LIMIT_FAIL)
    names = [n for n, _ in plat.calls]
    order = [n for n in names if n in ("ctf2_get_environment", "start_instance")
             or n.startswith("ctf2_user_delete")]
    check("环境不存在时先查后删再起",
          order == ["ctf2_get_environment",
                    "ctf2_user_delete_practice_id_challenges_challengeid_environment",
                    "start_instance"], str(order))
    check("删完重试 start 成功", isinstance(info, dict) and info.get("id") == "env-2", str(info))
    check("回收有事件说明", any("回收" in str(p.get("note")) for _, p in events), str(events))

    # ---- 3) 环境状态不明（查询失败）→ 不删、不重试 ----
    plat = FakePlatform(env=None)
    store = _store(tmp / "r3", cid)
    solver, _ = _solver(store, plat, _config())
    info = solver._recover_instance_slot(cid, "start_instance", LIMIT_FAIL)
    names = [n for n, _ in plat.calls]
    check("查询失败时不乱删", not any(n.startswith("ctf2_user_delete") for n in names), str(names))
    check("查询失败时不重试 start", "start_instance" not in names, str(names))
    check("查询失败返回 None（维持旧行为）", info is None, str(info))

    # ---- 4) start 配额剩 <2 → 不动作 ----
    plat = FakePlatform(env={"status": "none"})
    store = _store(tmp / "r4", cid)
    # 先把配额烧到只剩 1 次
    for _ in range(4):
        store.record_call(cid, "start_instance", 5)
    solver, events = _solver(store, plat, _config())
    info = solver._recover_instance_slot(cid, "start_instance", LIMIT_FAIL)
    check("配额仅剩 1 次时放弃重试", info is None, str(info))
    check("配额不足时一个平台调用都不发", plat.calls == [], str(plat.calls))

    # ---- 5) delete 失败 → 不重试 ----
    plat = FakePlatform(env={"status": "none"}, delete_ok=False)
    store = _store(tmp / "r5", cid)
    solver, _ = _solver(store, plat, _config())
    info = solver._recover_instance_slot(challenge_id=cid, start_name="start_instance",
                                         failure="agent_environment_limit: share one quota")
    check("回收失败时不盲目重试 start", info is None and "start_instance" not in [n for n, _ in plat.calls],
          str(plat.calls))

    # ---- 5b) 真实平台那种「软失败」回执（isError=true 文本、不抛异常）也算失败 ----
    from ctfy_agent.instance_guard import delete_environment as _del  # noqa: PLC0415

    soft = FakePlatform(env={"status": "none"}, delete_soft_error=True)
    got = _del(soft, cid, "ground-1")
    check("软失败回执不被当成删除成功", got.get("ok") is False, str(got))

    # ---- 5c) start 失败原因不是槽位问题（NOT_FOUND）→ 不删环境、不重试 ----
    not_found = ('[工具报错] ----- 工具返回 (isError=true) -----\n'
                 '{"code":"NOT_FOUND","message":"The requested resource was not found."}')
    plat = FakePlatform(env={"status": "none"}, start_results=[not_found])
    store = _store(tmp / "r5c", cid)
    solver, events = _solver(store, plat, _config())
    info = solver._recover_instance_slot(cid, "start_instance", not_found)
    names = [n for n, _ in plat.calls]
    check("NOT_FOUND 失败不按 challenge_id 删环境",
          not any(n.startswith("ctf2_user_delete") for n in names), str(names))
    check("NOT_FOUND 失败不重试 start", "start_instance" not in names, str(names))
    check("NOT_FOUND 失败有确定性说明",
          any("不是槽位问题" in str(p.get("note")) for _, p in events), str(events)[-300:])

    # ---- 6) 原语直测：delete 参数带 competition_id ----
    plat = FakePlatform(env={"status": "none"})
    got = delete_environment(plat, cid, "ground-1")
    check("delete_environment 成功", got.get("ok") is True, str(got))
    check("delete_environment 带 challenge_id",
          plat.calls and plat.calls[-1][1].get("challenge_id") == cid, str(plat.calls[-1:]))
    check("严格 schema 平台不塞 competition_id（塞了平台会拒）",
          "competition_id" not in plat.calls[-1][1], str(plat.calls[-1:]))
    check("query_environment 解出 environment",
          query_environment(FakePlatform(env={"status": "none"}), cid, "g") == {"status": "none"})
    # schema 里真有 competition_id 的平台（CTFY 风格）才传
    plat_comp = FakePlatform(env={"status": "none"}, allow_competition_id=True)
    query_environment(plat_comp, cid, "comp-9")
    check("schema 允许时 query 带 competition_id",
          plat_comp.calls and plat_comp.calls[-1][1].get("competition_id") == "comp-9",
          str(plat_comp.calls[-1:]))

    # ---- 7) GUI 开跑前清扫：无 list_instances 平台给确定性提示 ----
    from ctfy_agent.gui.platform_worker import _preflight_instances  # noqa: PLC0415

    seen: list = []
    _preflight_instances(FakePlatform(), cid, (), lambda k, p=None: seen.append((k, p or {})))
    note = " ".join(str(p.get("note") or "") for _, p in seen)
    check("无 list_instances 时提示跳过清扫", "list_instances" in note and "跳过" in note, note)
    check("提示里说明由 solver 按题自愈", "按题" in note or "自愈" in note, note)

    print()
    if FAILS:
        print(f"smoke_instance_recovery 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("smoke_instance_recovery 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())