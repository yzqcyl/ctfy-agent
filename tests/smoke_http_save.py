#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_http_save.py — http_request 的 save 参数（二进制原始字节落盘）回归。

背景（实证）：CYBENCH-004 是 Rust ELF 题，模型只能 shell curl 下载；CYBENCH-029 是 pcap，
正文走 utf-8 解码后二进制被替换字符破坏。save 让「下载→分析」成为一步。

覆盖 8 条：
  1) 二进制响应按原始字节落盘（0x7f ELF 魔数不被 utf-8 破坏）
  2) 返回 saved/sha256/magic/head_hex
  3) 魔数识别：ELF / ZIP / gzip / PNG / PCAP
  4) 文本响应也走 save（magic=文本）
  5) 自动建父目录
  6) save 优先于 grep/offset（不混着渲染）
  7) 4xx 错误体也能落盘（分析错误页/被删文件）
  8) save 目标是目录时报错

运行: python tests/smoke_http_save.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools, ToolError  # noqa: E402

FAILS: list[str] = []
ELF = b"\x7fELF\x02\x01\x01\x00" + bytes(range(64))
ZIP = b"PK\x03\x04" + b"\x00" * 60
GZ = b"\x1f\x8b\x08\x00" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
PCAP = b"\xd4\xc3\xb2\xa1" + b"\x00" * 60

ROUTES: dict[str, tuple[int, bytes]] = {
    "/elf": (200, ELF),
    "/zip": (200, ZIP),
    "/gz": (200, GZ),
    "/png": (200, PNG),
    "/pcap": (200, PCAP),
    "/text": (200, "hello 你好 flag{abc}".encode("utf-8")),
    "/denied": (403, b"403 forbidden: secret=hint"),
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        status, body = ROUTES.get(self.path, (404, b"nope"))
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}"

    # 注意：%TEMP% 在本机被 AV/沙箱拒绝写入（Error 5），scratch 一律放工作区 runs/ 下
    tmp = ROOT / "runs" / "_smoke_save"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    store = Store(tmp / "store")
    tools = LocalTools(store, "save-01", traffic_enabled=False)
    tools.allow_instance("127.0.0.1")

    out = json.loads(tools.http_request(f"{base}/elf", save=str(tmp / "bin" / "nettools")))
    saved = pathlib.Path(out["saved"])
    check("二进制按原始字节落盘", saved.exists() and saved.read_bytes() == ELF)
    check("摘要含 sha256/magic/head_hex",
          out.get("sha256") == hashlib.sha256(ELF).hexdigest() and "ELF" in out.get("magic", "")
          and out.get("head_hex", "").startswith("7f454c46"),
          json.dumps(out, ensure_ascii=False)[:200])
    check("自动建父目录", saved.parent.name == "bin")

    kinds = {}
    for name in ("zip", "gz", "png", "pcap", "text"):
        o = json.loads(tools.http_request(f"{base}/{name}", save=str(tmp / f"{name}.bin")))
        kinds[name] = o.get("magic")
    check("魔数识别 zip/gzip/png/pcap",
          "ZIP" in kinds["zip"] and "gzip" in kinds["gz"] and "PNG" in kinds["png"] and "PCAP" in kinds["pcap"],
          str(kinds))
    check("文本型标为文本", kinds["text"] == "文本", kinds["text"])

    o = json.loads(tools.http_request(f"{base}/text", grep="flag", save=str(tmp / "t.txt")))
    check("save 优先于 grep（不回正文渲染）", "saved" in o and "body" not in o, json.dumps(o, ensure_ascii=False)[:160])
    check("文本落盘内容正确", (tmp / "t.txt").read_text(encoding="utf-8").startswith("hello 你好"))

    o = json.loads(tools.http_request(f"{base}/denied", save=str(tmp / "denied.html")))
    saved_denied = pathlib.Path(o["saved"])
    check("4xx 错误体也能落盘", o["status"] == 403 and saved_denied.read_bytes().startswith(b"403 forbidden"),
          json.dumps(o, ensure_ascii=False)[:160])

    try:
        tools.http_request(f"{base}/elf", save=str(tmp))
        check("save 目标是目录时报错", False, "没有抛错")
    except ToolError as exc:
        check("save 目标是目录时报错", "目录" in str(exc), str(exc)[:100])

    server.shutdown()
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_http_save 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())