#!/usr/bin/env python3
"""mock_gateway.py —— 极简 OpenAI 兼容网关桩（本地测试用，无第三方依赖）。

用法：
    python3 mock_gateway.py [端口]        # 默认 18080
然后另开终端：
    export OPENAI_BASE_URL="http://127.0.0.1:18080/v1"
    export OPENAI_API_KEY="pk_mock"
    export OPENAI_MODEL="mock-model"
    python3 tools/linux_bootstrap/gateway_smoke.py
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "mock-model"


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/v1/models", "/models"):
            self._json(200, {"object": "list", "data": [{"id": MODEL}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except Exception:  # noqa: BLE001
            req = {}
        self._json(200, {
            "id": "chatcmpl-mock", "object": "chat.completion",
            "created": int(time.time()), "model": req.get("model", MODEL),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "MOCK_OK"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def log_message(self, fmt, *args):  # noqa: A002
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    print(f"mock gateway 监听 127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
