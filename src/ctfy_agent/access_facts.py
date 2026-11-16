# -*- coding: utf-8 -*-
"""接入事实（platform access facts）：把平台回执里「怎么连到靶机」翻成模型能直接照做的话。

背景（2026-11-12 CTFY「Platform Playground」接入测试，实测证据，不是猜的）：

1. ``get_competition`` 里有比赛级接入约束，旧版完全没采集：
   - ``tcp_access = "vpn"``：TCP 服务只能经平台 VPN 访问（本机直连 ``*.internal`` 必失败）；
   - ``agent_internet = "deny"``：靶机无外网 → OOB 只能用平台回调地址；
   - ``practice_instance_cap = 1`` / ``max_instances_per_team_effective = 3``：并发实例上限；
   - ``practice_ttl_s = 3600``：实例存活时间。
2. ``start_instance`` 回执的 ``surface`` 里有真实连接面：
   ``services[{service_type, host, port, label, access}]``（access 可能是 ``vpn``）、
   ``provided_files[]``（``per_team=true`` 的附件只能用实例附件工具下载）、
   ``vpn{proto, host, port, config_url, callback_host, callback_peer}``
   —— ``callback_host/callback_peer`` 是 OOB 题的回调身份，旧版从未给过模型。
3. 旧版 ``_wait_instance_ready`` 一律用 ``http://host`` 探测第一个服务：
   tcp/udp 实例（SAMPLE-016 的 ``tcp :1337``、SAMPLE-015 的 UDP）会白等 45s 并刷失败日志。

本模块只做**纯函数**（无网络、无状态），方便单测；调用方负责把行拼进提示词/工具回执。
"""

from __future__ import annotations

from typing import Any, Iterable

_REACH = {
    "vpn": "经平台 VPN（直连 *.internal 会 DNS/连接失败；本机跑必须先连 VPN，比赛容器内看平台注入的隧道）",
    "public": "独立公网端口（无需 VPN）",
    "direct": "直接可达（无需 VPN）",
}


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt_seconds(seconds: int) -> str:
    if seconds <= 0:
        return "未知"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}min"


def competition_access_lines(comp: dict | None) -> list[str]:
    """比赛级接入约束 → 提示行。空/缺字段时返回 []（绝不猜）。"""
    if not isinstance(comp, dict):
        return []
    lines: list[str] = []

    tcp = str(comp.get("tcp_access") or "").strip().lower()
    if tcp:
        lines.append(f"TCP 接入：{tcp} —— {_REACH.get(tcp, '按平台说明')}")

    internet = str(comp.get("agent_internet") or "").strip().lower()
    if internet == "deny":
        lines.append(
            "靶机出网：deny —— 目标没有外网；带外交互/OOB 只能回连平台给的回调地址"
            "（见实例 surface.vpn.callback_host / callback_peer），别指望靶机访问公网。"
        )

    cap = _as_int(comp.get("practice_instance_cap"))
    team_cap = _as_int(comp.get("max_instances_per_team_effective")) or _as_int(comp.get("max_instances_per_team"))
    if cap > 0:
        lines.append(f"实例并发：practice_instance_cap={cap}（练习场同一时刻最多 {cap} 个实例，开新的前先停旧的）")
    if team_cap > 0:
        lines.append(f"团队实例并发：max_instances_per_team={team_cap}")

    ttl = _as_int(comp.get("practice_ttl_s"))
    if ttl > 0:
        lines.append(f"实例存活：practice_ttl_s={ttl}（{_fmt_seconds(ttl)}），到期会被回收，长题记得 renew_instance")

    install = str(comp.get("solve_env_mode") or "").strip().lower()
    if install and install != "normal":
        lines.append(f"环境模式：solve_env_mode={install}（平台在题目放题/关闭窗口上有宽限语义，遇到 403/404 先看阶段）")

    # 分场次（session）的比赛：局间 start/submit 会被 403 competition_paused，重试无用。
    # 2026-11-13 练习场实测（4h 一局）：局间文案 "This competition is paused between sessions
    # — play resumes at 2026-11-13T04:00:00+00:00. Wait; do not retry sooner."
    sessions = comp.get("sessions")
    next_start = str(comp.get("next_session_starts_at") or "").strip()
    play_open = comp.get("play_open")
    if play_open is False and next_start:
        lines.append(
            f"当前不可玩（play_open=false）：下一局 {next_start}（UTC）开始。"
            "局间 start_instance/submit 会被 403 competition_paused —— 重试无用，先做不依赖实例的事。"
        )
    elif play_open is False:
        lines.append("当前不可玩（play_open=false）：局间 start/submit 会被 403 competition_paused，重试无用。")
    if isinstance(sessions, list) and sessions:
        spans = []
        for item in sessions[:6]:
            if isinstance(item, dict) and item.get("starts_at"):
                spans.append(f"{item.get('starts_at')}→{item.get('ends_at') or '?'}")
        if spans:
            lines.append("分场次：" + "；".join(spans) + "（UTC）—— 局间暂停，实例会被停/删。")
    return lines


def playability_blockers(competition: dict | None, challenge: dict | None) -> list[str]:
    """平台/题目级「现在根本不该开跑」的硬阻塞（与难度无关，跑也只会白烧预算）。

    2026-11-16 实测（TPCTF 练习场关闭后重跑 5 题）：``play_open=false``、题目
    ``closed=true`` / ``scoring=closed``，start/submit 一律 403 ``competition_paused``，
    但 GUI 批量解题仍给每道题开满 20 分钟预算、烧掉 41~79 万 token，最后显示成
    「未解出 / 时间预算到」——把「题不可玩」误报成「解题失败」。这里把判定抽成纯函数，
    调用方在起预算/派活**之前**先问一次，命中就立刻标记跳过（0 token）。

    返回空列表 = 可以开跑；每条是一条人类可读的阻塞原因。
    """
    blockers: list[str] = []
    comp = competition if isinstance(competition, dict) else {}
    info = challenge if isinstance(challenge, dict) else {}

    if comp.get("play_open") is False:
        next_start = str(comp.get("next_session_starts_at") or "").strip()
        tail = f"，下一局 {next_start}（UTC）开始" if next_start else ""
        blockers.append(f"比赛当前暂停（play_open=false{tail}）；start/submit 会 403 competition_paused")

    if info.get("closed") is True:
        blockers.append("题目已关闭（closed=true）")

    scoring = str(info.get("scoring") or "").strip().lower()
    if scoring in ("closed", "disabled", "off", "paused"):
        blockers.append(f"题目已停止计分（scoring={scoring}）")

    if info.get("my_fully_solved") is True:
        blockers.append("平台记录本题已解出（my_fully_solved=true），无需重跑")
    return blockers


def instance_access_lines(instance: dict | None) -> list[str]:
    """实例 surface → 可直接照做的连接说明（服务清单 / VPN / 回调 / per-team 附件）。"""
    if not isinstance(instance, dict):
        return []
    lines: list[str] = []

    surface = instance.get("surface") if isinstance(instance.get("surface"), dict) else {}
    services = surface.get("services") or instance.get("services") or []
    for svc in services:
        if not isinstance(svc, dict):
            continue
        kind = str(svc.get("service_type") or "tcp").lower()
        host = str(svc.get("host") or "").strip()
        port = svc.get("port")
        label = str(svc.get("label") or "")
        access = str(svc.get("access") or "").strip().lower()
        if not host:
            continue
        target = f"{host}:{port}" if port else host
        extra = []
        if label:
            extra.append(label)
        if access:
            extra.append(f"access={access}")
        if kind in ("http", "https"):
            extra.append(f"URL={kind}://{target}{svc.get('path') or '/'}")
        suffix = f"（{', '.join(extra)}）" if extra else ""
        lines.append(f"服务：{kind} {target}{suffix}")

    access_values = {str(s.get("access") or "").strip().lower()
                     for s in services if isinstance(s, dict)}
    hosts = [str(s.get("host") or "") for s in services if isinstance(s, dict)]
    if "vpn" in access_values or any(h.endswith(".internal") for h in hosts):
        lines.append(
            "⚠ 上面有 access=vpn / *.internal 的 host：**必须已连平台 VPN** 才能解析与连接；"
            "连不上时先确认 VPN（不要反复换端口重试），仍不通就按「接入层不可达」收口。"
        )

    vpn = surface.get("vpn") or {}
    if isinstance(vpn, dict) and vpn:
        proto = str(vpn.get("proto") or "udp")
        vhost = str(vpn.get("host") or "")
        vport = vpn.get("port")
        seg = [f"proto={proto}", f"host={vhost}:{vport}" if vhost else "host=?"]
        if vpn.get("config_url"):
            seg.append(f"config_url={vpn.get('config_url')}")
        lines.append("VPN 隧道：" + "，".join(seg))
        cb_host = str(vpn.get("callback_host") or "").strip()
        cb_peer = str(vpn.get("callback_peer") or "").strip()
        if cb_host or cb_peer:
            lines.append(
                "回调身份（OOB 题用）："
                + (f"你的监听在平台侧叫 {cb_host}" if cb_host else "")
                + (f"，对方看到的来源是 {cb_peer}" if cb_peer else "")
                + " —— 把监听绑 0.0.0.0，让靶机回连这个地址，别写 localhost/127.0.0.1。"
            )

    files = surface.get("provided_files") or []
    per_team = [f for f in files if isinstance(f, dict) and f.get("per_team")]
    if per_team:
        names = ", ".join(str(f.get("name") or "") for f in per_team)
        lines.append(
            f"实例附件（per_team）：{names} —— 这类文件每次实例化才生成，"
            "用 list_instance_attachments / download_instance_attachment 拿（拿题目附件接口会 404）。"
        )

    flag_path = str(surface.get("flag_path") or "").strip()
    if flag_path:
        lines.append(f"平台标记的 flag 路径：{flag_path}")
    return lines


def readiness_probes(services: Iterable[Any] | None) -> list[dict]:
    """把实例服务列表翻成「就绪探测计划」。

    只探测能判断应用是否起来的协议：
    - http/https → GET 首页（旧行为）；
    - tcp → socket connect（旧版拿 http:// 去打，必然失败还白等）；
    - udp → 不探测（UDP 没有握手，发一个空包反而污染题目日志）。
    返回 [{"kind": "http"|"tcp", "host", "port", "scheme"|"service_type"}]，按服务顺序去重。
    """
    plan: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for svc in services or []:
        if not isinstance(svc, dict):
            continue
        host = str(svc.get("host") or "").strip()
        if not host:
            continue
        kind = str(svc.get("service_type") or "").strip().lower()
        port = _as_int(svc.get("port"))
        key = (kind, host, port)
        if key in seen:
            continue
        seen.add(key)
        if kind in ("http", "https"):
            plan.append({"kind": "http", "host": host, "port": port or (443 if kind == "https" else 80),
                         "scheme": kind})
        elif kind in ("tcp", "") or not kind:
            if port:
                plan.append({"kind": "tcp", "host": host, "port": port, "service_type": "tcp"})
        # udp / 其它：不探测
    return plan