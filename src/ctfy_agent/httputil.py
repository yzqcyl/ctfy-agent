#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""共享 HTTP 传输助手：限读 + 重定向护栏。

解决审计里两类传输层问题：
  1. urllib 默认跟随重定向：MCP/LLM 客户端跨源重定向会外送 Authorization 头；
  2. http_request 的合规白名单只校验初始 URL，重定向目标不受校验。

用法：
  opener = build_opener(SameOriginRedirect(base_url))
  opener = build_opener(GuardedRedirect(check_fn))
"""

from __future__ import annotations

from urllib.parse import urlparse

import urllib.error
import urllib.request

MAX_READ_BYTES = 8 * 1024 * 1024  # MCP / LLM 响应体上限，防止恶意端点撑爆内存


def read_limited(response, limit: int = MAX_READ_BYTES) -> bytes:
    """读 response，最多 limit 字节（超出部分丢弃，返回时已读满 limit）。"""
    return response.read(limit)


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """只跟随同源重定向；跨源直接抛错，防止 Authorization 等凭据头被转发到第三方。"""

    def __init__(self, base_url: str):
        super().__init__()
        origin = urlparse(base_url)
        self._scheme = origin.scheme
        self._host = (origin.hostname or "").lower()
        self._port = origin.port

    def _same_origin(self, url: str) -> bool:
        target = urlparse(url)
        return (
            target.scheme == self._scheme
            and (target.hostname or "").lower() == self._host
            and target.port == self._port
        )

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self._same_origin(newurl):
            raise urllib.error.URLError("跨源重定向已拦截（防止凭据头外送）")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """每次跳转都重新校验目标，且禁止 https→http 降级。check 抛异常即拦截。"""

    def __init__(self, check):
        super().__init__()
        self._check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(req.full_url).scheme == "https" and urlparse(newurl).scheme == "http":
            raise urllib.error.URLError("拒绝从 https 降级到 http 的重定向")
        self._check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener(handler: urllib.request.BaseHandler) -> urllib.request.OpenerDirector:
    """用自定义重定向处理器建 opener（build_opener 不会重复装默认重定向处理器）。"""
    return urllib.request.build_opener(handler)
