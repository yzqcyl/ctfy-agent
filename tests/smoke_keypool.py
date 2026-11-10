#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_keypool.py — KeyPool 自动切换（加固-需求1）回归。

判定表（docs/plan-hardening-2026-11-10.md §2）：
  - 401/403/402 → 立即切下一个 key，重发不消耗 retries；
  - 429 且 body 是配额类字样 → 切；429 纯限速 → 不切，走退避；
  - 5xx / 网络错误 → 绝不切；
  - key 全耗尽 → LLMError(retryable=False)，不再重试；
  - 平台网关 base_url（*.tsinghua.edu.cn）→ pool_from_env 强制单 key（合规闸二）；
  - 未配 LLM_API_KEYS → 单 key，DeepSeekLLM 行为与现状一致（不旋转）。

运行: PYTHONPATH=src python tests/smoke_keypool.py
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import urllib.error
from email.message import Message
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.keypool import KeyPool, classify_key_failure, pool_from_env  # noqa: E402
from ctfy_agent.llm import DeepSeekLLM, LLMError  # noqa: E402

OK = {"choices": [{"message": {"content": "ok"}}], "usage": {}}


class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self._body


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/v1/chat/completions", code, "reason",
        Message(), io.BytesIO(body.encode("utf-8")),
    )


class _Opener:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.auth_seen: list[str] = []

    def open(self, request, timeout=None):
        self.calls += 1
        self.auth_seen.append(request.get_header("Authorization") or "")
        step = self.script.pop(0) if self.script else _http_error(500, "boom")
        if isinstance(step, Exception):
            raise step
        return _Resp(step)


def _llm_with_pool(keys) -> tuple[DeepSeekLLM, KeyPool]:
    pool = KeyPool(keys)
    llm = DeepSeekLLM(api_key=keys[0], model="m", base_url="https://example.invalid/v1",
                      timeout=1.0, key_pool=pool)
    return llm, pool


def case_classify() -> None:
    assert classify_key_failure(401, "") == "dead"
    assert classify_key_failure(403, "") == "dead"
    assert classify_key_failure(402, "") == "quota"
    assert classify_key_failure(429, '{"error":{"code":"insufficient_quota"}}') == "quota"
    assert classify_key_failure(429, "欠费停机") == "quota"
    assert classify_key_failure(429, "Rate limit reached: TPM 100000") == "ratelimit"
    assert classify_key_failure(429, "") == "ratelimit"
    assert classify_key_failure(400, "no permission for this model") == "dead"
    assert classify_key_failure(500, "internal") == "transient"
    assert classify_key_failure(502, "") == "transient"
    assert classify_key_failure(None, "network") is None
    print("ok classify")


def case_rotate_on_dead() -> None:
    # 401 → 立即换 key#2 重发成功；重发不消耗 retries；Authorization 头确实换了
    llm, pool = _llm_with_pool(["sk-AAAA", "sk-BBBB"])
    opener = _Opener([_http_error(401, "invalid api key"), OK])
    llm._opener = opener
    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        got = llm._post("/chat/completions", {"model": "m"}, retries=0)  # retries=0 也能救回
    assert got == OK, got
    assert llm.api_key == "sk-BBBB"
    assert opener.auth_seen == ["Bearer sk-AAAA", "Bearer sk-BBBB"], opener.auth_seen
    assert len(pool.events) == 1 and pool.events[0]["fp8"] != "sk-BBBB"  # 只存指纹
    print("ok rotate_on_dead")


def case_quota_429_rotates_but_ratelimit_not() -> None:
    # 配额类 429 → 切
    llm, _ = _llm_with_pool(["sk-AAAA", "sk-BBBB"])
    llm._opener = _Opener([_http_error(429, "insufficient_quota"), OK])
    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        assert llm._post("/chat/completions", {}) == OK
    assert llm.api_key == "sk-BBBB"
    # 纯限速 429 → 不切，走退避后仍用原 key 重试
    llm2, _ = _llm_with_pool(["sk-CCCC", "sk-DDDD"])
    llm2._opener = _Opener([_http_error(429, "Rate limit reached"), OK])
    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        assert llm2._post("/chat/completions", {}) == OK
    assert llm2.api_key == "sk-CCCC", "纯限速不得轮换 key"
    print("ok quota_vs_ratelimit")


def case_5xx_never_rotates() -> None:
    llm, _ = _llm_with_pool(["sk-AAAA", "sk-BBBB"])
    llm._opener = _Opener([_http_error(500, "boom"), OK])
    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        assert llm._post("/chat/completions", {}) == OK
    assert llm.api_key == "sk-AAAA", "5xx 不得轮换 key"
    print("ok 5xx_never_rotates")


def case_all_keys_dead() -> None:
    llm, pool = _llm_with_pool(["sk-AAAA", "sk-BBBB"])
    llm._opener = _Opener([_http_error(402, "arrearage"), _http_error(401, "dead")])
    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        try:
            llm._post("/chat/completions", {})
        except LLMError as exc:
            assert not exc.retryable, "全耗尽必须不可重试"
            assert "全部不可用" in str(exc)
        else:
            raise AssertionError("全 key 耗尽应抛 LLMError")
    assert pool.exhausted
    print("ok all_keys_dead")


def case_platform_gateway_forces_single() -> None:
    env = {"LLM_API_KEY": "sk-platform", "LLM_API_KEYS": "sk-self1,sk-self2"}
    pool = pool_from_env(env, "https://llm.tsinghua.edu.cn/v1")
    assert len(pool) == 1 and pool.current() == "sk-platform", "平台网关下必须锁单 key"
    # 自备网关：LLM_API_KEY 为主，LLM_API_KEYS 为备选，去重
    pool2 = pool_from_env(env, "https://dashscope.aliyuncs.com/compatible-mode/v1")
    assert [pool2.current()] == ["sk-platform"] and len(pool2) == 3
    assert pool2.rotate("t") and pool2.current() == "sk-self1"
    # 未配 LLM_API_KEYS → 单 key，rotate 恒 False
    pool3 = pool_from_env({"LLM_API_KEY": "sk-only"}, "https://api.deepseek.com/v1")
    assert len(pool3) == 1 and not pool3.rotate("t")
    print("ok platform_gateway_forces_single")


def case_no_pool_byte_compat() -> None:
    # key_pool=None：401 直接抛（现状行为），不旋转
    llm = DeepSeekLLM(api_key="sk-X", model="m", base_url="https://example.invalid/v1", timeout=1.0)
    llm._opener = _Opener([_http_error(401, "bad"), OK])
    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        try:
            llm._post("/chat/completions", {}, retries=0)
        except LLMError as exc:
            assert exc.status == 401
        else:
            raise AssertionError("无 pool 时 401 应直接抛")
    assert llm.api_key == "sk-X"
    print("ok no_pool_byte_compat")


def case_thread_safety() -> None:
    import threading

    pool = KeyPool([f"sk-{i}" for i in range(8)])
    results = []

    def worker():
        for _ in range(3):
            pool.rotate("race")
            results.append(pool.current())

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert pool.exhausted, "4 线程×3 次 rotate 后应耗尽且不越界"
    assert len(pool.events) == 7  # 8 个 key 只能成功轮转 7 次
    print("ok thread_safety")


def main() -> int:
    case_classify()
    case_rotate_on_dead()
    case_quota_429_rotates_but_ratelimit_not()
    case_5xx_never_rotates()
    case_all_keys_dead()
    case_platform_gateway_forces_single()
    case_no_pool_byte_compat()
    case_thread_safety()
    print("smoke_keypool: ALL PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
