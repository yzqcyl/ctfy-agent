#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_instance_id_repair.py — instance_id 被填成 host/前缀 的参数修正回归（离线、不连平台）。

背景（2026-11-11 本地 plat 测试实测，SAMPLE-008 / SAMPLE-019）：
  - `start_instance` 回执里的实例 id 是 UUID（如 7b4beb2a-…），而 host 是
    `sspvi6rzgeqvjxpc3.node4.ctfy.run` 这种完全不同的形态；
  - 模型调 `verify_exploit` 时把 host 或 host 前缀当成 instance_id → 平台恒回
    `instance_not_found`（404），该题的多问题判定永远过不了 → 整题 stalled。
  - 同一形态在 2026-10-29 SAMPLE-007 也出现过（platform.py 顶部注释）。

本文件钉住两件事（都只在**已经出错**的方向上兜底）：
  1) 模型填的值确实是「本题实例回执里的 host / host 前缀」时，纠回登记的 UUID；
     其它情况（合法 UUID、别的 UUID、无关字符串）一律不动，交给平台报错；
  2) 已失败的回执里若出现 instance_not_found，追加确定性提示（含本题登记 UUID）。

以及「不要为了修 bug 把原来能解的题弄坏」：
  3) 正确 UUID 的 verify_exploit 成功路径保持原样（判题通过仍会返回 solved=True 并落库）。

运行: python tests/smoke_instance_id_repair.py
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
from ctfy_agent.platform import PlatformError, looks_like_instance_uuid  # noqa: E402
from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import ALLOWED, Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools  # noqa: E402

FAILS: list[str] = []

INSTANCE_UUID = "7b4beb2a-cd96-4528-8156-7737e197e023"
INSTANCE_HOST = "sspvi6rzgeqvjxpc3.node4.ctfy.run"
OTHER_UUID = "c44f145b-0555-42bd-8557-55863af7deda"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


# ---------------------------------------------------------------- 假平台
class FakePlatform:
    """只实现 solver._execute / _autofill 会碰到的能力面。"""

    def __init__(self, *, solved=(), verify_text=None):
        self.calls: list[tuple[str, dict]] = []
        self._solved = set(solved)
        self._verify_text = verify_text or (
            '----- 工具返回 -----\n{"passed":true,"question_id":"flag"}')
        self.tool_map = {
            "submit_flag": "submit_answer",
            "start_instance": "start_instance",
            "stop_instance": "stop_instance",
            "verify_exploit": "verify_exploit",
            "verify_answer": "verify_answer",
            "get_instance": "get_instance",
        }

    def try_resolve(self, kind):
        return self.tool_map.get(kind)

    def resolve(self, kind):
        if kind not in self.tool_map:
            raise KeyError(kind)
        return self.tool_map[kind]

    def model_tools(self):
        return [{"name": name} for name in self.tool_map.values()]

    def prop_names(self, name):
        if name == "verify_exploit":
            return {"instance_id": {"type": "string"}, "question_id": {"type": "string"}}
        if name == "start_instance":
            return {"challenge_id": {"type": "string"}}
        return {}

    def call_text(self, name, arguments=None, *, retries=0):
        args = dict(arguments or {})
        self.calls.append((name, args))
        if name == "verify_exploit":
            instance_id = str(args.get("instance_id") or "")
            if instance_id != INSTANCE_UUID:
                return ('----- 工具返回 (isError=true) -----\n'
                        f'{{"code":"instance_not_found","message":"Instance {instance_id} not found"}}')
            return self._verify_text
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

    def my_solved_ids(self):
        return set(self._solved)


def _config() -> Config:
    cfg = Config()
    cfg.instance_ops_limit = 5
    cfg.extra.setdefault("competition_id", "ground-1")
    return cfg


def _store(root: pathlib.Path, cid: str, *, with_instance: bool = True) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.challenges.clear()
    store.upsert({
        "id": cid, "name": "inst-id", "category": "pwn",
        "description": "d", "points": 100,
    })
    item = store.require(cid)
    if with_instance:
        item.instance_id = INSTANCE_UUID
        item.instance = {
            "id": INSTANCE_UUID,
            "surface": {"services": [{"service_type": "https", "host": INSTANCE_HOST, "port": 443}]},
        }
        # 真实流程里 verify 之前必然已经 start 过（状态推到 instance_up）；new→submitted 是非法的，
        # 这里按真实状态机建场景，才能验证「判题通过 → submitted」这条原有路径没被改坏。
        if "instance_up" in ALLOWED.get(item.status, set()):
            store.set_status(cid, "instance_up")
    store.save()
    return store


def _solver(store: Store, platform: FakePlatform) -> Solver:
    class DummyLLM:
        budget = None

    return Solver(_config(), platform, DummyLLM(), store)


def main() -> int:
    tmp = ROOT / "runs" / f"_t_instid_{os.getpid()}"
    cid = "chall-inst-id"

    # ---- 0) 纯函数：UUID 形态判定 ----
    check("形态：登记 UUID 被识别", looks_like_instance_uuid(INSTANCE_UUID) is True)
    check("形态：host 不被当成 UUID", looks_like_instance_uuid(INSTANCE_HOST) is False)
    check("形态：host 前缀不被当成 UUID", looks_like_instance_uuid("sspvi6rzgeqvjxpc3") is False)
    check("形态：非字符串安全", looks_like_instance_uuid(None) is False)

    # ---- 1) 参数修正：host / host 前缀 → UUID ----
    store = _store(tmp / "r1", cid)
    solver = _solver(store, FakePlatform())
    check("修正：完整 host → UUID",
          solver._autofill("verify_exploit", {"instance_id": INSTANCE_HOST}, cid)["instance_id"] == INSTANCE_UUID)
    check("修正：host 前缀 → UUID",
          solver._autofill("verify_exploit", {"instance_id": "sspvi6rzgeqvjxpc3"}, cid)["instance_id"] == INSTANCE_UUID)
    notes = [line for line in store.transcript_path(cid).read_text(encoding="utf-8").splitlines()
             if "参数纠正" in line]
    check("修正：转录里有确定性说明", len(notes) >= 1, str(notes[:2]))

    # ---- 2) 不该动的一律不动（避免把别的实例/合法值改坏）----
    check("不动：正确 UUID 原样保留",
          solver._autofill("verify_exploit", {"instance_id": INSTANCE_UUID}, cid)["instance_id"] == INSTANCE_UUID)
    check("不动：别的 UUID 原样保留（可能是别题实例）",
          solver._autofill("verify_exploit", {"instance_id": OTHER_UUID}, cid)["instance_id"] == OTHER_UUID)
    check("不动：非 host 的自拼值原样保留",
          solver._autofill("verify_exploit", {"instance_id": "s3mzomar64iepo3qh"}, cid)["instance_id"] == "s3mzomar64iepo3qh")
    check("不动：无关参数不增不减",
          solver._autofill("verify_exploit",
                           {"instance_id": INSTANCE_HOST, "question_id": "flag"}, cid)
          == {"instance_id": INSTANCE_UUID, "question_id": "flag"})

    # ---- 3) 端到端：host 被纠正后 verify 真的能过（而不是 404）----
    store = _store(tmp / "r2", cid)
    platform = FakePlatform()
    solver = _solver(store, platform)
    local = LocalTools(store, cid)
    text, solved = solver._execute("verify_exploit", {"instance_id": INSTANCE_HOST, "question_id": "flag"},
                                   local, cid)
    check("端到端：平台收到的是 UUID", platform.calls and platform.calls[-1][1]["instance_id"] == INSTANCE_UUID,
          str(platform.calls[-1]))
    check("端到端：回执是 passed 而不是 instance_not_found", "passed" in text and "instance_not_found" not in text, text[:200])
    check("端到端：未判解出时不误报 solved", solved is False)

    # ---- 4) 端到端：纠正不了的值仍报错，但带确定性提示 ----
    store = _store(tmp / "r3", cid)
    platform = FakePlatform()
    solver = _solver(store, platform)
    local = LocalTools(store, cid)
    text, _ = solver._execute("verify_exploit", {"instance_id": OTHER_UUID, "question_id": "flag"}, local, cid)
    check("失败回执保留平台原文", "instance_not_found" in text, text[:200])
    check("失败回执带本题登记 UUID 提示", INSTANCE_UUID in text and "[提示] instance_not_found" in text, text[:300])

    # ---- 4b) 实例已被回收（state 里没有登记 id）时也要给方向，别让模型拿旧值反复 404 ----
    store = _store(tmp / "r3b", cid, with_instance=False)
    platform = FakePlatform()
    solver = _solver(store, platform)
    local = LocalTools(store, cid)
    text, _ = solver._execute("verify_exploit", {"instance_id": OTHER_UUID, "question_id": "flag"}, local, cid)
    check("已回收实例：提示重新 start_instance", "重新 start_instance" in text, text[:300])
    check("已回收实例：如实说未登记实例", "本题未登记实例" in text, text[:300])

    # ---- 5) 没有实例时行为不变（仍抛 PlatformError，不静默乱填）----
    store = _store(tmp / "r4", cid, with_instance=False)
    solver = _solver(store, FakePlatform())
    try:
        solver._autofill("verify_exploit", {}, cid)
        check("无实例：抛 PlatformError", False)
    except PlatformError as exc:
        check("无实例：抛 PlatformError（文案含 start_instance）", "start_instance" in str(exc), str(exc))

    # ---- 6) 不许破坏「原来能解出来的」：正确 UUID 的判定通过路径保持原样 ----
    store = _store(tmp / "r5", cid)
    # 真实流程里 verify 之前状态已推进到 candidate_flag（new→instance_up→probing→candidate_flag），
    # 只有从 candidate_flag 才允许落 submitted（state.ALLOWED）。按真实状态机建场景。
    store.set_status(cid, "probing")
    store.set_status(cid, "candidate_flag")
    store.save()
    platform = FakePlatform(solved={cid})
    solver = _solver(store, platform)
    local = LocalTools(store, cid)
    text, solved = solver._execute("verify_exploit", {"instance_id": INSTANCE_UUID, "question_id": "flag"},
                                   local, cid)
    check("回归：正确 UUID 判题通过仍返回 solved=True", solved is True, text[:200])
    check("回归：判题通过仍落库 submitted", store.require(cid).status == "submitted",
          str(store.require(cid).status))
    path = store.transcript_path(cid)
    transcript_text = path.read_text(encoding="utf-8") if path.exists() else ""
    check("回归：正确路径没有多余纠正记录", "参数纠正" not in transcript_text, transcript_text[-200:])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"smoke_instance_id_repair 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_instance_id_repair 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())