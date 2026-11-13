#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_access_facts.py — 平台接入事实（接入约束 / surface 解析 / 就绪探测计划）回归。

背景（2026-11-12 CTFY「Platform Playground」接入测试）：
- `get_competition` 返回 `tcp_access=vpn` / `agent_internet=deny` / `practice_instance_cap=1` /
  `practice_ttl_s=3600` —— 旧版完全没采集，模型不知道要先连 VPN、也不知道实例并发只有 1。
- `start_instance` 返回的 `surface` 里有服务清单（tcp/udp/http, access=vpn）、
  `vpn{proto,host,port,callback_host,callback_peer}`、`provided_files[{per_team:true}]` ——
  这些是「怎么连 / OOB 回调是谁 / per-team 附件怎么拿」的唯一可靠事实，旧版原样丢 JSON 给模型。
- 旧 `_wait_instance_ready` 对所有服务一律 `GET http://host/`：tcp 实例白等 45s、udp 实例更没意义。

本测试钉住纯函数行为（无网络）：
  1) 比赛级约束翻译（vpn/deny/并发/TTL）；
  2) 实例 surface 翻译（服务、VPN 告警、回调身份、per-team 附件、flag_path）；
  3) 就绪探测计划（http/https/tcp 各自方式，udp 不探测，去重）；
  4) Solver 的 _probe_tcp / _probe_http 对不可达目标返回 False 且不抛。

运行: python tests/smoke_access_facts.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.access_facts import (  # noqa: E402
    competition_access_lines,
    instance_access_lines,
    readiness_probes,
)
from ctfy_agent.solver import Solver  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def joined(lines: list[str]) -> str:
    return "\n".join(lines)


def main() -> int:
    # ---------------- 1) 比赛级接入约束 ----------------
    comp = {
        "tcp_access": "vpn",
        "agent_internet": "deny",
        "practice_instance_cap": 1,
        "max_instances_per_team_effective": 3,
        "practice_ttl_s": 3600,
    }
    lines = competition_access_lines(comp)
    text = joined(lines)
    check("tcp_access=vpn 翻成 VPN 提示", "vpn" in text.lower() and "VPN" in text, text)
    check("agent_internet=deny 翻成 OOB 回调提示", "deny" in text and "回调" in text, text)
    check("practice_instance_cap=1 被点出", "practice_instance_cap=1" in text, text)
    check("practice_ttl_s=3600 写成 1h", "1h" in text, text)
    check("max_instances_per_team 被点出", "max_instances_per_team=3" in text, text)
    check("空/非法比赛信息不抛且返回 []", competition_access_lines(None) == []
          and competition_access_lines({}) == [] and competition_access_lines("x") == [])
    check("tcp_access=public 的说明不含 *.internal 警告", "*.internal" not in joined(
        competition_access_lines({"tcp_access": "public"})))

    # ---------------- 1b) 局间暂停（CTFY competition_paused） ----------------
    paused = competition_access_lines({
        "play_open": False,
        "next_session_starts_at": "2026-11-13T04:00:00Z",
        "sessions": [
            {"starts_at": "2026-11-13T00:00:00Z", "ends_at": "2026-11-13T03:45:00Z"},
            {"starts_at": "2026-11-13T04:00:00Z", "ends_at": "2026-11-13T07:45:00Z"},
        ],
    })
    ptext = joined(paused)
    check("play_open=false + 下一局时间 → 明确「不可玩/重试无用」",
          "play_open=false" in ptext and "2026-11-13T04:00:00Z" in ptext
          and "competition_paused" in ptext, ptext)
    check("分场次表被点出", "分场次" in ptext and "00:00:00Z→" in ptext, ptext)
    check("没有 next_session 也能说清暂停语义",
          "competition_paused" in joined(competition_access_lines({"play_open": False})))
    check("play_open=true 不出暂停提示",
          "competition_paused" not in joined(competition_access_lines({"play_open": True})))
    real_pause = ("[工具报错] ----- 工具返回 (isError=true) ----- Error calling tool 'start_instance': "
                  "competition_paused: This competition is paused between sessions — play resumes at "
                  "2026-11-13T04:00:00+00:00. Wait; do not retry sooner.")
    check("_is_competition_paused 认真实 403 文案", Solver._is_competition_paused(real_pause) is True)
    check("_is_competition_paused 不误判配额/瞬时",
          Solver._is_competition_paused("team_capacity_exceeded: Max 1 concurrent instances per team") is False
          and Solver._is_competition_paused("Rate limited by upstream API, please retry later") is False
          and Solver._is_competition_paused("") is False)

    # ---------------- 2) 实例 surface → 接入说明 ----------------
    inst = {
        "id": "782e6892-e55e-4d75-8136-85766877d6fb",
        "surface": {
            "services": [
                {"service_type": "tcp", "host": "srdaqhkfl5agrntxi.internal", "port": 1337,
                 "label": "chal", "access": "vpn"},
                {"service_type": "http", "host": "web.internal", "port": 8080, "path": "/app",
                 "label": "portal", "access": "vpn"},
            ],
            "provided_files": [{"name": "README.txt", "size": 595, "per_team": True}],
            "flag_path": "/flag",
            "vpn": {"proto": "udp", "host": "node2.tpctf.ctfy.run", "port": 42616,
                    "config_url": "", "callback_host": "ctfy-callback",
                    "callback_peer": "172.16.13.254"},
        },
    }
    ilines = instance_access_lines(inst)
    itext = joined(ilines)
    check("tcp 服务写成 host:port", "tcp srdaqhkfl5agrntxi.internal:1337" in itext, itext)
    check("http 服务带 URL", "http://web.internal:8080/app" in itext, itext)
    check("access=vpn/*.internal 有明确警告", "必须已连平台 VPN" in itext, itext)
    check("VPN 隧道端点被点出", "node2.tpctf.ctfy.run:42616" in itext, itext)
    check("OOB 回调身份被点出（callback_host/peer）",
          "ctfy-callback" in itext and "172.16.13.254" in itext and "localhost" in itext, itext)
    check("per-team 附件提示用实例附件工具",
          "per_team" in itext and "download_instance_attachment" in itext, itext)
    check("flag_path 被点出", "/flag" in itext, itext)
    check("空实例不抛", instance_access_lines(None) == [] and instance_access_lines({}) == [])

    # udp-only 实例：不因缺 http 面而炸
    udp_only = {"surface": {"services": [{"service_type": "udp", "host": "udp.internal", "port": 9000,
                                          "access": "vpn"}]}}
    ulines = instance_access_lines(udp_only)
    check("udp 服务也能翻译", any("udp udp.internal:9000" in x for x in ulines), joined(ulines))

    # ---------------- 3) 就绪探测计划 ----------------
    probes = readiness_probes([
        {"service_type": "http", "host": "a.internal", "port": 8080},
        {"service_type": "https", "host": "b.internal", "port": 0},
        {"service_type": "tcp", "host": "c.internal", "port": 1337},
        {"service_type": "udp", "host": "d.internal", "port": 9000},
        {"service_type": "tcp", "host": "c.internal", "port": 1337},  # 重复
        {"service_type": "tcp", "host": "no-port.internal"},
        {"host": ""},
    ])
    kinds = [(p["kind"], p["host"], p["port"]) for p in probes]
    check("http 服务走 HTTP 探测", ("http", "a.internal", 8080) in kinds, str(kinds))
    check("https 缺省端口补 443", ("http", "b.internal", 443) in kinds, str(kinds))
    check("tcp 服务走 TCP 探测", ("tcp", "c.internal", 1337) in kinds, str(kinds))
    check("udp 服务不探测", all(p["host"] != "d.internal" for p in probes), str(kinds))
    check("无端口 tcp / 空 host 被跳过",
          all(p["host"] not in ("no-port.internal", "") for p in probes), str(kinds))
    check("重复服务去重", len(kinds) == len(set(kinds)), str(kinds))
    check("空服务列表返回 []", readiness_probes(None) == [] and readiness_probes([]) == [])

    # ---------------- 4) 探测函数：不可达返回 False 不抛 ----------------
    ok, detail = Solver._probe_tcp({"host": "127.0.0.1", "port": 1})
    check("_probe_tcp 对关闭端口返回 False", ok is False, detail)
    ok2, detail2 = Solver._probe_http({"scheme": "http", "host": "127.0.0.1", "port": 1})
    check("_probe_http 对关闭端口返回 False", ok2 is False, detail2)

    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())