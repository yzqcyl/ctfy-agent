# -*- coding: utf-8 -*-
"""探针：确认 dashscope 上 deepseek-v4.1-flash-1017 是否认 enable_thinking 字段。

三种 payload 各打一次（max_tokens 小、同一句提示）：
  A) 不带 enable_thinking（端点默认）
  B) enable_thinking = True
  C) enable_thinking = False
记录：HTTP 状态、是否有 reasoning_content、completion_tokens、正文首行。
用法：python probes/probe_thinking_field.py（在 E:\\ctfy_project 下）
"""
from __future__ import annotations

import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import load  # noqa: E402


def post(base_url: str, key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return {"status": resp.status, "body": json.loads(resp.read().decode("utf-8"))}
    except urllib.error.HTTPError as exc:  # noqa: F821
        try:
            body = json.loads(exc.read().decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001
            body = {}
        return {"status": exc.code, "body": body, "error": str(exc)}


def main() -> int:
    cfg = load()
    base = cfg.llm_base_url
    msg = [{"role": "user", "content": "只回两个字：收到"}]
    out = {}
    for label, extra in (("A_default", {}), ("B_true", {"enable_thinking": True}),
                         ("C_false", {"enable_thinking": False})):
        payload = {"model": cfg.model, "messages": msg, "max_tokens": 64, "temperature": 0.2}
        payload.update(extra)
        res = post(base, cfg.llm_api_key, payload)
        body = res.get("body") or {}
        choice = (body.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "")[:40]
        reasoning = ((choice.get("message") or {}).get("reasoning_content") or "")
        usage = body.get("usage") or {}
        out[label] = {
            "status": res.get("status"),
            "finish_reason": choice.get("finish_reason"),
            "has_reasoning": bool(reasoning),
            "reasoning_len": len(reasoning),
            "completion_tokens": usage.get("completion_tokens"),
            "content": content,
            "err": (body.get("error") or {}).get("message") if isinstance(body.get("error"), dict) else res.get("error"),
        }
        print(label, json.dumps(out[label], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
