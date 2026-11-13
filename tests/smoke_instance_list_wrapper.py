#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_instance_list_wrapper.py — list_instances 的 {"result": [...]} 包裹回归（CTFY 实测）。

背景（2026-11-13 CTFY「Platform Playground」接入测试，真实回执）：
  CTFY 3.4.6 的 `list_instances` 返回 `{"result": []}` / `{"result": [{...}]}`（单数键）。
  旧版归一化只认 list / {instances|items|data|results}：
    - `instance_guard._items` → 归一成空列表 → `reap()` 以为没有残留实例（配额泄漏触发器）；
    - `solver._evict_stale_instances` → `data["items"]` 取不到 → 配额 1 时「清残留」永远清 0；
    - `solver._find_live_instance` → 看不到本题已有实例 → 重复 start。
  本测试钉住三处归一化都认 `result` 包裹（含再包一层 {"result": {"instances": []}}）。

运行: python tests/smoke_instance_list_wrapper.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import Config  # noqa: E402
from ctfy_agent.instance_guard import _items  # noqa: E402
from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []
CID = "SAMPLE-016"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


class DummyLLM:
    def __getattr__(self, name):
        return lambda *a, **k: None


class FakePlatform:
    """最小平台面：list_instances 回 CTFY 形状，stop_instance 记账。"""

    def __init__(self, listed: dict):
        self._listed = listed
        self.stopped: list[str] = []

    def try_resolve(self, kind: str) -> str:
        return {"list_instances": "list_instances", "stop_instance": "stop_instance",
                "start_instance": "start_instance", "get_instance": "get_instance"}.get(kind, "")

    def call_text(self, name: str, args: dict) -> str:
        if name == "list_instances":
            return "----- 工具返回 -----\n" + json.dumps(self._listed)
        if name == "stop_instance":
            self.stopped.append(str((args or {}).get("instance_id") or ""))
            return "----- 工具返回 -----\n{}"
        return "----- 工具返回 -----\n{}"

    def _as_json(self, text: str):
        body = str(text or "").split("----- 工具返回 -----", 1)[-1].strip()
        try:
            return json.loads(body)
        except Exception:  # noqa: BLE001
            return None


def _config() -> Config:
    cfg = Config()
    cfg.instance_ops_limit = 5
    cfg.extra["competition_id"] = "ground-1"
    return cfg


def main() -> int:
    # ---- 1) 纯归一化：_items 认 result（单数）与再包一层 ----
    one = {"id": "782e6892", "challenge_id": CID}
    check("_items({'result': [...]}) 能取到实例", _items({"result": [one]}) == [one])
    check("_items({'result': []}) 为空列表", _items({"result": []}) == [])
    check("_items({'result': {'instances': [...]}}) 再包一层也能取",
          _items({"result": {"instances": [one]}}) == [one])
    check("_items 旧键 results 仍兼容", _items({"results": [one]}) == [one])
    check("_items 旧键 items 仍兼容", _items({"items": [one]}) == [one])
    check("_items 裸 list 仍兼容", _items([one]) == [one])
    check("_items 非法输入安全", _items(None) == [] and _items("x") == [] and _items({}) == [])

    # ---- 2) solver._find_live_instance：能看到 {"result": [...]} 里的本题实例 ----
    tmp = ROOT / "runs" / f"_t_wrap_{__import__('os').getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    store = Store(tmp / "wrap").load()
    platform = FakePlatform({"result": [
        {"id": "other-id", "challenge_id": "SAMPLE-999"},
        one,
    ]})
    solver = Solver(_config(), platform, DummyLLM(), store)
    found = solver._find_live_instance(CID)
    check("_find_live_instance 从 result 包裹里找到本题实例",
          isinstance(found, dict) and found.get("id") == one["id"], str(found))
    check("_find_live_instance 不会返回别题实例",
          not (isinstance(found, dict) and found.get("id") == "other-id"), str(found))

    # ---- 3) solver._evict_stale_instances（配额 1 语义）：result 包裹里的别题实例要被清掉 ----
    platform2 = FakePlatform({"result": [
        {"id": "stale-1", "challenge_id": "SAMPLE-003"},
        {"id": "mine-1", "challenge_id": CID},
    ]})
    solver2 = Solver(_config(), platform2, DummyLLM(), store)
    stopped = solver2._evict_stale_instances(CID, quota=1)
    check("_evict_stale_instances 清掉 result 里的别题残留（quota=1）", stopped == 1, f"stopped={stopped}")
    check("_evict_stale_instances 只停别题实例", platform2.stopped == ["stale-1"], str(platform2.stopped))

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())