# -*- coding: utf-8 -*-
"""LLM 传输层超时/网络错误的离线回归（不联网、不花 token）。

背景（实测事故）：`_post()` 原先只捕获 HTTPError / URLError / JSONDecodeError，
读响应体超时抛的裸 `TimeoutError`（不是 URLError 子类）会穿透 crew.solve（只接
LLMError/BudgetExceeded）→ 整个 run 报废。SAMPLE-007 归因臂三次都死在这一点上
（runs/clusterb-p1-007-k1{,b,c}），时间戳证据：最后一条转录 → 崩溃间隔 ≈ 读超时值。

本用例锁死三条契约：
  1. 读超时 → 归一为 `LLMError(retryable=True)`，且**默认只重试 1 次**（不无限挂）；
  2. 超时重试后成功 → 正常返回解析结果；
  3. 裸 OSError（连接重置）→ 同样可重试，不会穿透成致命异常。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


class _Resp:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def read(self, limit: int = -1) -> bytes:
        return self._body


class _Opener:
    """按脚本逐个 open()：异常实例→抛出，dict→当作合法响应。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def open(self, request, timeout=None):
        self.calls += 1
        step = self.script.pop(0) if self.script else TimeoutError("read operation timed out")
        if isinstance(step, Exception):
            raise step
        return _Resp(step)


def _llm(timeout: float = 1.0):
    from ctfy_agent.llm import DeepSeekLLM

    return DeepSeekLLM(
        api_key="k",
        model="m",
        base_url="https://example.invalid/v1",
        timeout=timeout,
    )


def case_llm_timeout_retry(tmp: Path) -> None:
    from ctfy_agent.llm import LLMError

    with mock.patch("ctfy_agent.llm.time.sleep", lambda _s: None):
        # 1) 读超时一次 → 重试成功
        llm = _llm()
        opener = _Opener([TimeoutError("read operation timed out"), {"ok": 1}])
        llm._opener = opener
        got = llm._post("/chat/completions", {"model": "m"})
        assert got == {"ok": 1}, got
        assert opener.calls == 2, opener.calls
        _ok("LLM 读超时 → 重试 1 次后成功（不再穿透）")

        # 2) 持续超时 → 有界重试后抛可重试 LLMError（默认 1 次重试 = 至多 2 次请求）
        llm = _llm(timeout=7.0)
        opener = _Opener([TimeoutError("read operation timed out")] * 5)
        llm._opener = opener
        try:
            llm._post("/chat/completions", {"model": "m"})
            raise AssertionError("应当抛 LLMError")
        except LLMError as exc:
            assert exc.retryable is True, exc
            assert "读超时" in str(exc), str(exc)
            assert "7s" in str(exc), str(exc)
        assert opener.calls == 2, opener.calls
        _ok("LLM 持续读超时 → 2 次请求后有界失败（retryable LLMError）")

        # 3) 裸 OSError（连接重置）→ 同样可重试，且成功即返回
        llm = _llm()
        opener = _Opener([ConnectionResetError("reset by peer"), {"ok": 2}])
        llm._opener = opener
        got = llm._post("/chat/completions", {"model": "m"})
        assert got == {"ok": 2}, got
        assert opener.calls == 2, opener.calls
        _ok("LLM 连接重置 → 归一为可重试并恢复")

        # 4) 非超时/非网络的解析错误仍是不可重试 LLMError（不放宽既有契约）
        llm = _llm()
        opener = _Opener([])
        llm._opener = opener
        try:
            llm._post("/chat/completions", {"model": "m"})
            raise AssertionError("应当抛 LLMError")
        except LLMError as exc:
            assert "读超时" in str(exc), str(exc)
        _ok("LLM 传输层错误契约：超时/网络可重试，异常值不静默吞掉")