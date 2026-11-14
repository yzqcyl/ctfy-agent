# -*- coding: utf-8 -*-
"""细看：thinking 开/关时 finish_reason、reasoning_content、content 的关系。"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import load  # noqa: E402


def call(cfg, payload):
    req = urllib.request.Request(
        f"{cfg.llm_base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {cfg.llm_api_key}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    cfg = load()
    prompt = "一个 3x3 魔方有多少种合法状态？只给数字。"
    for label, thinking in (("on", True), ("off", False)):
        payload = {"model": cfg.model, "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 2048, "temperature": 0.2, "enable_thinking": thinking}
        body = call(cfg, payload)
        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        print(f"[{label}] finish={choice.get('finish_reason')} "
              f"content_len={len(msg.get('content') or '')} "
              f"reasoning_len={len(msg.get('reasoning_content') or '')} "
              f"usage={body.get('usage')}")
        print(f"      content={(msg.get('content') or '')[:80]!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())