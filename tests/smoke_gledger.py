#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_gledger.py — 全局 token 总账（跨题 1B 总量）回归。

背景（2026-11-12 缺口审计 P1-2）：正式赛硬约束是整队 1B token 总量（规则 v2.5 §二(五)），
但代码里只有题级 max_tokens_per_challenge=800k。多题/重跑/重启容器叠加时没有任何总闸，
跑飞了也只会「单题」先撞墙，总量可能被顶穿。gledger.py 补上这块。

覆盖 10 条：
 1) 落盘累计：record 后新实例能读到（跨进程语义）
 2) 原始口径：prompt+completion 按平台计费口径累加，不减缓存折扣
 3) 到顶拦截：used>=cap 时 check() 抛 BudgetExceeded，kind/stop_reason 与现有编排兼容
 4) cap=0 = 不启用：check 不拦、remaining 为 None
 5) 路径解析：CTFY_GLOBAL_LEDGER > CTFY_WORK_DIR > runs 上一级
 6) 配置接线：AGENT_MAX_TOTAL_TOKENS / CTFY_MAX_TOTAL_TOKENS 环境变量覆盖
 7) build_global_ledger：cap<=0 返回 None（零开销）
 8) LLM 集成：chat() 发请求前 check、收到响应后 record
 9) LLM 拦截：总账到顶时 chat() 在发请求之前就抛错（不产生费用）
10) 多实例共享：同一路径两个实例交替写，累加不丢

运行: python tests/smoke_gledger.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.budget import BudgetExceeded, stop_reason_of  # noqa: E402
from ctfy_agent.config import load  # noqa: E402
from ctfy_agent.gledger import (  # noqa: E402
    DEFAULT_MAX_TOTAL_TOKENS, GlobalTokenLedger, build_global_ledger, ledger_path,
)
from ctfy_agent.llm import DeepSeekLLM  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def ledger_file(tmp: pathlib.Path) -> pathlib.Path:
    return tmp / ".ctfy-global-ledger.json"


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_gledger"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    path = ledger_file(tmp)

    # ---- 1) 落盘累计（新实例可读）----
    ledger = GlobalTokenLedger(path, 1000)
    ledger.record({"prompt_tokens": 100, "completion_tokens": 20})
    fresh = GlobalTokenLedger(path, 1000)
    check("落盘累计且新实例可读", fresh.used() == 120, f"used={fresh.used()}")
    saved = json.loads(path.read_text(encoding="utf-8"))
    check("总账文件结构可读（schema/used/calls）",
          saved.get("schema") == 1 and saved.get("used_tokens") == 120 and saved.get("calls") == 1,
          str(saved)[:160])

    # ---- 2) 原始口径：缓存折扣不影响总账 ----
    path2 = tmp / "raw.json"
    raw = GlobalTokenLedger(path2, 10_000)
    raw.record({
        "prompt_tokens": 1000,
        "completion_tokens": 50,
        "prompt_tokens_details": {"cached_tokens": 900},
    })
    snap = raw.snapshot()
    check("总账按原始 token 记（不减缓存折扣）", snap["used"] == 1050, f"used={snap['used']}")

    # ---- 3) 到顶拦截 ----
    path3 = tmp / "cap.json"
    capped = GlobalTokenLedger(path3, 150, label="SAMPLE-X")
    capped.record({"prompt_tokens": 120, "completion_tokens": 0})
    capped.check()  # 120 < 150，应放行
    capped.record({"prompt_tokens": 40, "completion_tokens": 0})
    raised = None
    try:
        capped.check()
    except BudgetExceeded as exc:
        raised = exc
    check("used>=cap 时 check() 抛 BudgetExceeded", raised is not None)
    if raised is not None:
        check("异常文案说明是全局总账",
              "全局 token 总账" in str(raised) and "1B" in str(raised), str(raised)[:140])
        check("kind/stop_reason 与现有编排兼容",
              raised.kind == "budget_exhausted" and raised.attempt is False
              and stop_reason_of(raised) == "budget_exhausted",
              f"kind={raised.kind} attempt={raised.attempt}")

    # ---- 4) cap=0 = 不启用 ----
    off = GlobalTokenLedger(tmp / "off.json", 0)
    off.record({"prompt_tokens": 10**9, "completion_tokens": 0})
    ok = True
    try:
        off.check()
    except BudgetExceeded:
        ok = False
    check("cap=0 不拦且 remaining=None", ok and off.remaining() is None)

    # ---- 5) 路径解析 ----
    class FakeCfg:
        def __init__(self, runs_path: pathlib.Path):
            self.runs_path = runs_path
            self.extra: dict = {}

    cfg = FakeCfg(tmp / "runs")
    saved_env = {k: os.environ.get(k) for k in ("CTFY_GLOBAL_LEDGER", "CTFY_WORK_DIR")}
    try:
        for key in saved_env:
            os.environ.pop(key, None)
        check("默认落在 runs 上一级", ledger_path(cfg) == tmp / ".ctfy-global-ledger.json", str(ledger_path(cfg)))
        os.environ["CTFY_WORK_DIR"] = str(tmp / "work")
        check("CTFY_WORK_DIR 优先", ledger_path(cfg) == tmp / "work" / ".ctfy-global-ledger.json", str(ledger_path(cfg)))
        os.environ["CTFY_GLOBAL_LEDGER"] = str(tmp / "explicit.json")
        check("CTFY_GLOBAL_LEDGER 最高优先", ledger_path(cfg) == tmp / "explicit.json", str(ledger_path(cfg)))
        os.environ.pop("CTFY_GLOBAL_LEDGER")
        cfg.extra["global_ledger_path"] = str(tmp / "from_config.json")
        check("config.extra.global_ledger_path 生效", ledger_path(cfg) == tmp / "from_config.json", str(ledger_path(cfg)))
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ---- 6) 配置接线（环境变量覆盖）----
    saved_cap = {k: os.environ.get(k) for k in ("AGENT_MAX_TOTAL_TOKENS", "CTFY_MAX_TOTAL_TOKENS")}
    try:
        os.environ.pop("AGENT_MAX_TOTAL_TOKENS", None)
        os.environ.pop("CTFY_MAX_TOTAL_TOKENS", None)
        cfg_default = load()
        check("默认总账上限 = 1B（正式赛口径）",
              cfg_default.max_total_tokens == DEFAULT_MAX_TOTAL_TOKENS == 1_000_000_000,
              str(cfg_default.max_total_tokens))
        os.environ["AGENT_MAX_TOTAL_TOKENS"] = "1234"
        check("AGENT_MAX_TOTAL_TOKENS 覆盖生效", load().max_total_tokens == 1234, str(load().max_total_tokens))
        os.environ.pop("AGENT_MAX_TOTAL_TOKENS")
        os.environ["CTFY_MAX_TOTAL_TOKENS"] = "0"
        check("CTFY_MAX_TOTAL_TOKENS=0 可关闭", load().max_total_tokens == 0, str(load().max_total_tokens))
    finally:
        for key, value in saved_cap.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    # ---- 7) build_global_ledger：cap<=0 → None ----
    class Cfg2(FakeCfg):
        max_total_tokens = 0

    check("cap<=0 时 build_global_ledger 返回 None", build_global_ledger(Cfg2(tmp / "runs")) is None)

    # ---- 8) LLM 集成：请求前 check、响应后 record ----
    calls = {"post": 0}

    class FakeLLM(DeepSeekLLM):
        def _post(self, path_, payload, retries=5, timeout_retries=1):
            calls["post"] += 1
            return {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 110, "completion_tokens": 30, "total_tokens": 140},
            }

    path4 = tmp / "llm.json"
    gl4 = GlobalTokenLedger(path4, 10_000)
    llm = FakeLLM(api_key="sk-test", base_url="https://example.invalid/v1", model="m", gledger=gl4)
    llm.chat([{"role": "user", "content": "hi"}])
    check("chat() 后总账 +140", gl4.used() == 140, f"used={gl4.used()}")
    check("chat() 真的发了请求", calls["post"] == 1, str(calls))

    # ---- 9) LLM 拦截：到顶时请求发出前就抛 ----
    path5 = tmp / "llm_cap.json"
    gl5 = GlobalTokenLedger(path5, 100)
    gl5.record({"prompt_tokens": 100, "completion_tokens": 0})
    calls["post"] = 0
    llm2 = FakeLLM(api_key="sk-test", base_url="https://example.invalid/v1", model="m", gledger=gl5)
    blocked = None
    try:
        llm2.chat([{"role": "user", "content": "hi"}])
    except BudgetExceeded as exc:
        blocked = exc
    check("总账到顶时 chat() 拒绝发请求", blocked is not None and calls["post"] == 0,
          f"blocked={blocked is not None} posts={calls['post']}")

    # ---- 10) 多实例共享同一路径 ----
    path6 = tmp / "shared.json"
    a = GlobalTokenLedger(path6, 10_000)
    b = GlobalTokenLedger(path6, 10_000)
    a.record({"prompt_tokens": 10, "completion_tokens": 0})
    b.record({"prompt_tokens": 20, "completion_tokens": 0})
    a.record({"prompt_tokens": 30, "completion_tokens": 0})
    check("多实例交替写累加不丢", GlobalTokenLedger(path6, 10_000).used() == 60,
          f"used={GlobalTokenLedger(path6, 10_000).used()}")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_gledger 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())