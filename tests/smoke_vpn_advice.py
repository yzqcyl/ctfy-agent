#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_vpn_advice.py — O-G 验收：DNS 解析失败时给「内网/VPN 不可达」确定性判据。

背景：P0 校准里 XBOW-001 是 *.internal 内网实例，agent 换了 http_request/http_map/curl/python
四种方式、烧 0.44M token 才确认 DNS 不通。这个判据要写进工具错误文本，让模型一步收口。

覆盖 5 条：
  1) _dns_unreachable_advice 对 getaddrinfo failed 返回「内网隧道」判据
  2) 对 *.internal 域名额外点明「平台内网实例」
  3) 对非 DNS 错误（connection refused）不返回建议
  4) 端到端：http_request 打不可解析的内网域名 → 错误文本含判据与「report」
  5) 端到端：http_map 走同一请求层，错误文本也带判据

运行: python tests/smoke_vpn_advice.py
"""
from __future__ import annotations

import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools, ToolError, _dns_unreachable_advice  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _local(tmp: pathlib.Path) -> LocalTools:
    store = Store(tmp / "store").load()
    store.challenges.clear()
    return LocalTools(store, "web-01", traffic_enabled=False)


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_vpn_advice"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    local = _local(tmp)

    # ---- 1/2/3) 纯函数 ----
    adv = _dns_unreachable_advice("http://abc.internal:80/", "[Errno 11001] getaddrinfo failed")
    check("DNS 失败给判据", "内网隧道" in adv and "report" in adv, adv[:80])
    check("*.internal 额外点明内网实例", "平台内网实例" in adv, adv[:120])
    check("非 DNS 错误不给建议",
          _dns_unreachable_advice("http://a.example/", "Connection refused") == "")

    # ---- 4) 端到端 http_request ----
    host = "ctfy-offline-probe.internal"
    local.allow_instance(host, 80)
    try:
        local.http_request(f"http://{host}/")
        check("http_request 内网域名报错并带判据", False, "没抛错")
    except ToolError as exc:
        msg = str(exc)
        check("http_request 内网域名报错并带判据", "内网隧道" in msg and "report" in msg, msg[:120])

    # ---- 5) 端到端 http_map（同一请求层）----
    try:
        local.http_map(f"http://{host}/", mode="crawl")
        check("http_map 同样带判据", False, "没抛错")
    except ToolError as exc:
        msg = str(exc)
        check("http_map 同样带判据", "内网隧道" in msg, msg[:120])

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_vpn_advice 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())