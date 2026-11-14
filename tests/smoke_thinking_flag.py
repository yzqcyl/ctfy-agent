#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：思考模式开关（P21）。

实测（2026-11-14，dashscope + deepseek-v4.1-flash-1017）：
  * 不带 enable_thinking → 返回 reasoning_content（端点默认开）；
  * enable_thinking=True → 返回 reasoning_content；
  * enable_thinking=False → 无 reasoning（completion_tokens 2 vs 37）。
用户要求「开思考模式」，所以代码必须能把 1/true/on 显式翻成 True，且不能对不认该字段的
端点乱发（智谱等严格网关会 400 → 整题报废）。
本用例锁：三态（1→True / 0→False / 未设→不发）+ 非 DashScope 端点一律不发。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.llm import DeepSeekLLM  # noqa: E402

DASH = "https://dashscope.aliyuncs.com/compatible-mode/v1"
OTHER = "https://open.bigmodel.cn/api/paas/v4"


def payload_for(base_url: str, env_value: str | None) -> dict:
    """构造一次 chat，返回实际发出去的 payload。"""
    old = os.environ.pop("LLM_ENABLE_THINKING", None)
    try:
        if env_value is not None:
            os.environ["LLM_ENABLE_THINKING"] = env_value
        llm = DeepSeekLLM(api_key="sk-test", model="deepseek-v4.1-flash-1017",
                          base_url=base_url, max_tokens=64)
        captured: dict = {}

        def fake_post(path, payload, retries=0, timeout_retries=0):  # noqa: ANN001
            captured.update(payload)
            return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}

        llm._post = fake_post  # type: ignore[method-assign]
        llm.chat([{"role": "user", "content": "hi"}])
        return captured
    finally:
        os.environ.pop("LLM_ENABLE_THINKING", None)
        if old is not None:
            os.environ["LLM_ENABLE_THINKING"] = old


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("[ok] " if cond else "[FAIL] ") + name + ("  " + detail if not cond and detail else ""))
    if not cond:
        check.failed = True  # type: ignore[attr-defined]


check.failed = False  # type: ignore[attr-defined]


def main() -> int:
    for value in ("1", "true", "on", "yes"):
        got = payload_for(DASH, value)
        check(f"dashscope + LLM_ENABLE_THINKING={value} → enable_thinking=True",
              got.get("enable_thinking") is True, str(got.get("enable_thinking")))
    for value in ("0", "false", "off", "no"):
        got = payload_for(DASH, value)
        check(f"dashscope + LLM_ENABLE_THINKING={value} → enable_thinking=False",
              got.get("enable_thinking") is False, str(got.get("enable_thinking")))
    got = payload_for(DASH, None)
    check("dashscope 未设环境变量 → 不发该字段（保持端点默认=开）",
          "enable_thinking" not in got, str(got.get("enable_thinking")))
    got = payload_for(OTHER, "1")
    check("非 DashScope 端点即使设 1 也不发（严格网关会 400）",
          "enable_thinking" not in got, str(got.get("enable_thinking")))
    return 1 if check.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())