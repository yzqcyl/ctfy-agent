#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_http_cache.py — O1 验收：多 pattern grep（O1a）+ 非 200 响应缓存/fresh（O1b）。

规格：docs/spec-o1-http-cache.md §5（12 条验收）。
全部离线：grep 渲染直接调 `LocalTools._render_http`；缓存用 127.0.0.1 上的真实小服务 + 计数。
运行: python tests/smoke_http_cache.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.tools_local import CACHE_MAX_ERROR_ENTRIES, LocalTools  # noqa: E402

FAILS: list[str] = []
STATE = {"get_hits": 0, "post_hits": 0}

# 固定正文：flag 在 200 偏移、token 在 60、admin 在 320、set-cookie 在 500
BODY = (
    "x" * 200 + "flag{multi_pattern_hit}" + "y" * 140 + "token=abc123" + "z" * 100 + "admin_panel" + "w" * 120
)


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802
        STATE["get_hits"] += 1
        path = self.path.split("?")[0]
        if path == "/js":
            self._send(200, BODY)
        elif path == "/missing":
            self._send(404, "no such page")
        elif path == "/denied":
            self._send(403, "forbidden")
        else:
            self._send(200, "ok")

    def do_POST(self):  # noqa: N802
        STATE["post_hits"] += 1
        self._send(200, "posted")

    def _send(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_http_cache"
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


def _local(tmp: pathlib.Path, port: int, cache: dict | None = None) -> LocalTools:
    store = Store(tmp / "store").load()
    store.challenges.clear()
    local = LocalTools(store, "web-01", cache=cache if cache is not None else {}, traffic_enabled=False)
    local.allow_instance("127.0.0.1", port)
    return local


def _render(payload: str, grep, **kw) -> dict:
    return json.loads(LocalTools._render_http(200, {}, payload, grep, **kw))


def _run(tmp: pathlib.Path, port: int) -> int:
    base = f"http://127.0.0.1:{port}"

    # ---- 1) 多 pattern OR 命中、偏移升序、去重、每 pattern ≤8 条 ----
    out = _render(BODY, ["flag\\{[^}]+\\}", "token=[a-z0-9]+", "admin_panel"])
    counts = {item["pattern"]: item["count"] for item in out["grep_matched"]}
    check("多 pattern 各自命中计数", counts == {"flag\\{[^}]+\\}": 1, "token=[a-z0-9]+": 1, "admin_panel": 1}, str(counts))
    offsets = [int(line.split("]")[0].lstrip("[")) for line in out["body"].splitlines() if line.startswith("[")]
    check("输出按偏移升序", offsets == sorted(offsets), str(offsets))
    # 同偏移重叠：两个 pattern 命中同一位置，只保留首个
    dup = _render("aaa flag{x}", ["flag\\{[^}]+\\}", "flag\\{x\\}"])
    check("同偏移重叠只保留首个 pattern", dup["matched"] == 1, str(dup["matched"]))
    # 两个互不重叠的 pattern：各自 8 条上限
    many = _render("A" * 20 + "\n" + "B" * 20, ["A", "B"])
    per = {item["pattern"]: item["count"] for item in many["grep_matched"]}
    check("每 pattern 命中上限 8 条", per == {"A": 8, "B": 8}, str(per))

    # ---- 2) 全局 30 条上限 + grep_truncated ----
    wide = _render(" ".join(["ab"] * 40), ["ab"])
    check("单 pattern 全局限 30（旧路径）", wide["matched"] == 30 and wide["truncated"] is True, str(wide["matched"]))
    # 4 个 pattern × 各 8 条 = 32 条候选 → 全局截到 30
    multi_wide = _render("A" * 20 + "B" * 20 + "C" * 20 + "D" * 20, ["A", "B", "C", "D"])
    check("多 pattern 全局限 30 条", multi_wide["matched"] == 30 and multi_wide["truncated"] is True, str(multi_wide["matched"]))
    check("被截断 pattern 报 grep_truncated", "grep_truncated" in multi_wide and multi_wide["grep_truncated"], str(multi_wide.get("grep_truncated")))

    # ---- 3) 非法正则只影响自身 ----
    bad = _render("flag{ok} token=x", ["flag\\{", "a(", "b)["])
    check("非法正则不打断其余 pattern", bad["matched"] >= 1 and len(bad.get("grep_errors") or []) == 2, json.dumps(bad.get("grep_errors"), ensure_ascii=False))

    # ---- 4) 单 pattern 输出与旧实现逐字节一致 ----
    payload = "a" * 500 + "flag{abc}" + "b" * 500
    match = __import__("re").search(r"flag\{[^}]+\}", payload)
    expected = {
        "status": 200, "headers": {}, "grep": "flag\\{[^}]+\\}", "matched": 1, "truncated": False,
        "body": f"[{match.start()}] " + payload[max(0, match.start() - 80): match.end() + 80],
    }
    check("单 pattern 输出逐字节兼容", LocalTools._render_http(200, {}, payload, "flag\\{[^}]+\\}") == json.dumps(expected, ensure_ascii=False))

    # ---- 5) 7 个 pattern 截断到 6 并提示 ----
    seven = _render(BODY, ["flag", "token", "admin", "x", "y", "z", "w"])
    check("pattern 超 6 个被截断并提示", len(seven["grep"]) == 6 and seven.get("grep_dropped") == 1, json.dumps({k: seven[k] for k in ("grep_dropped",)}, ensure_ascii=False))

    # ---- 12) grep + offset：先切窗口再搜，偏移报绝对位置 ----
    token_at = BODY.index("token")
    windowed = _render(BODY, "token", offset=240, limit=400)
    win_offsets = [int(line.split("]")[0].lstrip("[")) for line in windowed["body"].splitlines() if line.startswith("[")]
    check("grep+offset 只搜窗口内且报绝对偏移", win_offsets == [token_at], f"{win_offsets} != {[token_at]}")
    # 窗口不含匹配点 → 无命中（token 在 363，窗口从 400 起）
    skipped = _render(BODY, "token", offset=400, limit=200)
    check("grep+offset 窗口外的命中不返回", skipped["matched"] == 0, str(skipped["matched"]))

    # ---- 6) GET 404 二次请求走缓存桩（opener 计数=1）----
    local = _local(tmp, port)
    STATE["get_hits"] = 0
    first = json.loads(local.http_request(f"{base}/missing"))
    second = json.loads(local.http_request(f"{base}/missing"))
    check("404 首次真实请求", first["status"] == 404 and STATE["get_hits"] == 1, str((first["status"], STATE["get_hits"])))
    check("404 二次命中缓存桩", second.get("cached") is True and second["status"] == 404 and STATE["get_hits"] == 1, str(second))
    check("非 200 桩提示 fresh=1", "fresh=1" in second.get("note", ""), second.get("note"))

    # ---- 7) fresh=1 强制重取（opener 计数=2）----
    refreshed = json.loads(local.http_request(f"{base}/missing", fresh=True))
    check("fresh=1 跳过缓存重取", refreshed.get("cached") is None and STATE["get_hits"] == 2, str((refreshed.get("cached"), STATE["get_hits"])))

    # ---- 8) POST 不缓存：同参两次 POST 都要真实发出 ----
    STATE["post_hits"] = 0
    local.http_request(f"{base}/post", method="POST", body="x=1")
    local.http_request(f"{base}/post", method="POST", body="x=1")
    check("POST 不缓存（两次都发网络）", STATE["post_hits"] == 2, str(STATE["post_hits"]))

    # ---- 9) 403/404 探测刷屏不挤占 200 条目 ----
    cache: dict = {}
    scratch = _local(tmp, port, cache=cache)
    scratch._cache_put(("h", "/real", "", ()), {"status": 200, "headers": {}, "payload": "real", "content_length": None})
    for i in range(40):
        scratch._cache_put(("h", f"/nope{i}", "", ()), {"status": 404, "headers": {}, "payload": "nf", "content_length": None})
    kept_errors = [k for k, e in cache.items() if (e.get("status") or 200) != 200]
    check("200 条目未被 403/404 挤掉", any(k[1] == "/real" for k in cache), str(list(cache)[:3]))
    check("总容量仍受 CACHE_MAX_ENTRIES 约束", len(cache) <= 16, str(len(cache)))
    check("非 200 条目全部可回收", len(kept_errors) <= 15, str(len(kept_errors)))

    # ---- 11) 多角色共享同一 cache → 第二次请求命中 ----
    shared: dict = {}
    STATE["get_hits"] = 0
    web = _local(tmp, port, cache=shared)
    rev = _local(tmp, port, cache=shared)
    web.http_request(f"{base}/js")
    stub = json.loads(rev.http_request(f"{base}/js"))
    check("同题跨角色共享缓存命中", stub.get("cached") is True and STATE["get_hits"] == 1, str((stub.get("cached"), STATE["get_hits"])))

    # ---- 10) 跨题隔离：_solve_once 开头清空缓存（源码契约）----
    src = (ROOT / "src" / "ctfy_agent" / "solver.py").read_text(encoding="utf-8")
    start = src.find("def _solve_once")
    block = src[start: start + 3000] if start >= 0 else ""
    check("_solve_once 开头清空缓存（跨题隔离）", "_http_cache.clear()" in block, "未找到 _http_cache.clear()")

    # ---- 13) 账本交互：多 pattern（greps）与 fresh 都进去重键 ----
    from ctfy_agent.ledger import attempt_key  # noqa: PLC0415

    plain = attempt_key("http_request", {"url": "http://a/x", "grep": "flag"})
    multi = attempt_key("http_request", {"url": "http://a/x", "greps": ["flag", "token"]})
    multi_again = attempt_key("http_request", {"url": "http://a/x", "greps": ["flag", "token"]})
    check("greps 数组进账本键且同参撞键", plain != multi and multi == multi_again, f"{plain[:40]} | {multi[:40]}")
    fresh_key = attempt_key("http_request", {"url": "http://a/x", "fresh": True})
    plain2 = attempt_key("http_request", {"url": "http://a/x"})
    check("fresh=1 与普通 GET 键不同（不被误拦）", fresh_key != plain2, f"{fresh_key[:40]} | {plain2[:40]}")

    # ---- 14) http_request 端到端：greps 数组走工具面，且缓存命中时仍可多搜 ----
    shared_g: dict = {}
    STATE["get_hits"] = 0
    grep_local = _local(tmp, port, cache=shared_g)
    first = json.loads(grep_local.http_request(f"{base}/js", greps=["flag\\{[^}]+\\}", "token=[a-z0-9]+", "admin_panel"]))
    check("greps 端到端多命中", first.get("matched") == 3 and len(first.get("grep_matched") or []) == 3, json.dumps(first.get("grep_matched"), ensure_ascii=False))
    cached_grep = json.loads(grep_local.http_request(f"{base}/js", greps=["admin_panel"]))
    check("缓存命中也支持换 greps 重搜", cached_grep.get("matched") == 1 and STATE["get_hits"] == 1, str((cached_grep.get("matched"), STATE["get_hits"])))

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_http_cache 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())