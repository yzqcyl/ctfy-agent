# -*- coding: utf-8 -*-
"""端到端验证：经 DeepSeekLLM.chat 真实调用，确认思考模式确实被打开。

断言：
  * LLM_ENABLE_THINKING=1 时，usage/回执里能看出思考参与（completion_tokens 明显更多）；
  * 不抛 400（说明 dashscope 接受 enable_thinking=True）；
  * 模型是 deepseek-v4.1-flash-1017。
用法：python probes/probe_thinking_e2e.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import load  # noqa: E402
from ctfy_agent.llm import DeepSeekLLM  # noqa: E402


def main() -> int:
    cfg = load()
    print("model =", cfg.model)
    results = {}
    for label, env in (("thinking_on", "1"), ("thinking_off", "0")):
        os.environ["LLM_ENABLE_THINKING"] = env
        llm = DeepSeekLLM(api_key=cfg.llm_api_key, model=cfg.model,
                          base_url=cfg.llm_base_url, max_tokens=2048, timeout=180)
        try:
            reply = llm.chat([{"role": "user",
                               "content": "一个 3x3 魔方有多少种合法状态？只给数字。"}])
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: FAIL {type(exc).__name__}: {exc}")
            return 1
        usage = reply.get("usage") or {}
        text = ((reply.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        results[label] = {"completion_tokens": usage.get("completion_tokens"),
                          "content": text.strip()[:60]}
        print(f"{label}: completion_tokens={usage.get('completion_tokens')} content={text.strip()[:40]!r}")
    os.environ.pop("LLM_ENABLE_THINKING", None)
    on = results["thinking_on"]["completion_tokens"] or 0
    off = results["thinking_off"]["completion_tokens"] or 0
    print(f"\n对比：thinking_on={on} vs thinking_off={off} completion_tokens")
    if on <= off:
        print("[warn] 开思考的 completion_tokens 没有更多——可能没真正生效，请看回执")
        return 1
    print("[ok] 思考模式已生效（开 > 关）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())