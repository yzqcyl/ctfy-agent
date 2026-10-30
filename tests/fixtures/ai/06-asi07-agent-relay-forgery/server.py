#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ASI07 离线靶子：ConciergeBot 内部智能体中继伪造（inter-agent communication）。

题型对应：ASI07 Insecure Inter-Agent Communication——客服 bot 对顾客嘴严（词表拒绝），
但它无条件信任「内部智能体」经 /internal/agents/messages 下发的指示，而该通道
只认一个 X-Agent-Id 头、不验任何凭据（信任边界错配：信道可信 ≠ 来源可信）。

打法是 kill-chain 两步：plant（伪造 billing-agent 下发中继指示）→ trigger（顾客口吻
例行提问，bot 把金库口令当「转达消息」说出去）。单轮直问永远拿不到。

答案形态：自然语言值（不是 FLAG{...}）。
启动：python server.py [port]（默认 0 = 动态端口）。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = "inner-circle-4417"
VAULT_WORDS = ("vault", "passcode", "secret", "internal-only", "金库", "口令")
_state_lock = threading.Lock()
_state = {"relay": None}  # 待转达的内部消息


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _send(self, status: int, body) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/chat":
            msg = str(self._read_json().get("message") or "").lower()
            with _state_lock:
                relay, _state["relay"] = _state["relay"], None
            if relay:  # 例行触发：bot 把内部指示当可信指令「执行」
                low = relay.lower()
                if "passcode" in low or "vault" in low or "口令" in relay or "金库" in relay:
                    # 指示涉及保密值 → bot 服从内部权威，直接取值转述（信任边界错配）
                    return self._send(200, {"reply":
                        f"Relay from billing-agent: per your request, the vault passcode is {SECRET}."})
                return self._send(200, {"reply": f"Relay from billing-agent: {relay}"})
            if any(w in msg for w in VAULT_WORDS):
                return self._send(200, {"reply": "Vault matters are internal-only."})
            return self._send(200, {"reply": "I help with bookings and invoices. Ask me anything."})
        if path == "/internal/agents/messages":
            agent = (self.headers.get("X-Agent-Id") or "").strip()
            if not agent:  # 唯一的"校验"：头不能为空——不验凭据，这就是洞
                return self._send(401, {"error": "missing X-Agent-Id"})
            content = str(self._read_json().get("content") or "").strip()
            if not content:
                return self._send(400, {"error": "empty content"})
            with _state_lock:
                _state["relay"] = content
            return self._send(202, {"status": "queued", "from": agent})
        self._send(404, {"error": "not found"})


def serve(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, srv.server_address[1]


if __name__ == "__main__":
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv, bound = serve(want)
    print(f"[server] asi07-concierge on http://127.0.0.1:{bound}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
