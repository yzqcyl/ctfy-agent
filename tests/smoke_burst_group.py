#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_burst_group.py — O2' 验收：http_burst 分组前归一化（消 payload 回显碎片）。

规格：docs/spec-o2-o3-tools.md §2.3（5 条验收）。全部离线：127.0.0.1 小服务 + 计数。
运行: python tests/smoke_burst_group.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools, _burst_group_key  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802
        v = self.path.rstrip("/").split("/")[-1]
        if self.path.startswith("/samebody"):
            # 同一正文，但状态码按值区分（验「状态码不同必须分组」）
            status = 403 if v == "b" else 200
            self._send(status, "identical body")
            return
        verdict = "welcome" if v.startswith("ok") else "denied"
        # 响应回显 payload + 每次不同的时变片段（旧实现会因此每请求一组）
        body = f"result for {v}: {verdict} ts={time.time()} uuid={uuid.uuid4()} epoch={int(time.time() * 1000)}"
        self._send(200, body)

    def _send(self, status: int, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_burst_group"
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


def _local(tmp: pathlib.Path, port: int) -> LocalTools:
    store = Store(tmp / "store").load()
    store.challenges.clear()
    local = LocalTools(store, "web-01", traffic_enabled=False)
    local.allow_instance("127.0.0.1", port)
    return local


def _run(tmp: pathlib.Path, port: int) -> int:
    base = f"http://127.0.0.1:{port}"
    local = _local(tmp, port)

    # ---- 1) 24 个 values、响应回显 payload → 归一化后组数 < 原始组数，raw_count 正确 ----
    vals = [f"ok{i}" if i % 2 == 0 else f"no{i}" for i in range(24)]
    burst = json.loads(local.http_burst(f"{base}/echo/{{v}}", method="GET", values=vals))
    check("回显 payload 不再一值一组", burst["distinct_bodies"] < len(vals), f'{burst["distinct_bodies"]} vs {len(vals)}')
    check("归一化后合并为 2 类结果", burst["distinct_bodies"] == 2, str(burst["distinct_bodies"]))
    check("raw_distinct_bodies 报告原始组数", burst.get("raw_distinct_bodies") == len(vals), str(burst.get("raw_distinct_bodies")))
    check("每组 raw_count 之和 = 请求数", sum(g.get("raw_count", 0) for g in burst["groups"]) == len(vals), str([g.get("raw_count") for g in burst["groups"]]))

    # ---- 2) i 合并后不重复、升序；v 映射回原 values ----
    for g in burst["groups"]:
        check(f"组 i 升序且不重复（{g['status']}）", g["i"] == sorted(set(g["i"])), str(g["i"]))
    merged = [g for g in burst["groups"] if g["count"] > 1]
    check("合并组保留全部成员 v", merged and sorted(v for g in merged for v in g["v"]) == sorted(vals), str(merged))

    # ---- 3) 无 values 的 race 模式输出结构不变（无 raw_count / raw_distinct_bodies）----
    race = json.loads(local.http_burst(f"{base}/echo/race", method="GET", count=4))
    check("race 模式分组字段不变", set(race["groups"][0]) == {"count", "i", "status", "head"}, str(race["groups"][0].keys()))
    check("race 模式无 raw_distinct_bodies", "raw_distinct_bodies" not in race, str(race.keys()))

    # ---- 4) 归一化不误合：状态码不同仍分开 ----
    status_burst = json.loads(local.http_burst(f"{base}/samebody/{{v}}", method="GET", values=["a", "b"]))
    check("同正文不同状态码仍分两组", status_burst["distinct_bodies"] == 2, str(status_burst["distinct_bodies"]))

    # ---- 5) 输出仍是合法 JSON 且可解析（结构完整）----
    check("输出为合法 JSON 且结构完整", isinstance(burst.get("groups"), list) and "status" in burst and "ok" in burst, str(burst.keys()))

    # 单元级：归一化函数本身
    key_a = _burst_group_key("try admin => denied ts=1700000000 uuid=123e4567-e89b-12d3-a456-426614174000", ["admin"])
    key_b = _burst_group_key("try root => denied ts=1700000001 uuid=123e4567-e89b-22d3-a456-426614174001", ["root"])
    check("归一化：payload/时间/UUID 都被抹平", key_a == key_b, f"{key_a} != {key_b}")

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_burst_group 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())