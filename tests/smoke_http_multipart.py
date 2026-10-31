#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_http_multipart.py — O-F 验收：http_request 的 multipart `files` / `form` 参数。

覆盖 6 条：
  1) files + form 拼出合法 multipart，服务端能解出字段名/文件名/内容
  2) content_b64 二进制原样到达（字节级一致）
  3) path 来源读沙箱内文件（不必先 base64）
  4) files 与 body 互斥（报错而不是静默丢弃）
  5) path 越界被拒（不许读沙箱外文件）
  6) Content-Type 自动带 boundary；模型显式传的 Content-Type 不被覆盖

全部离线：127.0.0.1 小服务。运行: python tests/smoke_http_multipart.py
"""
from __future__ import annotations

import base64
import json
import pathlib
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools, ToolError  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or ""
        body = {
            "content_type": ctype,
            "parts": _parse_multipart(raw, ctype),
        }
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _parse_multipart(raw: bytes, ctype: str) -> list[dict]:
    """极简 multipart 解析：只为断言字段名/文件名/内容，不追求规范完备。"""
    marker = "boundary="
    if marker not in ctype:
        return []
    boundary = ctype.split(marker, 1)[1].split(";")[0].strip().strip('"')
    parts: list[dict] = []
    for chunk in raw.split(b"--" + boundary.encode()):
        chunk = chunk.strip(b"\r\n")
        if not chunk or chunk == b"--":
            continue
        if b"\r\n\r\n" not in chunk:
            continue
        head, content = chunk.split(b"\r\n\r\n", 1)
        head_text = head.decode("utf-8", "replace")
        name = ""
        filename = None
        for line in head_text.splitlines():
            low = line.lower()
            if low.startswith("content-disposition:"):
                for item in line.split(";"):
                    item = item.strip()
                    if item.startswith("name="):
                        name = item.split("=", 1)[1].strip('"')
                    elif item.startswith("filename="):
                        filename = item.split("=", 1)[1].strip('"')
        parts.append({
            "name": name,
            "filename": filename,
            "content_b64": base64.b64encode(content).decode(),
            "size": len(content),
        })
    return parts


def _body(raw: str) -> dict:
    """http_request 返回渲染后的 JSON 串：外层 {status, headers, body}，body 里才是服务端 JSON。"""
    outer = json.loads(raw)
    return json.loads(outer["body"])


def _local(tmp: pathlib.Path, port: int) -> LocalTools:
    store = Store(tmp / "store").load()
    store.challenges.clear()
    local = LocalTools(store, "web-01", traffic_enabled=False)
    local.allow_instance("127.0.0.1", port)
    return local


def _run(tmp: pathlib.Path, port: int) -> int:
    base = f"http://127.0.0.1:{port}"
    local = _local(tmp, port)

    # ---- 1) files + form：字段名/文件名/内容都对 ----
    resp = _body(local.http_request(
        f"{base}/upload", method="POST",
        form={"csrf": "tok123", "action": "upload"},
        files=[{"field": "f", "filename": "shell.php", "content": "<?php echo 1; ?>"}],
    ))
    parts = {p["name"]: p for p in resp["parts"]}
    check("multipart 字段名齐全", {"csrf", "action", "f"} <= set(parts), str(list(parts)))
    check("form 文本字段值正确", base64.b64decode(parts["csrf"]["content_b64"]) == b"tok123")
    check("文件名保留", parts["f"]["filename"] == "shell.php", str(parts["f"]["filename"]))
    check("文件内容正确", base64.b64decode(parts["f"]["content_b64"]) == b"<?php echo 1; ?>")
    check("Content-Type 带 boundary", "multipart/form-data" in resp["content_type"] and "boundary=" in resp["content_type"])

    # ---- 2) content_b64 二进制原样 ----
    blob = bytes(range(256))
    resp2 = _body(local.http_request(
        f"{base}/upload", method="POST",
        files=[{"field": "bin", "filename": "x.png", "content_b64": base64.b64encode(blob).decode()}],
    ))
    got = base64.b64decode(resp2["parts"][0]["content_b64"])
    check("二进制内容字节级一致", got == blob, f"{got[:8]!r} vs {blob[:8]!r}")

    # ---- 3) path 来源读沙箱文件 ----
    sandbox = local._sandbox_dir()
    sandbox.mkdir(parents=True, exist_ok=True)
    (sandbox / "shell.php").write_text("<?php system($_GET[1]); ?>", encoding="utf-8")
    resp3 = _body(local.http_request(
        f"{base}/upload", method="POST",
        files=[{"field": "f", "filename": "shell.php", "path": "shell.php"}],
    ))
    check("path 来源读到沙箱文件", b"system($_GET[1])" in base64.b64decode(resp3["parts"][0]["content_b64"]))

    # ---- 4) files 与 body 互斥 ----
    try:
        local.http_request(f"{base}/upload", method="POST", body="a=1",
                           files=[{"field": "f", "filename": "a.txt", "content": "x"}])
        check("files 与 body 互斥报错", False, "没报错")
    except ToolError as exc:
        check("files 与 body 互斥报错", "互斥" in str(exc), str(exc)[:80])

    # ---- 5) path 越界被拒 ----
    try:
        local.http_request(f"{base}/upload", method="POST",
                           files=[{"field": "f", "filename": "x", "path": "../../../etc/passwd"}])
        check("path 越界被拒", False, "没报错")
    except ToolError as exc:
        check("path 越界被拒", "越界" in str(exc), str(exc)[:80])

    # ---- 6) 显式 Content-Type 不被覆盖 ----
    resp4 = _body(local.http_request(
        f"{base}/upload", method="POST", headers={"Content-Type": "multipart/form-data; boundary=fake"},
        files=[{"field": "f", "filename": "a.txt", "content": "x"}],
    ))
    check("显式 Content-Type 不被覆盖", "boundary=fake" in resp4["content_type"], resp4["content_type"])

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_http_multipart 全部通过")
    return 0


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_multipart"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        return _run(tmp, port)
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())