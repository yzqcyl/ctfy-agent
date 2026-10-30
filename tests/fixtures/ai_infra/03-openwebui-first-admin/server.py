#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 基建靶子 03：Open WebUI 形状（首个注册用户即 admin → 管理面翻凭据）。

对应 `src/ctfy_agent/tools_local.py` 的指纹规则：
  `/api/config` 正文含 `"features"` → 组件 Open WebUI。
对应 next 打法（_FINGERPRINT_RULES 第 4 条）：
  `首个注册用户即 admin：POST /api/v1/auths/signup，然后翻 /api/v1/ 管理面`。

答案形态：自然语言值（不是 FLAG{...}）。
启动：python server.py [port]（默认 0 = 动态端口）。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ADMIN_KEY = "sk-openwebui-admin-4e2c"
LEGACY_KEY = "webui-legacy-key-91ac"  # 管理面里翻到的目标答案
_state_lock = threading.Lock()
_state = {"admin_created": False}


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
            self._send(200, b"<!doctype html><title>Open WebUI</title>", ctype="text/html; charset=utf-8")
        elif path == "/api/config":
            self._send(200, {
                "version": "0.5.24",
                "features": {"auth": True, "signup": True, "auth_trusted_header": False},
                "default_models": [],
                "name": "Open WebUI",
            })
        elif path == "/api/v1/auths/":
            auth = self.headers.get("Authorization") or ""
            if auth == f"Bearer {ADMIN_KEY}":
                self._send(200, {"users": [
                    {"email": "ops@internal", "role": "admin", "api_key": ADMIN_KEY},
                    {"email": "legacy@internal", "role": "user", "api_key": LEGACY_KEY},
                ]})
            else:
                self._send(401, {"detail": "Unauthorized"})
        else:
            self._send(404, {"detail": "Not Found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/api/v1/auths/signup":
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                return self._send(400, {"detail": "Bad Request"})
            email = str(req.get("email") or "").strip()
            if "@" not in email:
                return self._send(400, {"detail": "Invalid email"})
            with _state_lock:
                first = not _state["admin_created"]
                if first:
                    _state["admin_created"] = True
            if first:
                return self._send(200, {
                    "id": "u-0001", "email": email, "name": str(req.get("name") or ""),
                    "role": "admin", "api_key": ADMIN_KEY, "token": ADMIN_KEY,
                })
            return self._send(403, {"detail": "Registration is disabled by the administrator."})
        self._send(404, {"detail": "Not Found"})


def serve(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, srv.server_address[1]


if __name__ == "__main__":
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv, bound = serve(want)
    print(f"[server] openwebui-shape on http://127.0.0.1:{bound}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
