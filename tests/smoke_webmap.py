#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：http_map 站点成图 / 端点探测 / 白名单 / 角色接线。

背景（2026-10-26 flash 三跑实测）：042/043/044 三题 `http_map` 调用数都是 **0**。
原因是两层都没接上：
  1) crew._role_specs 里没有 http_map 的挂载分支；
  2) roles.<name>.local 白名单里没有 http_map。
本用例把四件事钉死：解析正确、probe 命中、白名单越界被拒、角色真的能看见这个工具。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.crew import Crew  # noqa: E402
from ctfy_agent.tools_local import LocalTools, ToolError  # noqa: E402

HOME_HTML = """<!doctype html><html><body>
<h1>SupportMind</h1>
<a href="/portal/tickets/1">Ticket #1</a>
<a href="/kb/rotating-credentials">KB: rotating credentials</a>
<a href="http://evil.example.com/steal">external</a>
<a href="javascript:void(0)">js link</a>
<form action="/portal/new" method="post">
  <input name="subject" type="text">
  <textarea name="body"></textarea>
  <input type="hidden" name="csrf_token" value="tok-1">
  <select name="priority"><option value="low">low</option><option value="high">high</option></select>
</form>
<script>fetch("/api/tools").then(r => r.json())</script>
</body></html>"""


class _Store:
    def add_note(self, *args, **kwargs):  # noqa: D102
        pass


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def _send(self, status: int, body: bytes, ctype: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/":
            self._send(200, HOME_HTML.encode("utf-8"))
        elif self.path == "/openapi.json":
            self._send(200, b'{"openapi":"3.0.0","paths":{}}', "application/json")
        elif self.path == "/portal/tickets/1":
            self._send(200, b"<p>ticket one</p>")
        else:
            self._send(404, b"not found")


def _serve() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def case_http_map(tmp: Path) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    server, port = _serve()
    try:
        tools = LocalTools(_Store(), f"case-map-{port}")
        tools.allow_instance("127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"

        # ---- 1) crawl：链接/表单/字段/接口线索都要结构化出来 ----
        payload = json.loads(tools.http_map(base + "/"))
        assert payload["status"] == 200, payload
        assert payload["mode"] == "crawl"
        links = payload["links"]
        assert "/portal/tickets/1" in links, f"详情页链接没抽到：{links}"
        assert "/kb/rotating-credentials" in links, f"文档链接没抽到：{links}"
        assert not any("evil.example.com" in item for item in links), f"跨域链接泄漏进地图：{links}"
        assert not any(item.startswith("javascript") for item in links), f"伪协议没被过滤：{links}"
        # 工单/文档类链接应排在前面（优先级排序）
        assert links[0].startswith(("/portal", "/kb")), f"入口链接没排到前面：{links}"

        assert payload["forms"], f"表单没抽到：{payload}"
        form = payload["forms"][0]
        assert form["action"] == "/portal/new", form
        assert form["method"] == "POST", form
        names = [field["name"] for field in form["fields"]]
        for expected in ("subject", "body", "csrf_token", "priority"):
            assert expected in names, f"字段 {expected} 丢了：{names}"
        priority = next(field for field in form["fields"] if field["name"] == "priority")
        assert priority.get("options") == ["low", "high"], f"select 选项没抽到：{priority}"

        assert "/api/tools" in payload["api_hints"], f"JS 接口线索没抽到：{payload['api_hints']}"

        # ---- 2) probe：常见端点里命中的要报状态码 ----
        probe = json.loads(tools.http_map(base + "/", mode="probe"))
        assert probe["mode"] == "probe", probe
        hit_paths = {item["path"]: item["status"] for item in probe["hits"]}
        assert hit_paths.get("/openapi.json") == 200, f"probe 没命中 openapi.json：{probe}"
        assert probe["not_found"] >= 1, f"probe 应记录不存在的路径：{probe}"

        # ---- 3) 白名单：未下发的实例端口必须拒绝 ----
        denied = LocalTools(_Store(), f"case-map-deny-{port}")
        try:
            denied.http_map(base + "/")
        except ToolError as exc:
            assert "实例" in str(exc) or "allow" in str(exc).lower() or "拒绝" in str(exc), exc
        else:
            raise AssertionError("未 allow_instance 的 host 居然能访问")

        label = "http_map：站点成图（链接/表单/字段/接口线索）+ probe + 白名单"
        if callable(reporter):
            reporter(label)
        else:
            print(f"PASS {label}")
    finally:
        server.shutdown()
        server.server_close()


def case_http_map_wiring(tmp: Path) -> None:
    """工具挂载链路：roles.<name>.local 写了 ≠ 模型能看见，必须过 crew._role_specs。"""
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)

    class _Platform:
        def tool_names(self):
            return set()

        def openai_tools_for(self, names):
            return []

    crew = Crew.__new__(Crew)
    crew.platform = _Platform()
    crew.roles = {
        "coordinator": {"local": []},
        "ai": {"local": ["http_request", "http_map", "chat"]},
        "web": {"local": ["http_request", "authdiff", "jsrecon", "sqli"]},
        "misc": {"local": ["shell"]},
    }
    ai_names = {spec["function"]["name"] for spec in crew._role_specs("ai")}
    web_names = {spec["function"]["name"] for spec in crew._role_specs("web")}
    misc_names = {spec["function"]["name"] for spec in crew._role_specs("misc")}
    assert "http_map" in ai_names, f"ai 白名单里有 http_map，但 _role_specs 没挂上：{sorted(ai_names)}"
    assert "http_map" not in web_names, f"web 没开 http_map 却挂上了（越权给工具）：{sorted(web_names)}"
    for tool in ("authdiff", "jsrecon", "sqli"):
        assert tool in web_names, f"web 白名单里有 {tool}，但 _role_specs 没挂上：{sorted(web_names)}"
        assert tool not in misc_names, f"misc 没开 {tool} 却挂上了（越权给工具）：{sorted(misc_names)}"
    assert "report" in ai_names, "专家必须始终有 report（交付通道）"

    # 配置漂移：真跑用的 config/agent.json 里 ai/web 必须真的开着 http_map（P3 三工具同理）
    config = json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    for role_name in ("ai", "web"):
        local = ((config.get("roles") or {}).get(role_name) or {}).get("local") or []
        assert "http_map" in local, (
            f"config/agent.json 的 roles.{role_name}.local 缺 http_map：模型将看不到这个工具"
            "（flash 三跑 0 次调用的直接原因）"
        )
    web_local = ((config.get("roles") or {}).get("web") or {}).get("local") or []
    for tool in ("authdiff", "jsrecon", "sqli"):
        assert tool in web_local, f"config/agent.json 的 roles.web.local 缺 {tool}：模型将看不到这个工具"

    label = "http_map + P3 三工具接线：_role_specs 挂载 + config/agent.json 白名单不漂移"
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


if __name__ == "__main__":
    import traceback

    try:
        case_http_map(ROOT / "dist" / "_webmap_standalone")
        case_http_map_wiring(ROOT / "dist" / "_webmap_wiring")
        print("OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
