#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_netbridge.py — 本地端口桥 + socket 透明改写（无 TUN 环境的 VPN 接入层）。

背景：平台按实例签发 WireGuard 配置（REST /instances/{id}/vpn-config），正式比赛容器里隧道由平台
注入；本机（Windows 非管理员）没有 WireGuard 客户端，所以用用户态 wireproxy 起本地转发端口，
再由 portmap 把 ``host:port`` 透明改写到 ``127.0.0.1:port`` —— 现有工具代码零改动。

本测试不依赖 wireproxy 二进制/网络：
  1) 配置生成：TCP/UDP/回连三类隧道、只挑 access=vpn 或 *.internal、私钥原样带上；
  2) OOB 回连端口从题面 ``ctfy-callback:4444`` 提取；
  3) portmap：getaddrinfo / connect / sendto 三条路径都改写，未命中时原样直通；
  4) bridge_settings：开关/二进制查找/端口前缀环境变量；
  5) 桥的生命周期：二进制缺失、就绪探测、portmap 安装与清理、私钥配置不残留。

运行: python tests/smoke_netbridge.py
"""
from __future__ import annotations

import os
import pathlib
import socket
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import netbridge, portmap  # noqa: E402

FAILS: list[str] = []
VPN_CONF = """[Interface]
Address = 172.16.9.254/32
PrivateKey = SUPER_SECRET_KEY=
DNS = 172.16.5.3, 1.1.1.1

[Peer]
PublicKey = TFpU8/qX1jhuTkwZZqsh2+S0Mioo6o4qtDgwxcbGJnk=
Endpoint = node6.tpctf.ctfy.run:42177
AllowedIPs = 172.16.9.0/24
PersistentKeepalive = 25
"""


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def services() -> list[dict]:
    return [
        {"service_type": "tcp", "host": "abc.internal", "port": 1337, "access": "vpn", "label": "chal"},
        {"service_type": "http", "host": "def.internal", "port": 80, "access": "vpn", "label": "web"},
        {"service_type": "udp", "host": "ghi.internal", "port": 5353, "access": "vpn", "label": "udp"},
        {"service_type": "tcp", "host": "ctf.example.com", "port": 8080, "access": "public", "label": "pub"},
    ]


class Cfg:
    """最小 config 替身：只带 bridge_settings / start_bridge 需要的字段。"""

    def __init__(self, extra: dict | None = None):
        self.extra = extra or {}
        self.mcp_url = "https://ctfy.example/api/v1/mcp/"
        self.token = "tok"


def main() -> int:
    # ---- 1) 配置生成 ----
    text, tunnels = netbridge.build_plan(VPN_CONF, services(), inbound_ports=[4444])
    check("TCP 服务生成 TCPClientTunnel", "[TCPClientTunnel]" in text and "Target = abc.internal:1337" in text)
    check("HTTP 服务也走 TCP 隧道", "Target = def.internal:80" in text)
    check("UDP 服务生成 UDPProxyTunnel", "[UDPProxyTunnel]" in text and "Target = ghi.internal:5353" in text)
    check("OOB 生成 TCPServerTunnel", "[TCPServerTunnel]" in text and "ListenPort = 4444" in text)
    check("公网服务不建隧道", "ctf.example.com:8080" not in text)
    check("私钥原样保留（wireproxy 要用）", "PrivateKey = SUPER_SECRET_KEY=" in text)
    check("补 PersistentKeepalive（用户态隧道长期空闲）",
          "PersistentKeepalive = 25" in text, text)
    check("隧道计数（TCP+UDP+OOB+DNS）", len(tunnels) == 5, str(tunnels))
    check("隧道 local 都指向 127.0.0.1",
          all(str(item["local"]).startswith("127.0.0.1:") for item in tunnels), str(tunnels))
    check("local 端口互不相同", len({item["local"] for item in tunnels}) == len(tunnels))
    dns_tunnels = [item for item in tunnels if item["kind"] == "dns"]
    check("DNS 隧道指向平台解析器",
          bool(dns_tunnels) and dns_tunnels[0]["target"] == "172.16.5.3:53", str(dns_tunnels))
    lines = netbridge.tunnel_lines(tunnels)
    check("提示行覆盖三类形态",
          any("OOB 回调" in line for line in lines)
          and any("UDP 服务" in line for line in lines)
          and any("TCP 服务" in line for line in lines))

    # ---- 1b) 只有题面名字（无 surface.services）也要建隧道；OOB 回调端口要错开本地端口 ----
    names = netbridge.estate_names_from_text('Two names resolve inside the tunnel: `portal.sample023.internal` and `files.sample023.internal`.')
    check('从题面提取 estate 域名', names == ['portal.sample023.internal', 'files.sample023.internal'], str(names))
    text2, tunnels2 = netbridge.build_plan(VPN_CONF, [], inbound_ports=[4444],
                                           estate_names=['portal.sample023.internal'])
    targets2 = {(item['target'], item['kind']) for item in tunnels2}
    check('无服务清单时为 estate 域名建 80/443 隧道',
          ('portal.sample023.internal:80', 'tcp') in targets2
          and ('portal.sample023.internal:443', 'tcp') in targets2, str(sorted(targets2)))
    inbound2 = [item for item in tunnels2 if item['kind'] == 'inbound']
    check('OOB 回调本地端口与 ListenPort 错开',
          bool(inbound2) and inbound2[0]['local'] != '127.0.0.1:4444', str(inbound2))

    # ---- 2) OOB 端口提取 ----
    ports = netbridge.inbound_ports_from_text(
        "The beacon connects to ``ctfy-callback:4444`` and sends the stored document.",
        "或是 callback_host 6000 这种写法")
    check("从题面提取回连端口", ports == [4444, 6000], str(ports))
    check("没有回连端口时为空", netbridge.inbound_ports_from_text("no callback here") == [])

    # ---- 3) 端口映射透明改写 ----
    portmap.restore()
    mapping = portmap.install({("abc.internal", 1337): ("127.0.0.1", 19100)})
    check("install 返回规范化的映射", mapping and ("abc.internal", 1337) in mapping, str(mapping))
    infos = socket.getaddrinfo("abc.internal", 1337, 0, socket.SOCK_STREAM)
    check("getaddrinfo 命中后改写到本地端口", infos[0][4] == ("127.0.0.1", 19100), str(infos[0][4]))
    infos2 = socket.getaddrinfo("localhost", 1337)
    check("未命中时原样直通（不抛错）", isinstance(infos2, list) and len(infos2) > 0)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        local_port = server.getsockname()[1]
        portmap.install({("conn.internal", 1234): ("127.0.0.1", local_port)})
        client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        client.settimeout(2)
        client.connect(("conn.internal", 1234))
        conn, _ = server.accept()
        check("socket.connect 按映射连到本地端口", True)
        conn.close()
        client.close()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_server:
        udp_server.bind(("127.0.0.1", 0))
        udp_port = udp_server.getsockname()[1]
        portmap.install({("udp.internal", 9999): ("127.0.0.1", udp_port)})
        udp_server.settimeout(2)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(b"ping", ("udp.internal", 9999))
        payload, _ = udp_server.recvfrom(64)
        check("socket.sendto 按映射改写到本地端口", payload == b"ping", str(payload))
    portmap.clear()
    check("clear 后映射为空", portmap.current() == {})
    portmap.install({("x.internal", 1): ("127.0.0.1", 2)})
    portmap.restore()
    check("restore 恢复标准库实现", socket.getaddrinfo.__name__ != "_gai")

    # ---- 3b) 内网 DNS：解析器走隧道，未知名按 NXDOMAIN 处理 ----
    import struct
    import threading

    def dns_server(fake: dict[str, str], stop: list[bool]) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server.bind(("127.0.0.1", 0))
        server.settimeout(0.5)
        fake["__port__"] = server.getsockname()[1]
        while not stop:
            try:
                query, addr = server.recvfrom(1024)
            except socket.timeout:
                continue
            except OSError:
                break
            txid = query[:2]
            offset = 12
            labels = []
            while offset < len(query) and query[offset]:
                size = query[offset]
                labels.append(query[offset + 1:offset + 1 + size].decode("latin-1"))
                offset += size + 1
            name = ".".join(labels)
            ip = fake.get(name)
            if ip:
                qname = query[12:offset + 5]
                answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + socket.inet_aton(ip)
                body = txid + struct.pack("!HHHHH", 0x8180, 1, 1, 0, 0) + qname + answer
            else:
                body = txid + struct.pack("!HHHHH", 0x8183, 1, 0, 0, 0) + query[12:]
            server.sendto(body, addr)
        server.close()

    fake = {"portal.sample023.internal": "10.9.8.7"}
    stop_flag: list[bool] = []
    thread = threading.Thread(target=dns_server, args=(fake, stop_flag), daemon=True)
    thread.start()
    for _ in range(50):
        if "__port__" in fake:
            break
        time.sleep(0.02)
    portmap.set_dns_port(fake.get("__port__", 0))
    infos = socket.getaddrinfo("portal.sample023.internal", 80, 0, socket.SOCK_STREAM)
    check("内网 DNS 解析 *.internal", infos[0][4] == ("10.9.8.7", 80), str(infos[0][4]))
    try:
        socket.getaddrinfo("nope.sample023.internal", 80)
        check("NXDOMAIN → 抛 gaierror（不误当可达）", False)
    except socket.gaierror:
        check("NXDOMAIN → 抛 gaierror（不误当可达）", True)
    portmap.set_dns_port(0)
    stop_flag.append(True)
    thread.join(timeout=2)
    # ---- 4) 配置读取 ----
    old_env = {key: os.environ.get(key) for key in ("CTFY_LOCAL_BRIDGE", "CTFY_WIREPROXY")}
    os.environ.pop("CTFY_LOCAL_BRIDGE", None)
    os.environ.pop("CTFY_WIREPROXY", None)
    try:
        check("默认不启用本地桥", netbridge.bridge_settings(Cfg()) is None)
        fake_bin = ROOT / "runs" / "_t_netbridge" / "fake_wireproxy_x"
        fake_bin.parent.mkdir(parents=True, exist_ok=True)
        fake_bin.write_text("x", encoding="utf-8")
        settings = netbridge.bridge_settings(Cfg({"local_bridge": True,
                                                 "wireproxy_path": str(fake_bin)}))
        check("显式 wireproxy_path 生效", settings and settings["binary"] == str(fake_bin), str(settings))
        check("端口前缀默认 19100", settings and settings["port_base"] == 19100, str(settings))
        settings2 = netbridge.bridge_settings(Cfg({"local_bridge": True,
                                                  "local_bridge_port_base": 20000,
                                                  "wireproxy_path": str(fake_bin)}))
        check("端口前缀可配置", settings2 and settings2["port_base"] == 20000, str(settings2))
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    check("MCP 端点 → REST 根", netbridge.rest_base("https://ctfy.example/api/v1/mcp/") == "https://ctfy.example",
          netbridge.rest_base("https://ctfy.example/api/v1/mcp/"))

    # ---- 5) 桥的生命周期（不依赖 wireproxy 二进制） ----
    # 配置/私钥只放仓库内 runs 下：临时目录在沙箱里不可写（workspace-write）
    work = ROOT / "runs" / "_t_netbridge"
    work.mkdir(parents=True, exist_ok=True)

    # 5a) 二进制不存在 → BridgeError，且不残留含私钥的配置
    bridge = netbridge.LocalBridge(str(work / "definitely_missing.exe"), work)
    try:
        bridge.start(VPN_CONF, [{"service_type": "tcp", "host": "abc.internal", "port": 1,
                                 "access": "vpn"}])
        check("wireproxy 二进制缺失 → BridgeError", False)
    except netbridge.BridgeError as exc:
        check("wireproxy 二进制缺失 → BridgeError", "wireproxy" in str(exc), str(exc))
    check("失败后不残留配置（含私钥）", not bridge.config_path.exists())
    bridge.stop()

    # 5b) launch=False：手工占住本地端口，验证「就绪探测 + 装改写 + stop 清理」
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        holder.bind(("127.0.0.1", 0))
        holder.listen(8)
        # build_plan 会随机分配端口；这里改为固定端口以对齐 holder
        local_port = holder.getsockname()[1]
        original_build = netbridge.build_plan
        netbridge.build_plan = lambda vpn, svcs, inbound_ports=None, claimed=None, estate_names=None, **kw: (  # type: ignore[assignment]
            "\n".join([vpn, ""]),
            [{"kind": "tcp", "target": "abc.internal:1337", "local": f"127.0.0.1:{local_port}"},
             {"kind": "udp", "target": "ghi.internal:5353", "local": "127.0.0.1:9"}],
        )
        bridge2 = netbridge.LocalBridge(str(work / "wireproxy-not-needed.exe"), work)
        try:
            lines2 = bridge2.start(VPN_CONF, [], launch=False)
            check("就绪探测通过 → 返回提示行", bool(lines2) and "TCP 服务" in lines2[0], str(lines2))
            active = portmap.current()
            check("TCP/UDP 两类都装进映射",
                  ("abc.internal", 1337) in active and ("ghi.internal", 5353) in active, str(active))
            check("映射指向本机回环", active[("abc.internal", 1337)][0] == "127.0.0.1",
                  str(active.get(("abc.internal", 1337))))
            bridge2.stop()
            check("stop 后映射清空", portmap.current() == {})
            check("stop 后私钥配置删除", not bridge2.config_path.exists())
        finally:
            bridge2.stop()
            netbridge.build_plan = original_build  # type: ignore[assignment]

    # 5c) 端口不通 → BridgeError（就绪等待有界）
    bridge3 = netbridge.LocalBridge(str(work / "wireproxy-not-needed.exe"), work)
    original_build = netbridge.build_plan
    netbridge.build_plan = lambda vpn, svcs, inbound_ports=None, claimed=None, estate_names=None, **kw: (  # type: ignore[assignment]
        "\n".join([vpn, ""]),
        [{"kind": "tcp", "target": "dead.internal:1", "local": "127.0.0.1:9"}],
    )
    try:
        started = time.time()
        try:
            bridge3.start(VPN_CONF, [], launch=False)
            check("端口不通 → BridgeError", False)
        except netbridge.BridgeError as exc:
            check("端口不通 → BridgeError", "端口未就绪" in str(exc), str(exc))
        check("就绪等待有界（<12s）", time.time() - started < 12.0, f"{time.time() - started:.1f}s")
    finally:
        netbridge.build_plan = original_build  # type: ignore[assignment]
        bridge3.stop()
    check("异常路径也不残留私钥配置", not bridge3.config_path.exists())

    # 5d) 找不到 wireproxy 的分支（根目录可注入，避免依赖本机是否装了客户端）
    try:
        netbridge.bridge_settings(Cfg({"local_bridge": True}), root=work / "no_such_root")
        check("指定根目录下找不到 wireproxy → BridgeError", False)
    except netbridge.BridgeError as exc:
        check("指定根目录下找不到 wireproxy → BridgeError", "wireproxy" in str(exc), str(exc))

    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())