#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 基建靶子 01：Ollama 形状（无鉴权推理 API + 模型读文件）。

对应 `src/ctfy_agent/tools_local.py` 的指纹规则：
  `/api/version` 正文含 `"version"` → 组件 Ollama（版本正则同时提取 `0.1.29`）。
对应 next 打法（_FINGERPRINT_RULES 第 1 条）：
  `POST /api/generate 让本地模型读文件`——靶子里的"模型"会引用虚拟文件系统中的文件。

答案形态：自然语言值（不是 FLAG{...}），与真实 AI 题判分一致。
启动：python server.py [port]（默认 0 = 动态端口，由 solve.py 拿动态口）。
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OLLAMA_VERSION = "0.1.29"
# 虚拟文件系统：模拟"本地模型能读到的服务端文件"
FILES: dict[str, str] = {
    "/srv/ollama/operator_token.txt": "ollama-ops-token-7741\nissued to node-runner-3\n",
}
SECRET_RE_TOKEN = "ollama-ops-token-7741"


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

    def _not_found(self) -> None:
        self._send(404, {"error": "not found"})

    def do_GET(self):  # noqa: N802
        path = self.path.split("?")[0]
        if path == "/api/version":
            self._send(200, {"version": OLLAMA_VERSION})
        elif path == "/api/tags":
            self._send(200, {"models": [
                {"name": "llama3.2:1b", "model": "llama3.2:1b", "size": 2019393189, "digest": "a80c4f17acd5"},
            ]})
        else:
            self._not_found()

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/api/generate":
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                req = {}
            prompt = str(req.get("prompt") or "")
            # "模型"具备文件引用能力：prompt 里提到虚拟 FS 的路径就原样引用其内容
            for fpath, content in FILES.items():
                if fpath in prompt:
                    return self._send(200, {
                        "model": req.get("model") or "llama3.2:1b",
                        "response": f"FILE {fpath} -> {content.strip()}",
                        "done": True,
                    })
            return self._send(200, {"model": req.get("model") or "llama3.2:1b",
                                    "response": "I can quote any file on the server if you name its path.",
                                    "done": True})
        self._not_found()


def serve(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, srv.server_address[1]


if __name__ == "__main__":
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv, bound = serve(want)
    print(f"[server] ollama-shape on http://127.0.0.1:{bound}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
