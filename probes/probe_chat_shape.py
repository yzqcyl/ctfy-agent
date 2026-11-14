# -*- coding: utf-8 -*-
"""确认 DeepSeekLLM.chat 的返回结构（thinking 开时 content 是否为空）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import load  # noqa: E402
from ctfy_agent.llm import DeepSeekLLM  # noqa: E402

cfg = load()
for env in ("1", "0"):
    os.environ["LLM_ENABLE_THINKING"] = env
    llm = DeepSeekLLM(api_key=cfg.llm_api_key, model=cfg.model,
                      base_url=cfg.llm_base_url, max_tokens=2048, timeout=180)
    r = llm.chat([{"role": "user", "content": "一个 3x3 魔方有多少种合法状态？只给数字。"}])
    raw = r.get("raw") or {}
    print(f"env={env} keys={list(r)} finish={r.get('finish_reason')} "
          f"content={r.get('content')!r} reasoning_len={len(raw.get('reasoning_content') or '')} "
          f"completion_tokens={(r.get('usage') or {}).get('completion_tokens')}")
os.environ.pop("LLM_ENABLE_THINKING", None)