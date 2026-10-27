#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：无进展刹车（M5.6）——原地打转要提前止损，正常推进不能被误伤。

背景（真机证据）：
  - 039：连发 8 次 `GET /playground?message=…`（GET 不执行消息，页面永远不变），
    整题烧到 266k token 仍未解。
  - 043：同一页面换 grep/offset 连读 3 次，每步仍付 ~7k prompt。
账本去重只按「参数完全相同」判重，抓不到「换参数读同一份内容」；本用例钉住补充信号：
**工具输出摘要与此前相同 = 这一步没产生新信息** → 3 步提示换方向、5 步提前止损。
"""
from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.config import load as load_config  # noqa: E402
from ctfy_agent.platform import CtfyPlatform  # noqa: E402
from ctfy_agent.solver import NO_PROGRESS_STOP_AFTER, Solver  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402
from ctfy_agent.testing import fake_llm  # noqa: E402
from ctfy_agent.testing.fake_llm import tool_call  # noqa: E402

STUCK_BODY = b"<html><body><p>same page, no new info</p></body></html>"


class _Handler(BaseHTTPRequestHandler):
    """GET /stuck 永远返回同一页；GET /page?i=N 每次返回不同正文（对照组）。"""

    hits: dict[str, int] = {}

    def log_message(self, *args):  # noqa: D102
        pass

    def _send(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        from urllib.parse import urlparse  # noqa: PLC0415

        path = urlparse(self.path)
        key = path.path
        _Handler.hits[key] = _Handler.hits.get(key, 0) + 1
        if key == "/stuck":
            self._send(STUCK_BODY)
        elif key == "/page":
            index = path.query.split("=")[-1] or "0"
            self._send(f"<html><body><p>content {index}</p></body></html>".encode("utf-8"))
        else:
            self._send(b"not found")


def _setup(tmp: Path, port: int):
    server, url = fake_llm.start_fake_mcp()
    config = load_config()
    config.mcp_url = url
    config.token = "pf_test_token_123456"
    config.runs_dir = str(tmp)
    config.model = "deepseek-v4.1-flash"
    platform = CtfyPlatform(url, config.token)
    platform.connect()
    store = Store(tmp / "brake").load()
    store.challenges.clear()
    challenge = store.upsert(
        {
            "id": "web-01",
            "name": "Stuck Page",
            "category": "web",
            "points": 100,
            "description": "页面不会因为 GET 参数而变化。",
        }
    )
    # upsert 只认白名单字段，实例信息要直接挂上（run_role 用 challenge.instance 登记可访问端点）
    challenge.instance = {"id": "inst-1", "surface": {"services": [
        {"service_type": "http", "host": "127.0.0.1", "port": port}
    ]}}
    store.save()
    return server, config, platform, store


def case_no_progress_brake(tmp: Path) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)

    server, port = ThreadingHTTPServer(("127.0.0.1", 0), _Handler), 0
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    fake_server = None
    try:
        fake_server, config, platform, store = _setup(tmp, port)
        solver = Solver(config, platform, fake_llm.ScriptedLLM([]), store)
        base = f"http://127.0.0.1:{port}"

        # ---- 1) 原地打转：同一 URL 反复 GET，输出永远一样 ----
        _Handler.hits.clear()
        stuck = fake_llm.ScriptedLLM(
            [{"content": "", "tool_calls": [tool_call("http_request", {"url": base + "/stuck"})]}
             for _ in range(12)]
        )
        result = solver.run_role(
            "web-01",
            role="ai",
            llm=stuck,
            system="s",
            task="t",
            specs=[],
            max_steps=12,
            platform_names=solver._platform_names(),
        )
        assert result["reason"] == "no_progress", result
        # 首发是全文、后续是缓存命中桩（文字不同）→ 会多算 1 步，因此留 +2 余量；
        # 关键是「远早于 max_steps=12」就停住。
        assert result["steps"] <= NO_PROGRESS_STOP_AFTER + 2, f"刹车没提前停：{result['steps']} 步"
        assert result["steps"] < 12, f"没有提前止损：{result['steps']} 步"
        # 账本 + 刹车双保险：同一 URL 实际只打出去 1 次
        assert _Handler.hits.get("/stuck", 0) == 1, f"重复请求还是发出去了：{_Handler.hits}"
        assert "无进展刹车" in result["report"], result["report"][:200]

        # ---- 2) 对照：每步都有新内容，不能误判 ----
        _Handler.hits.clear()
        advancing = fake_llm.ScriptedLLM(
            [{"content": "", "tool_calls": [tool_call("http_request", {"url": f"{base}/page?i={i}"})]}
             for i in range(12)]
        )
        result2 = solver.run_role(
            "web-01",
            role="ai",
            llm=advancing,
            system="s",
            task="t",
            specs=[],
            max_steps=6,
            platform_names=solver._platform_names(),
        )
        assert result2["reason"] == "step_limit", f"正常推进被误刹车：{result2}"
        assert result2["steps"] == 6, f"对照组应跑满步数：{result2['steps']}"

        label = "无进展刹车：输出重复 3 步提示、5 步止损；内容有变化时不误伤"
        if callable(reporter):
            reporter(label)
        else:
            print(f"PASS {label}")
    finally:
        server.shutdown()
        server.server_close()
        if fake_server is not None:
            fake_server.shutdown()
            fake_server.server_close()


if __name__ == "__main__":
    import traceback

    try:
        case_no_progress_brake(ROOT / "dist" / "_brake_standalone")
        print("OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
