#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""04 参考解：probe 定型 vLLM/LiteLLM → 默认管理 key sk-1234 打 /key/info 拿虚拟 key。"""
from __future__ import annotations

import json
import re
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))
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
        tools = LocalTools(_Store(), "fixture-infra-04")
        tools.allow_instance("127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"

        # 1) 指纹：probe 定型 vLLM/LiteLLM
        raw = tools.http_map(base + "/", mode="probe")
        payload = json.loads(raw)
        comps = payload.get("components") or []
        assert "vLLM/LiteLLM" in comps, f"probe 未定型 vLLM/LiteLLM：components={comps}；raw[:300]={raw[:300]}"
        hit = next(h for h in payload.get("hits", []) if h.get("component") == "vLLM/LiteLLM")
        assert hit["path"] == "/v1/models", f"vLLM/LiteLLM 特征路径不对：{hit}"

        # 2) 打法（next 第 6 条）：默认管理 key sk-1234 打管理面
        resp = tools.http_request(
            base + "/key/info", method="GET",
            headers={"Authorization": "Bearer sk-1234"},
        )
        match = re.search(r"litellm-virtual-token-\w+", resp)
        if not match:
            print(f"[fail] /key/info 没吐虚拟 key：{resp[:300]}", file=sys.stderr)
            return 1
        print("[infra] litellm")
        print(match.group(0))
        return 0
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
