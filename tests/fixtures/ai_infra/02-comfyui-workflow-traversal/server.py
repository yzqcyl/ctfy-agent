#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 基建靶子 02：ComfyUI 形状（工作流参数路径穿越读文件）。

对应 `src/ctfy_agent/tools_local.py` 的指纹规则：
  `/system_stats` 正文含 `comfyui` → 组件 ComfyUI（版本正则同时提取 `0.6.1`）；
  首页正文含 `__comfyui_frontend_version__` → crawl 页面级强特征（`_PAGE_MARKERS`）。
对应 next 打法（_FINGERPRINT_RULES 第 2 条）：
  `模型参数 ckpt_name/lora_name 试路径穿越`——工作流输入里的 `../` 序列会越出模型目录。

答案形态：自然语言值（不是 FLAG{...}）。
启动：python server.py [port]（默认 0 = 动态端口）。
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FILES: dict[str, str] = {
    "/srv/comfyui/secret_note.txt": "comfyui-drop-token-3f9b\n",
}
INDEX_HTML = (
    b"<!doctype html><html><head><title>ComfyUI</title></head><body>\n"
    b"<script>window.__COMFYUI_FRONTEND_VERSION__ = \"1.17.6\";</script>\n"
    b"<div id='comfyui-workflow'>workflow editor</div></body></html>\n"
)


def _walk_inputs(value, out: list):
    if isinstance(value, dict):
        for v in value.values():
            _walk_inputs(v, out)
    elif isinstance(value, list):
        for v in value:
            _walk_inputs(v, out)
    elif isinstance(value, str):
        out.append(value)


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
            self._send(200, INDEX_HTML, ctype="text/html; charset=utf-8")
        elif path == "/system_stats":
            self._send(200, {
                "system": {"os": "linux", "ram_total": 33566268416,
                           "comfyui_version": "0.6.1", "version": "0.6.1",
                           "comfyui_commit_hash": "c0ffea5"},
                "devices": [{"name": "cuda:0", "type": "cuda", "vram_total": 8589934592}],
            })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        if path == "/prompt":
            try:
                req = json.loads(raw.decode("utf-8") or "{}")
            except Exception:  # noqa: BLE001
                return self._send(400, {"error": "bad json"})
            values: list[str] = []
            _walk_inputs(req.get("prompt") or req, values)
            traversed = [v for v in values if "../" in v]
            if not traversed:
                return self._send(400, {"error": "workflow has no loadable inputs"})
            # 穿越路径越出模型目录：按虚拟 FS 兜底读
            for v in traversed:
                for fpath, content in FILES.items():
                    if v.endswith(fpath.split("/")[-1]) and "../" in v:
                        return self._send(200, {
                            "prompt_id": "p-4f2a1c", "number": 1, "node_errors": {},
                            "status": {"status_str": "done", "completed": True, "messages": [
                                {"type": "dependency_read", "path": fpath, "content": content.strip()},
                            ]},
                        })
            return self._send(400, {"error": "model file not found",
                                    "searched": traversed[:3]})
        self._send(404, {"error": "not found"})


def serve(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    return srv, srv.server_address[1]


if __name__ == "__main__":
    want = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv, bound = serve(want)
    print(f"[server] comfyui-shape on http://127.0.0.1:{bound}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
