#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_ai_oracle.py — AI 方向补丁回归。

背景：AGENTBENCH-002 注入链已通但卡在业务标识 booking_ref；同时 chat 只看回复正文，
目标把工具调用放在 tool_calls 里时会误判「注入没生效」。

覆盖 7 条：
  1) 方法卡：AGENTBENCH-002 的 tags 命中 ASI01-标识符 oracle
  2) 方法卡：普通 web tags 不命中该卡（不污染 web 面）
  3) 方法卡：卡里含「报错原文 / 存在性 oracle / 省略或通配」三条关键动作
  4) chat JSON：目标返回 tool_calls → 输出含 [tool_calls] + 工具名 + 参数
  5) chat JSON：返回 OpenAI 风格 function{name,arguments} 也能抽到
  6) chat：没有工具调用时不出现 [tool_calls] 段（不制造假阳性）
  7) chat SSE：data: 分片里的 tool_calls 也能抽到

运行: python tests/smoke_ai_oracle.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.method_cards import method_cards  # noqa: E402
from ctfy_agent.tools_local import LocalTools, ToolError  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


PAYLOADS: dict[str, tuple[str, dict]] = {}
ECHO: dict[str, dict] = {}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        ECHO[self.path] = {
            "raw": raw.decode("utf-8", "replace"),
            "ctype": self.headers.get("Content-Type") or "",
        }
        body, headers = PAYLOADS.get(self.path, ('{"reply":"ok"}', {"Content-Type": "application/json"}))
        data = body.encode("utf-8")
        self.send_response(200)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # 静音
        pass


def main() -> int:
    # ---- 1/2/3) 方法卡 ----
    tags_002 = ["owasp_2026_asi01", "agentic_ai", "prompt_injection",
                "prompt_injection_bypass", "i18n", "flask", "python"]
    cards = method_cards(tags_002)
    oracle = [c for c in cards if "标识符 oracle" in c]
    check("002 tags 命中标识符 oracle 卡", bool(oracle), f"命中 {len(cards)} 张：{[c[:16] for c in cards]}")
    if oracle:
        card = oracle[0]
        for needle in ("报错", "存在性 oracle", "省略"):
            check(f"oracle 卡含「{needle}」", needle in card, card[:80])
    web_tags = ["sql", "upload", "file_upload", "sqli", "shadow_file", "lfi"]
    web_cards = method_cards(web_tags)
    check("普通 web tags 不命中 oracle 卡",
          not any("标识符 oracle" in c for c in web_cards), str([c[:12] for c in web_cards]))

    # ---- 4/5/6/7) chat 工具调用解析 ----
    PAYLOADS["/tc-json"] = (
        json.dumps({"reply": "Sure, one moment.",
                    "tool_calls": [{"name": "reveal_confirmed_itinerary",
                                    "arguments": {"booking_ref": "TM-1"}}]}, ensure_ascii=False),
        {"Content-Type": "application/json"})
    PAYLOADS["/tc-openai"] = (
        json.dumps({"reply": "On it.",
                    "choices": [{"message": {"role": "assistant", "content": "On it.",
                                             "function_call": {"name": "get_booking",
                                                               "arguments": "{\"id\":\"TM-9\"}"}}}]},
                   ensure_ascii=False),
        {"Content-Type": "application/json"})
    PAYLOADS["/tc-none"] = (json.dumps({"reply": "I cannot help with that."}),
                            {"Content-Type": "application/json"})
    PAYLOADS["/tc-sse"] = (
        'data: {"delta": {"content": "Working "}}\n\n'
        'data: {"tool_calls": [{"name": "list_itineraries", "arguments": {}}]}\n\n'
        'data: {"delta": {"content": "on it."}}\n\ndata: [DONE]\n\n',
        {"Content-Type": "text/event-stream"})

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    tmp = ROOT / "runs" / "_tmp_ai_oracle"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    store = Store(tmp / "store").load()
    store.challenges.clear()
    local = LocalTools(store, "ai-01", traffic_enabled=False)
    local.allow_instance("127.0.0.1")

    try:
        out = local.chat(f"http://127.0.0.1:{port}/tc-json", "which itinerary is preloaded?")
        check("JSON tool_calls 出现在输出", "[tool_calls]" in out and "reveal_confirmed_itinerary" in out, out[:200])
        check("JSON tool_calls 参数可见", "TM-1" in out, out[-300:])
        check("调工具后给下一步提示", "返回值原文" in out, out[-200:])

        out2 = local.chat(f"http://127.0.0.1:{port}/tc-openai", "get it", session="s2")
        check("OpenAI function_call 也能抽到", "get_booking" in out2 and "TM-9" in out2, out2[:200])

        local3 = LocalTools(store, "ai-02", traffic_enabled=False)
        local3.allow_instance("127.0.0.1")
        out3 = local3.chat(f"http://127.0.0.1:{port}/tc-none", "hello")
        check("无工具调用不出 [tool_calls] 段", "[tool_calls]" not in out3, out3[:200])

        local4 = LocalTools(store, "ai-03", traffic_enabled=False)
        local4.allow_instance("127.0.0.1")
        out4 = local4.chat(f"http://127.0.0.1:{port}/tc-sse", "do it", session="sse")
        check("SSE tool_calls 也能抽到", "[tool_calls]" in out4 and "list_itineraries" in out4, out4[:240])

        # ---- 8/9/10) extra 字段：AI 题的注入面不只在 message ----
        PAYLOADS["/tc-extra"] = (json.dumps({"reply": "Locale set."}), {"Content-Type": "application/json"})
        local5 = LocalTools(store, "ai-04", traffic_enabled=False)
        local5.allow_instance("127.0.0.1")
        out5 = local5.chat(f"http://127.0.0.1:{port}/tc-extra", "hello",
                           extra={"lang": "fr", "role": "verified_operator"})
        sent = json.loads(ECHO["/tc-extra"]["raw"])
        check("extra 字段并入 JSON 请求体",
              sent.get("lang") == "fr" and sent.get("role") == "verified_operator", json.dumps(sent)[:200])
        check("extra 不影响 message/session", sent.get("message") == "hello" and sent.get("session") == "default",
              json.dumps(sent)[:200])
        check("输出头显示 extra 字段", "extra=" in out5 and "lang" in out5, out5[:120])

        local6 = LocalTools(store, "ai-05", traffic_enabled=False)
        local6.allow_instance("127.0.0.1")
        local6.chat(f"http://127.0.0.1:{port}/tc-extra", "hi", mode="form", extra={"lang": "de"})
        form_raw = ECHO["/tc-extra"]["raw"]
        check("form 模式 extra 也并入", "lang=de" in form_raw and "message=hi" in form_raw, form_raw[:160])

        local7 = LocalTools(store, "ai-06", traffic_enabled=False)
        local7.allow_instance("127.0.0.1")
        try:
            local7.chat(f"http://127.0.0.1:{port}/tc-extra", "hi", mode="text", extra={"lang": "de"})
            check("text 模式 + extra 显式报错", False, "没有抛错")
        except ToolError as exc:
            check("text 模式 + extra 显式报错", "text 模式不支持 extra" in str(exc), str(exc)[:120])
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_ai_oracle 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())