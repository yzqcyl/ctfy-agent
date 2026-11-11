#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_instance_quota_scope.py — instance_ops_limit 改成「本次运行」作用域的回归。

背景（2026-11-11 本地 plat 第二轮实测，runs/plat）：
  - 同一 run 目录重跑 17 题，其中 8 题第一轮就把 start_instance 记到 5 次；
  - 旧实现闸门读**持久化** `tool_counts` → 第二轮第一次 start 就回
    「[操作被拦截] start_instance 已达本题上限 5 次」：整轮 52 次拦截，8 题无法起实例，
    重跑「全对」根本不可能，跟模型能力无关。
修法：
  - 闸门读「本次运行（进程）」计数（`Store._run_calls()`，按 state.json 路径隔离，
    同进程内多 lane 共享；新进程/新运行从 0 开始）；
  - 持久化 `tool_counts` 继续跨轮累计，只当审计账，不参与闸门；
  - 平台自己的每题硬上限不变；被平台拒时仍 `refund_call` 回滚。

本文件钉住（全部离线，不连平台）：
  1) 同一次运行内仍然是 5 次上限，refund 生效；
  2) 「上一轮已用满 5 次」的新一轮（模拟新进程）能重新 start —— 本轮修复的核心；
  3) `start_remaining` 跟随本次运行口径（不再被旧账锁死）；
  4) 真实 `Solver._start_with_backoff` 在持久化账=5 的新一轮里能成功起实例；
  5) 审计账仍然累加（不给事后核对添乱）。

运行: python tests/smoke_instance_quota_scope.py
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
from ctfy_agent.instance_guard import start_remaining  # noqa: E402
from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []
CID = "chall-quota-scope"
UUID = "7b4beb2a-cd96-4528-8156-7737e197e023"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _new_process() -> None:
    """模拟「新进程/新一轮」：进程内的本次运行计数清空（持久化状态不动）。

    真实场景里这就是「上一个 run 的进程退出、驱动重新起一个进程跑同一 run 目录」。
    """
    with Store._run_call_guard:
        Store._run_call_counts.clear()


def _seed(root: pathlib.Path) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.meta["competition_id"] = "ground-1"
    store.upsert({"id": CID, "name": "quota", "category": "pwn", "description": "d", "points": 100})
    store.save()
    return store


def _audit(store: Store) -> int:
    """读磁盘上的持久化审计账（跨轮累计）。"""
    data = json.loads(store.path.read_text(encoding="utf-8"))
    return int(((data.get("challenges") or {}).get(CID) or {}).get("tool_counts", {}).get("start_instance", 0))


class FakePlatform:
    """_start_with_backoff 只需要 start_instance。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def start_instance(self, challenge_id, competition_id=None):
        self.calls.append((challenge_id, str(competition_id)))
        return {"id": UUID, "challenge_id": challenge_id}


def _solver(store: Store, platform) -> Solver:
    class DummyLLM:
        budget = None

    cfg = Config()
    cfg.instance_ops_limit = 5
    return Solver(cfg, platform, DummyLLM(), store)


def main() -> int:
    tmp = ROOT / "runs" / f"_t_quota_{os.getpid()}"

    # ---- 1) 同一次运行内：5 次上限 + refund ----
    store = _seed(tmp / "r1")
    results = [store.record_call(CID, "start_instance", 5) for _ in range(5)]
    check("同轮：前 5 次放行", all(results), str(results))
    check("同轮：第 6 次被拦", store.record_call(CID, "start_instance", 5) is False)
    check("同轮：call_count=5", store.call_count(CID, "start_instance") == 5, str(store.call_count(CID, "start_instance")))
    check("同轮：start_remaining=0", start_remaining(store, CID, 5) == 0)
    store.refund_call(CID, "start_instance")
    check("同轮：refund 后 call_count=4", store.call_count(CID, "start_instance") == 4)
    check("同轮：refund 后 start_remaining=1", start_remaining(store, CID, 5) == 1)
    store.save()
    check("审计：持久化账=4（跨轮累计，只作审计）", _audit(store) == 4, str(_audit(store)))

    # ---- 2) 新一轮（新进程）：旧账满 5 次也不能再锁死 start ----
    store = _seed(tmp / "r2")
    for _ in range(5):
        store.record_call(CID, "start_instance", 5)
    store.save()
    check("前置：上一轮持久化账已用满 5", _audit(store) == 5, str(_audit(store)))
    _new_process()
    fresh = Store(tmp / "r2").load()
    check("新一轮：call_count 从 0 开始", fresh.call_count(CID, "start_instance") == 0)
    check("新一轮：start_remaining=5（不再被旧账锁死）", start_remaining(fresh, CID, 5) == 5)
    check("新一轮：第一次 start 放行", fresh.record_call(CID, "start_instance", 5) is True)
    fresh.save()
    check("审计：新一轮后持久化账继续累加=6", _audit(fresh) == 6, str(_audit(fresh)))

    # ---- 3) 真实 solver 路径：上一轮配额用满后，新一轮能真正发出 start_instance ----
    store = _seed(tmp / "r3")
    for _ in range(5):
        store.record_call(CID, "start_instance", 5)
    store.save()
    _new_process()
    solver = _solver(Store(tmp / "r3").load(), FakePlatform())
    info = solver._start_with_backoff(CID, "start_instance")
    check("solver：新一轮 _start_with_backoff 成功起实例", isinstance(info, dict) and info.get("id") == UUID,
          str(info))
    check("solver：平台真的被调用 1 次", len(solver.platform.calls) == 1, str(solver.platform.calls))

    # ---- 4) 反向：同一轮内配额用尽后仍然拦住（保护没被削弱）----
    store = _seed(tmp / "r4")
    for _ in range(5):
        store.record_call(CID, "start_instance", 5)
    solver = _solver(store, FakePlatform())
    blocked = solver._start_with_backoff(CID, "start_instance")
    check("solver：同一轮配额用尽仍返回 None（闸门还在）", blocked is None, str(blocked))
    check("solver：配额用尽时平台没被调用", solver.platform.calls == [], str(solver.platform.calls))

    print()
    if FAILS:
        print(f"smoke_instance_quota_scope 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("smoke_instance_quota_scope 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())