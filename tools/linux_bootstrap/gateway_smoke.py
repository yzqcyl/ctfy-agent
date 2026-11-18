#!/usr/bin/env python3
"""gateway_smoke.py —— 验证「走平台大模型网关」的链路（读 OPENAI_* 环境变量，用项目自己的 LLM 客户端发一次 chat）。

用法（真实网关）：
    export OPENAI_BASE_URL="https://<厂商>.<会话域名>"
    export OPENAI_API_KEY="<会话凭据>"
    export OPENAI_MODEL="<网关模型名>"
    python3 tools/linux_bootstrap/gateway_smoke.py

用法（本地 mock）：
    python3 tools/linux_bootstrap/mock_gateway.py 18080 &   # 另开终端
    export OPENAI_BASE_URL="http://127.0.0.1:18080/v1"
    export OPENAI_API_KEY="pk_mock"
    export OPENAI_MODEL="mock-model"
    python3 tools/linux_bootstrap/gateway_smoke.py
"""
from __future__ import annotations

import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from ctfy_agent.config import load  # noqa: E402
from ctfy_agent.llm import DeepSeekLLM  # noqa: E402


def try_urls(base: str) -> list[str]:
    base = base.rstrip("/")
    candidates = [base, base + "/v1"]
    seen: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    return seen


def main() -> int:
    # 强制只读环境变量（OPENAI_*），忽略本地 .env 里的 DEEPSEEK_*，才能真实测网关回退
    for key in ("DEEPSEEK_BASE_URL", "DEEPSEEK_API_KEY", "LLM_API_KEY", "DEEPSEEK_MODEL",
                "LLM_BASE_URL", "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"):
        os.environ.pop(key, None)
    cfg = load(use_env_file=False)
    print("config: model=%s base=%s key_len=%d" %
          (cfg.model, cfg.llm_base_url, len(cfg.llm_api_key or "")))
    bases = try_urls(cfg.llm_base_url)
    ok = False
    for base in bases:
        client = DeepSeekLLM(api_key=cfg.llm_api_key, model=cfg.model,
                             base_url=base, max_tokens=32)
        try:
            result = client.chat([{"role": "user", "content": "只回复两个字：收到"}])
            print(f"[OK] {base} -> {str(result.get('content'))[:40]!r}")
            ok = True
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {base} -> {type(exc).__name__}: {str(exc)[:120]}")
    print("网关冒烟结果：", "通过 ✅" if ok else "失败 ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
