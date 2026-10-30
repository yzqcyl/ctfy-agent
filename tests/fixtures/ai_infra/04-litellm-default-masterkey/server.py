#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 基建靶子 04：LiteLLM 形状（默认管理 key sk-1234 泄虚拟 key）。

对应 `src/ctfy_agent/tools_local.py` 的指纹规则：
  `/v1/models` 正文含 `"data"` → 组件 vLLM/LiteLLM。
对应 next 打法（_FINGERPRINT_RULES 第 6 条）：
  `路径含 litellm 时试其默认管理 key（sk-1234 / 配置里的 master_key）`。

答案形态：自然语言值（不是 FLAG{...}）。
启动：python server.py [port]（默认 0 = 动态端口）。
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MASTER_KEY = "sk-1234"  # LiteLLM 出厂默认 master key（未改配置的实例直接可用）
VIRTUAL_KEY = "litellm-virtual-token-8d5f"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _send(self, status: int, body, ctype: str = "application/json; charset=utf-8") -> None:
        raw = body if isinstance(body, bytes) else (
            body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/":
            self._send(200, b"<!doctype html><title>LiteLLM Proxy</title>", ctype="text/html; charset=utf-8")
        elif path == "/v1/models":
            self._send(200, {
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "object": "model", "owned_by": "litellm-proxy", "created": 1730000000},
                    {"id": "team-embedding", "object": "model", "owned_by": "litellm-proxy", "created": 1730000001},
                ],
            })
        elif path == "/health/liveliness":
            self._send(200, "I'm LiteLLM - Health Check Passed", ctype="text/plain; charset=utf-8")
        elif path == "/key/info":
            auth = self.headers.get("Authorization") or ""
            if auth == f"Bearer {MASTER_KEY}":
                self._send(200, {
                    "key": VIRTUAL_KEY,
                    "expires": "never",
                    "max_parallel_requests": 5,
                    "metadata": {"team": "platform-infra"},
                    "models": [],
                    "info": {"key_alias": "infra-virtual"},
                })
            else:
                self._send(401, {"error": {"type": "auth_error",
                                           "message": "Invalid Authentication. Router auth failed."}})
        else:
            self._send(404, {"detail": "Not Found"})


def serve(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, srv.server_address[1]


if __name__ == "__main__":
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv, bound = serve(want)
    print(f"[server] litellm-shape on http://127.0.0.1:{bound}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
