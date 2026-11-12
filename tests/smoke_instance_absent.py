#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_instance_absent.py — 「平台明确说本题没有环境」的负缓存回归（离线、不连平台）。

背景（HANDOFF 2026-11-10 §8 P2）：crypto/misc 这类纯附件题没有实例，但重跑时
`solver._ensure_instance` / `crew` 的自动自启仍会先 start_instance 一次，平台回
`NOT_FOUND / is not visible`——白烧一次写操作配额 + 一步；模型之后还可能反复 start。

本文件钉住新逻辑：
  1) 失败原因判定：NOT_FOUND / not visible 才算「没有环境」；限流/槽位、比赛上下文不符不算；
  2) 首次 start 失败 → 落 `instance_absent` 负缓存（幂等、跨 store 重载存活）；
  3) 重跑：`auto_start_allowed()` 直接 False（solver 与 crew 共用这一判定）；
  4) 模型手动 start：确定性拦回、不再打平台、不再吃写操作配额；
  5) 逃生口：`instance_absent_retry=true` 时允许再试一次；
  6) 真的起来了实例 → 负缓存自动反转（平台偶发误报可自愈）。

运行: python tests/smoke_instance_absent.py
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

CID = "SAMPLE-NOENV"
NEW_UUID = "9f0c7a52-1f3e-4a6b-8f00-2d4c9c6a1b77"

# 平台真实回执形态（DASCTF ctf2）：工具级软失败 + isError
NOT_FOUND_TEXT = (
    '----- 工具返回 (isError=true) -----\n'
    '{"code":"NOT_FOUND","message":"challenge is not visible to current token"}'
)
LIMIT_TEXT = (
    '----- 工具返回 (isError=true) -----\n'
    '{"code":"agent_environment_limit","message":"practice and competition environments share one quota"}'
)
MISMATCH_TEXT = (
    '----- 工具返回 (isError=true) -----\n'
    '{"code":"challenge_not_found","message":"no such challenge in competition"}'
)


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


class FakePlatform:
    """只实现 solver 起实例会碰到的能力面；没有 list_instances（同 ctf2）。"""

    def __init__(self, *, start_result=None, env=None, has_challenge_env_tool=False):
        self.calls: list[tuple[str, dict]] = []
        self._start_result = start_result if start_result is not None else NOT_FOUND_TEXT
        self._env = env
        self.tool_map = {"start_instance": "start_instance", "submit_flag": "submit_answer"}
        if has_challenge_env_tool:
            # ctf2 形态：get_instance → 按 challenge_id 查环境
            self.tool_map["get_instance"] = "ctf2_get_environment"
            self.tool_map["stop_instance"] = "ctf2_delete_environment"

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        if kind not in self.tool_map:
            # 与真实 Platform.resolve 一致：解析不到抛 PlatformError（solver 内部按「忽略」处理）
            from ctfy_agent.platform import PlatformError  # noqa: PLC0415

            raise PlatformError(f"没有可解析的工具：{kind}")
        return self.tool_map[kind]

    def prop_names(self, name):
        if name in ("ctf2_get_environment", "ctf2_delete_environment"):
            return {"challenge_id", "practice_ground_id"}
        if name == "start_instance":
            return {"challenge_id", "competition_id"}
        return {}

    def model_tools(self):
        return [{"name": name} for name in self.tool_map.values()]

    def start_instance(self, challenge_id, competition_id=None):
        self.calls.append(("start_instance", {"challenge_id": challenge_id,
                                              "competition_id": competition_id}))
        return self._start_result

    def call_text(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "ctf2_get_environment":
            return "----- 工具返回 -----\n" + json.dumps({"environment": self._env or {}})
        if name == "ctf2_delete_environment":
            return "----- 工具返回 -----\n{}"
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


def _config(**extra) -> Config:
    cfg = Config()
    cfg.instance_ops_limit = 5
    cfg.auto_start_instance = True
    cfg.extra["competition_id"] = "ground-1"
    cfg.extra.update(extra)
    return cfg


def _store(root: pathlib.Path) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({"id": CID, "name": "no-env", "category": "CRYPTO", "description": "纯附件题"})
    store.save()
    return store


class DummyLLM:
    budget = None


def _solver(store: Store, platform: FakePlatform, cfg: Config | None = None) -> Solver:
    return Solver(cfg or _config(), platform, DummyLLM(), store)


def main() -> int:
    tmp = ROOT / "runs" / f"_t_noenv_{os.getpid()}"

    # ---- 0) 纯判定：什么才算「没有环境」 ----
    solver = _solver(_store(tmp / "judge"), FakePlatform())
    check("判定：NOT_FOUND → 没有环境", solver._looks_no_instance(NOT_FOUND_TEXT) is True)
    check("判定：not visible → 没有环境", solver._looks_no_instance("challenge is not visible") is True)
    check("判定：instance_not_found 不算没有环境（那是旧实例没了，该重建）",
          solver._looks_no_instance("instance_not_found: instance does not exist") is False)
    check("判定：配额/限流 不算没有环境", solver._looks_no_instance(LIMIT_TEXT) is False)
    check("判定：比赛上下文不符不算没有环境", solver._looks_no_instance(MISMATCH_TEXT) is False)
    check("判定：空串安全", solver._looks_no_instance("") is False)

    # ---- 1) 首次自启失败 → 落负缓存 ----
    store = _store(tmp / "mark")
    events: list[str] = []
    platform = FakePlatform()
    solver = _solver(store, platform)
    solver.on_event = lambda kind, payload=None: events.append(kind)
    info = solver._ensure_instance(CID)
    item = store.require(CID)
    check("首次 start：返回 None（平台没有环境）", info is None, str(info))
    check("首次 start：落 instance_absent 负缓存", item.instance_absent is True)
    check("首次 start：发出 instance_absent 事件", "instance_absent" in events, str(events))
    transcript = store.transcript_path(CID).read_text(encoding="utf-8")
    check("首次 start：转录留痕（说明按无环境题解）",
          "平台明确本题没有环境" in transcript and "无环境" in transcript, transcript[-200:])

    # ---- 2) 重跑：负缓存跨 store 重载存活，且自动自启被跳过 ----
    store2 = Store(tmp / "mark").load()
    solver2 = _solver(store2, platform)
    item2 = store2.require(CID)
    check("重载：负缓存持久化", item2.instance_absent is True)
    check("重跑：auto_start_allowed=False", solver2.auto_start_allowed(item2) is False)

    # ---- 3) 模型手动 start：确定性拦回、不打平台、不吃配额 ----
    from ctfy_agent.tools_local import LocalTools  # noqa: PLC0415

    before = len(platform.calls)
    quota_before = store2.call_count(CID, "start_instance")
    local = LocalTools(store2, CID)
    text, _ = solver2._execute("start_instance", {"challenge_id": CID}, local, CID)
    check("手动 start：回执确定性说明没有环境", "平台此前已明确本题没有环境" in text, text[:200])
    check("手动 start：没有打到平台", len(platform.calls) == before, str(platform.calls[before:]))
    check("手动 start：没有消耗写操作配额",
          store2.call_count(CID, "start_instance") == quota_before,
          str(store2.call_count(CID, "start_instance")))

    # ---- 4) 逃生口：instance_absent_retry=true 允许再试一次（平台偶发误报）----
    retry_solver = _solver(store2, platform, _config(instance_absent_retry=True))
    check("逃生口：auto_start_allowed 放行", retry_solver.auto_start_allowed(store2.require(CID)) is True)
    before = len(platform.calls)
    retry_solver._execute("start_instance", {"challenge_id": CID}, local, CID)
    check("逃生口：真的又打了一次平台", len(platform.calls) == before + 1, str(platform.calls[before:]))

    # ---- 5) 配额/限流失败不落负缓存（别把「槽位被占」误判成「没有环境」）----
    store3 = _store(tmp / "limit")
    solver3 = _solver(store3, FakePlatform(start_result=LIMIT_TEXT))
    solver3._ensure_instance(CID)
    check("限流失败：不落负缓存", store3.require(CID).instance_absent is False)

    # ---- 6) 真的起来了实例 → 负缓存反转 ----
    store4 = _store(tmp / "started")
    store4.require(CID).instance_absent = True
    store4.save()
    started_platform = FakePlatform(start_result={"id": NEW_UUID, "access_url": "1.2.3.4:9999"})
    solver4 = _solver(store4, started_platform)
    info = solver4._ensure_instance(CID)
    check("恢复：start 成功返回实例信息", isinstance(info, dict) and info.get("id") == NEW_UUID, str(info))
    check("恢复：负缓存被反转", store4.require(CID).instance_absent is False)

    # ---- 7) 负缓存不影响「本来就有实例」的题 ----
    store5 = _store(tmp / "has-instance")
    item5 = store5.require(CID)
    item5.instance = {"id": NEW_UUID, "access_url": "1.2.3.4:9999"}
    item5.instance_id = NEW_UUID
    store5.save()
    check("有实例：auto_start_allowed=False（复用已登记实例）",
          _solver(store5, FakePlatform()).auto_start_allowed(store5.require(CID)) is False)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"smoke_instance_absent 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_instance_absent 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())