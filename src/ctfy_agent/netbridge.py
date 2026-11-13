#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地端口桥：没有 TUN / 没有 WireGuard 客户端时，照样把实例的 VPN 服务接到本机。

背景（2026-11-13 CTFY 练习场接入测试，实测）：
  - 平台对每个实例签发一份 WireGuard 配置：
    ``GET /api/v1/instances/{id}/vpn-config?competition_id=…``（``…/openvpn-config`` 同义）；
  - 正式比赛容器里这条隧道由平台注入，agent 一般不用管；但**本机开发/验收**（Windows 非管理员、
    没有 WireGuard 驱动/NET_ADMIN）连不上，实例服务（``*.internal``）一律不可达。
  - 解决办法：用户态 WireGuard 实现 ``wireproxy``（单文件、免管理员、不动网卡）按实例配置起
    本地 TCP/UDP 转发端口，再由 ``portmap`` 把 ``host:port`` 透明改写到本地端口 —— 现有工具代码
    一行都不用改。

用法（由 solver 在实例就绪后自动调用）：
    start_bridge(config, instance, competition_id, description=...) -> list[str] 提示行
    stop_bridge()   # 释放实例/进程退出时清理

安全：WireGuard 配置含实例私钥，只写在 ``CTFY_BRIDGE_DIR``/用户目录下（权限 0600），
stop 时删除；不会写进仓库（``runs/``、``.tools/`` 已 gitignore）。
"""
from __future__ import annotations

import atexit
import json
import os
import pathlib
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request

from . import portmap

_UA = "ctfy-agent-bridge/1.0"
_ACTIVE: "LocalBridge | None" = None
_ATEXIT_HOOKED = False


class BridgeError(RuntimeError):
    """本地桥起不来（wireproxy 缺失/配置无效/端口不通）。"""


# ---------- 配置读取 ----------

def _flag(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def bridge_settings(config, *, root: pathlib.Path | None = None) -> dict | None:
    """从 config.extra / 环境变量读「是否启用本地桥」；不启用返回 None。

    开关（任一即可）：config.extra["local_bridge"] / 环境变量 CTFY_LOCAL_BRIDGE=1
    路径：config.extra["wireproxy_path"] / CTFY_WIREPROXY / PATH 里的 wireproxy /
          仓库 .tools/wireproxy/wireproxy[.exe]
    """
    extra = getattr(config, "extra", None) or {}
    enabled = _flag(extra.get("local_bridge")) or _flag(os.environ.get("CTFY_LOCAL_BRIDGE"))
    if not enabled:
        return None
    explicit = str(extra.get("wireproxy_path") or os.environ.get("CTFY_WIREPROXY") or "").strip()
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    found = shutil.which("wireproxy")
    if found:
        candidates.append(found)
    root = root or pathlib.Path(__file__).resolve().parents[2]
    candidates += [str(root / ".tools" / "wireproxy" / "wireproxy.exe"),
                   str(root / ".tools" / "wireproxy" / "wireproxy")]
    binary = next((path for path in candidates if path and pathlib.Path(path).is_file()), "")
    if not binary:
        raise BridgeError("找不到 wireproxy 可执行文件（设 CTFY_WIREPROXY 或放 .tools/wireproxy/）")
    work_dir = str(extra.get("local_bridge_dir") or os.environ.get("CTFY_LOCAL_BRIDGE_DIR")
                   or pathlib.Path(tempfile.gettempdir()) / "ctfy_bridge")
    return {"enabled": True, "binary": binary, "work_dir": work_dir,
            "port_base": int(extra.get("local_bridge_port_base")
                             or os.environ.get("CTFY_BRIDGE_PORT_BASE") or 19100)}


# ---------- 平台配置获取 ----------

def rest_base(mcp_url: str) -> str:
    """MCP 端点 → REST 根（``…/api/v1/mcp/`` → ``…``）。"""
    base = str(mcp_url or "").strip().rstrip("/")
    base = re.sub(r"/api/v1/mcp$", "", base)
    return base


def fetch_vpn_config(mcp_url: str, token: str, instance_id: str, competition_id: str,
                     *, timeout: float = 20.0) -> str:
    """取本实例的 WireGuard 配置（含私钥；只在内存/私有目录里用，别打日志）。

    ``…/vpn-config`` 是 openapi.json 里的官方名；``…/openvpn-config`` 是兼容别名（返回同一份 .conf）。
    """
    instance_id = str(instance_id or "").strip()
    if not instance_id:
        raise BridgeError("实例缺 id，无法取 VPN 配置")
    query = f"?competition_id={competition_id}" if competition_id else ""
    url = f"{rest_base(mcp_url)}/api/v1/instances/{instance_id}/vpn-config{query}"
    headers = {"User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - 统一包成 BridgeError，让调用方只处理一种错
        raise BridgeError(f"取 VPN 配置失败（{instance_id}）：{type(exc).__name__}: {exc}") from exc
    if "[Interface]" not in text or "PrivateKey" not in text:
        raise BridgeError(f"VPN 配置格式不符（没有 [Interface]/PrivateKey）：{text[:120]}")
    return text


# ---------- 配置生成 ----------

def _tunnel_services(services: list[dict]) -> list[dict]:
    """挑出需要过隧道的服务：access=vpn 或 host 是 *.internal。"""
    picked = []
    for svc in services or []:
        if not isinstance(svc, dict):
            continue
        host, port = str(svc.get("host") or ""), svc.get("port")
        if not host or not port:
            continue
        access = str(svc.get("access") or "").lower()
        internal = host.lower().endswith(".internal")
        if access != "vpn" and not internal:
            continue
        kind = str(svc.get("service_type") or "tcp").lower()
        if kind in ("http", "https"):
            kind = "tcp"  # HTTP 也是 TCP 隧道，协议由目标端口决定
        if kind not in ("tcp", "udp"):
            continue
        picked.append({"host": host, "port": int(port), "kind": kind,
                       "label": str(svc.get("label") or "")})
    return picked


def inbound_ports_from_text(*texts: str) -> list[int]:
    """从题面里找 OOB 回调端口（``ctfy-callback:4444`` 这类），给 [TCPServerTunnel] 用。"""
    ports: list[int] = []
    for text in texts:
        for match in re.finditer(r"(?:ctfy-callback|callback[_ -]?host)[^\d]{0,24}(\d{2,5})",
                                 str(text or ""), re.I):
            port = int(match.group(1))
            if 1 <= port <= 65535 and port not in ports:
                ports.append(port)
    return ports


_NAME_RE = re.compile(r"\b([a-z0-9][a-z0-9._-]*\.internal)\b", re.I)


def estate_names_from_text(*texts: str) -> list[str]:
    """从题面里抠 estate 内网主机名（``portal.sample023.internal`` 这类）。

    有些题目（如 SAMPLE-023）平台不下发 ``surface.services``，只给 VPN + 一句「estate 自己的
    edge-dns 会解析这两个名字」。没有服务清单就没法预建隧道，但它们一定走标准 HTTP 端口，
    所以在 80/443 上各建一条隧道即可（域名由隧道内 DNS 解析）。
    """
    names: list[str] = []
    for text in texts:
        for match in _NAME_RE.finditer(str(text or "")):
            name = match.group(1).lower().rstrip(".")
            if name not in names:
                names.append(name)
    return names


def _free_port(kind: str = "tcp") -> int:
    socktype = socket.SOCK_DGRAM if kind == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, socktype) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def dns_servers(vpn_conf: str) -> list[str]:
    """从 WireGuard 配置的 ``DNS =`` 行取内网解析器（平台下发 172.16.5.x）。"""
    servers: list[str] = []
    for line in str(vpn_conf or "").splitlines():
        key, _, value = line.partition("=")
        if key.strip().lower() != "dns":
            continue
        for item in value.split(","):
            candidate = item.strip().split()[0] if item.strip() else ""
            try:
                socket.inet_aton(candidate)
            except OSError:
                continue
            if candidate not in servers:
                servers.append(candidate)
    return servers


def build_plan(vpn_conf: str, services: list[dict], *, inbound_ports: list[int] | None = None,
               claimed: set[int] | None = None, include_dns: bool = True,
               estate_names: list[str] | None = None) -> tuple[str, list[dict]]:
    """生成 wireproxy 配置文本 + 隧道清单（不含端口分配副作用：可反复调用做单测）。

    返回 (config_text, tunnels)，tunnels 元素：
      {"kind": "tcp"|"udp"|"inbound"|"dns", "target": "host:port", "local": "127.0.0.1:port"}
    """
    tunnels: list[dict] = []
    lines: list[str] = [vpn_conf.strip(), ""]
    used_ports: set[int] = set(claimed or set())

    def take_port(kind: str = "tcp") -> int:
        for _ in range(20):
            port = _free_port(kind)
            if port not in used_ports:
                used_ports.add(port)
                return port
        raise BridgeError("连续分配本地端口失败")

    for svc in _tunnel_services(services):
        local_port = take_port("udp" if svc["kind"] == "udp" else "tcp")
        target = f"{svc['host']}:{svc['port']}"
        if svc["kind"] == "udp":
            lines += ["[UDPProxyTunnel]", f"BindAddress = 127.0.0.1:{local_port}",
                      f"Target = {target}", "InactivityTimeout = 120", ""]
        else:
            lines += ["[TCPClientTunnel]", f"BindAddress = 127.0.0.1:{local_port}",
                      f"Target = {target}", ""]
        tunnels.append({"kind": svc["kind"], "target": target, "local": f"127.0.0.1:{local_port}"})
    # 没有 surface.services 的 VPN 题：按题面里点名的 estate 域名 + 标准 HTTP 端口补隧道
    known = {f"{item['target'].rsplit(':', 1)[0]}:{item['target'].rsplit(':', 1)[1]}".lower()
             for item in tunnels}
    for name in (estate_names or [])[:4]:
        for port in (80, 443):
            key = f"{name}:{port}"
            if key in known:
                continue
            local_port = take_port("tcp")
            lines += ["[TCPClientTunnel]", f"BindAddress = 127.0.0.1:{local_port}",
                      f"Target = {key}", ""]
            tunnels.append({"kind": "tcp", "target": key, "local": f"127.0.0.1:{local_port}"})
            known.add(key)
    for port in inbound_ports or []:
        if not (1 <= int(port) <= 65535):
            continue
        local_port = take_port("tcp")
        # [TCPServerTunnel] 把「隧道另一侧连到 WG地址:ListenPort」的流量转到本机 Target
        lines += ["[TCPServerTunnel]", f"ListenPort = {int(port)}",
                  f"Target = 127.0.0.1:{local_port}", ""]
        tunnels.append({"kind": "inbound", "target": f"ctfy-callback:{int(port)}",
                        "local": f"127.0.0.1:{local_port}"})
    if include_dns:
        servers = dns_servers(vpn_conf)
        if servers:
            local_port = take_port("udp")
            lines += ["[UDPProxyTunnel]", f"BindAddress = 127.0.0.1:{local_port}",
                      f"Target = {servers[0]}:53", "InactivityTimeout = 60", ""]
            tunnels.append({"kind": "dns", "target": f"{servers[0]}:53",
                            "local": f"127.0.0.1:{local_port}"})
    lines += ["[Resolve]", "ResolveStrategy = auto", ""]
    return "\n".join(lines), tunnels


def tunnel_lines(tunnels: list[dict]) -> list[str]:
    """给模型的提示行（不含私钥/端点主机，只讲怎么连）。"""
    lines: list[str] = []
    for item in tunnels:
        if item["kind"] == "inbound":
            lines.append(f"OOB 回调：靶机拨 {item['target']} → 落到本机 {item['local']}；"
                         f"直接在本机监听该端口即可收到回连")
        elif item["kind"] == "dns":
            lines.append("内网 DNS 已接入（*.internal 走平台解析器；题面说没有公网名字的服务仍解析不到，属正常）")
        elif item["kind"] == "udp":
            lines.append(f"UDP 服务 {item['target']} → 本机 {item['local']}（可用本地端口发数据报）")
        else:
            lines.append(f"TCP 服务 {item['target']} → 本机 {item['local']}"
                         f"（连本地端口或照原 host:port 直连均可，已透明改写）")
    return lines


# ---------- 桥的生命周期 ----------

class LocalBridge:
    """一个实例一个桥：起 wireproxy 子进程 + 装 portmap；停止时清进程/映射/私钥文件。"""

    def __init__(self, binary: str, work_dir: str | pathlib.Path):
        self.binary = str(binary)
        self.work_dir = pathlib.Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.proc: subprocess.Popen | None = None
        self.config_path = self.work_dir / "wireproxy.conf"
        self.log_path = self.work_dir / "wireproxy.log"
        self.tunnels: list[dict] = []
        self.instance_id = ""

    # -- 起 --
    def start(self, vpn_conf: str, services: list[dict], *, instance_id: str = "",
              description: str = "", launch: bool = True) -> list[str]:
        """起桥：写配置 → 启 wireproxy（launch=False 只做配置/端口/改写，测试用）→ 等端口 → 装改写。

        失败一律抛 BridgeError，并且在抛之前删掉含私钥的配置文件。
        """
        self.stop()
        self.instance_id = str(instance_id or "")
        inbound = inbound_ports_from_text(description)
        config_text, tunnels = build_plan(vpn_conf, services, inbound_ports=inbound,
                                          estate_names=estate_names_from_text(description))
        self.config_path.write_text(config_text, encoding="utf-8")
        try:
            os.chmod(self.config_path, 0o600)
        except OSError:
            pass
        self.tunnels = tunnels
        try:
            if launch:
                self._launch()
            self._wait_ready()
            mapping = {(item["target"].rsplit(":", 1)[0], int(item["target"].rsplit(":", 1)[1])):
                       (item["local"].rsplit(":", 1)[0], int(item["local"].rsplit(":", 1)[1]))
                       for item in tunnels if item["kind"] in ("tcp", "udp")}
            portmap.install(mapping)
            dns = [item for item in tunnels if item["kind"] == "dns"]
            portmap.set_dns_port(int(dns[0]["local"].rsplit(":", 1)[1]) if dns else 0)
        except Exception:
            self.stop()
            raise
        return tunnel_lines(tunnels)

    def _launch(self) -> None:
        log_fh = self.log_path.open("a", encoding="utf-8", errors="replace")
        log_fh.write(f"\n==== {time.strftime('%Y-%m-%d %H:%M:%S')} start {self.instance_id} ====\n")
        log_fh.flush()
        try:
            self.proc = subprocess.Popen(  # noqa: S603 - 路径来自受信配置，参数固定
                [self.binary, "-c", str(self.config_path), "-s"],
                stdin=subprocess.DEVNULL, stdout=log_fh, stderr=subprocess.STDOUT,
                cwd=str(self.work_dir), close_fds=True,
            )
        except OSError as exc:
            raise BridgeError(f"启动 wireproxy 失败（{self.binary}）：{exc}") from exc
        finally:
            log_fh.close()

    def _wait_ready(self, timeout: float = 8.0) -> None:
        deadline = time.time() + timeout
        tcp = [item for item in self.tunnels if item["kind"] == "tcp"]
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                raise BridgeError(f"wireproxy 退出（code={self.proc.returncode}）：{self._log_tail()}")
            if all(self._port_open(item["local"]) for item in tcp):
                break
            time.sleep(0.15)
        else:
            raise BridgeError(f"wireproxy 端口未就绪：{self._log_tail()}")
        udp = [item for item in self.tunnels if item["kind"] == "udp"]
        if udp:
            self._warm_up_udp(udp)

    @staticmethod
    def _warm_up_udp(tunnels: list[dict], attempts: int = 4) -> None:
        """UDP 隧道暖机：先发几个哑数据报，逼 wireproxy 先把 WireGuard 握手/DNS 走完。

        实测（SAMPLE-015）：UDP-only 实例上第一、第二个数据报常在握手完成前被丢弃
        （Windows 上表现为 WSAECONNRESET 10054），第三次才拿到回复。暖机把这段不确定性
        收敛在起桥阶段，避免解题时把「隧道还没热」误判成题目不可达。
        """
        for _ in range(attempts):
            for item in tunnels:
                host, port = str(item["local"]).rsplit(":", 1)
                try:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                        sock.settimeout(0.8)
                        sock.sendto(b"\n", (host, int(port)))
                        try:
                            sock.recvfrom(64)
                        except OSError:
                            pass
                except OSError:
                    pass
            time.sleep(0.35)

    @staticmethod
    def _port_open(local: str) -> bool:
        host, port = local.rsplit(":", 1)
        try:
            with socket.create_connection((host, int(port)), timeout=0.5):
                return True
        except OSError:
            return False

    def _log_tail(self, limit: int = 600) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    # -- 停 --
    def stop(self) -> None:
        portmap.clear()
        portmap.set_dns_port(0)
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001 - 清理尽力而为
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        for path in (self.config_path,):  # 配置里有实例私钥，别留盘
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        self.tunnels = []


def _atexit_stop() -> None:
    global _ACTIVE
    if _ACTIVE is not None:
        try:
            _ACTIVE.stop()
        except Exception:  # noqa: BLE001
            pass
        _ACTIVE = None


def start_bridge(config, instance: dict, competition_id: str, *, description: str = "",
                 mcp_url: str | None = None, token: str | None = None) -> list[str]:
    """起（或替换）当前实例的本地桥；返回给模型的提示行。失败抛 BridgeError。"""
    global _ACTIVE, _ATEXIT_HOOKED
    settings = bridge_settings(config)
    if settings is None:
        return []
    instance = instance if isinstance(instance, dict) else {}
    services = [svc for svc in ((instance.get("surface") or {}).get("services") or [])
                if isinstance(svc, dict)]
    wants_vpn = any(str(svc.get("access") or "").lower() == "vpn"
                    or str(svc.get("host") or "").lower().endswith(".internal") for svc in services)
    if not wants_vpn and not inbound_ports_from_text(description) \
            and not estate_names_from_text(description):
        return []  # 既没有过隧道的服务，也没有 OOB 回连端口/estate 域名
    instance_id = str(instance.get("id") or instance.get("instance_id") or "")
    vpn_conf = fetch_vpn_config(str(mcp_url or getattr(config, "mcp_url", "") or ""),
                                str(token or getattr(config, "token", "") or ""),
                                instance_id, competition_id)
    if _ACTIVE is not None:
        _ACTIVE.stop()
    bridge = LocalBridge(settings["binary"], settings["work_dir"])
    lines = bridge.start(vpn_conf, services, instance_id=instance_id, description=description)
    _ACTIVE = bridge
    if not _ATEXIT_HOOKED:
        atexit.register(_atexit_stop)
        _ATEXIT_HOOKED = True
    return ["本机没有 VPN 客户端，已自动建好用户态隧道（效果等同已连平台 VPN）", *lines]


def stop_bridge() -> None:
    global _ACTIVE
    if _ACTIVE is not None:
        try:
            _ACTIVE.stop()
        finally:
            _ACTIVE = None


def active_bridge() -> LocalBridge | None:
    return _ACTIVE