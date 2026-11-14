#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""socket 地址透明改写：把内网 ``host:port`` 换成 ``127.0.0.1:本地端口``。

用途：本机没有 TUN/WireGuard 客户端（Windows 非管理员是典型场景）时，用用户态隧道
（见 ``netbridge``）把实例的 VPN 服务映射到本地端口；这一层负责让**原有工具代码不用改**——
`socket.create_connection("xxx.internal", 1337)`、`urllib3/requests` 的 `getaddrinfo`、
`pwnlib.remote` 直接 connect，都会落到本地端口。

另外两件事也在这里：
* ``set_dns_port``：把平台下发的内网 DNS（WireGuard 配置里的 ``DNS = 172.16.5.x``）通过隧道
  转成本地 UDP 端口，``getaddrinfo`` 解析不到的名字改走它——否则 ``*.internal`` 只有「已下发
  服务」能解析，任何别的名字都报 DNS 失败，会被误判成「VPN 不可达」。
* 只在 netbridge 真的装了映射时才 patch；映射为空时行为与标准库一致（仅多一次 dict 查找）。
"""
from __future__ import annotations

import socket
import struct
import threading

_LOCK = threading.RLock()  # patched socket 函数会被多线程调用（集群-A 并行路）
_MAP: dict[tuple[str, int], tuple[str, int]] = {}
# 分属主映射：supervisor 并发跑多道网络题时，每条隧道映射各自的实例服务，
# 一个实例释放/换实例只能撤掉自己的映射，不能把别人的一起清掉（旧实现是全局单表）。
_OWNERS: dict[str, dict[tuple[str, int], tuple[str, int]]] = {}
_ORDER: list[str] = []            # 按 install 顺序合并（冲突时先到者保留）
_DNS_OWNERS: dict[str, int] = {}  # 每个属主的内网 DNS 本地端口
_DNS_PORTS: list[int] = []        # 所有在用 DNS 本地端口（按 owner 顺序，查询逐个试）
_CONFLICTS: list[dict] = []       # 跨属主同 host:port 冲突记录（不静默覆盖）
_ORIG: dict[str, object] = {}
_INSTALLED = False
_DNS_PORT = 0
_DNS_SUFFIXES = (".internal", ".ctfy.run", ".tpctf.ctfy.run")
_DNS_TIMEOUT = 3.0


def _norm_host(host) -> str:
    return str(host).strip().lower().rstrip(".")


def _lookup(host, port) -> tuple[str, int] | None:
    """host:port → 本地 (ip, port)；不在映射里返回 None。"""
    try:
        key = (_norm_host(host), int(port))
    except (TypeError, ValueError):
        return None
    return _MAP.get(key)


def _socktype(kind: int) -> int:
    return kind or socket.SOCK_STREAM


def _is_ip_literal(host) -> bool:
    try:
        socket.inet_aton(str(host))
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 内网 DNS

def _build_query(name: str, txid: int) -> bytes:
    labels = [part for part in name.rstrip(".").split(".") if part]
    qname = b"".join(bytes([len(part)]) + part.encode("ascii", "ignore") for part in labels) + b"\x00"
    return struct.pack("!HHHHHH", txid, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", 1, 1)


def _parse_answers(data: bytes) -> tuple[int, list[str]]:
    """返回 (rcode, A 记录列表)；解析失败给 ([], b"") 由调用方兜底。"""
    if len(data) < 12:
        return 2, []
    _txid, flags, qd, an, _ns, _ar = struct.unpack("!HHHHHH", data[:12])
    rcode = flags & 0x000F
    offset = 12
    for _ in range(qd):
        while offset < len(data) and data[offset]:
            offset += data[offset] + 1
        offset += 5  # 结尾 0x00 + QTYPE(2) + QCLASS(2)
    ips: list[str] = []
    for _ in range(an):
        # NAME：可能是压缩指针（2 字节）或逐段标签
        if offset >= len(data):
            break
        if data[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while offset < len(data) and data[offset]:
                offset += data[offset] + 1
            offset += 1
        if offset + 10 > len(data):
            break
        rtype, _rclass, _ttl, rdlen = struct.unpack("!HHIH", data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlen]
        offset += rdlen
        if rtype == 1 and rdlen == 4:
            ips.append(socket.inet_ntoa(rdata))
    return rcode, ips


def _dns_resolve(name: str, ports: list[int] | None = None) -> list[str]:
    """通过隧道里的平台 DNS 解析名字（无连接 UDP，失败返回空列表）。

    并发多题时每实例各有一条 DNS 隧道，端口不同：按 owner 顺序**逐个试**，
    不能只认最后一个（否则 A 题的 *.internal 可能走了 B 实例的解析器）。
    """
    candidates = list(ports or _DNS_PORTS)
    if not candidates:
        return []
    txid = (int.from_bytes(name.encode("utf-8", "ignore")[:2], "big") ^ 0x5A5A) & 0xFFFF
    query = _build_query(name, txid)
    for port in candidates:
        for _attempt in range(2):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.settimeout(_DNS_TIMEOUT)
                sock.sendto(query, ("127.0.0.1", int(port)))
                data, _addr = sock.recvfrom(2048)
            except OSError:
                continue
            finally:
                sock.close()
            rcode, ips = _parse_answers(data)
            if ips:
                return ips
            if rcode == 3:  # NXDOMAIN：确定不存在，换下一个解析器（不同实例的解析域不同）
                break
    return []


def _addrinfo_for(ips: list[str], port, type: int, proto: int) -> list:
    socktype = _socktype(type)
    protocol = proto or (socket.IPPROTO_UDP if socktype == socket.SOCK_DGRAM else socket.IPPROTO_TCP)
    return [(socket.AF_INET, socktype, protocol, "", (ip, port)) for ip in ips]


def _gai(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002 - 对齐标准库签名
    mapped = _lookup(host, port)
    # 只改写 IPv4 目标：内网服务全是 A 记录；强求 AF_INET6 时交回原解析器，避免造出坏 sockaddr
    if mapped is not None and family in (0, socket.AF_INET):
        socktype = _socktype(type)
        protocol = proto or (socket.IPPROTO_UDP if socktype == socket.SOCK_DGRAM else socket.IPPROTO_TCP)
        return [(socket.AF_INET, socktype, protocol, "", mapped)]
    # 未在「已下发服务」里：内网域名先问平台 DNS（比等系统解析器超时快，也不会误报不可达）
    if host and not isinstance(host, (bytes, bytearray)) and _DNS_PORTS and family in (0, socket.AF_INET):
        name = str(host)
        if not _is_ip_literal(name) and _norm_host(name).endswith(_DNS_SUFFIXES):
            ips = _dns_resolve(name)
            if ips:
                return _addrinfo_for(ips, port, type, proto)
    try:
        return _ORIG["gai"](host, port, family, type, proto, flags)
    except socket.gaierror:
        if host and not isinstance(host, (bytes, bytearray)) and _DNS_PORTS and family in (0, socket.AF_INET):
            name = str(host)
            if not _is_ip_literal(name):
                ips = _dns_resolve(name)
                if ips:
                    return _addrinfo_for(ips, port, type, proto)
        raise


def _connect(self, address):
    if isinstance(address, tuple) and len(address) == 2:
        mapped = _lookup(address[0], address[1])
        if mapped is not None:
            address = mapped
    return _ORIG["connect"](self, address)


def _connect_ex(self, address):
    if isinstance(address, tuple) and len(address) == 2:
        mapped = _lookup(address[0], address[1])
        if mapped is not None:
            address = mapped
    return _ORIG["connect_ex"](self, address)


def _sendto(self, data, *args):
    # sendto(data, addr) / sendto(data, flags, addr)
    if args:
        address = args[-1]
        if isinstance(address, tuple) and len(address) == 2:
            mapped = _lookup(address[0], address[1])
            if mapped is not None:
                args = args[:-1] + (mapped,)
    return _ORIG["sendto"](self, data, *args)


def _install_locked() -> None:
    """装 patch（调用方持锁）。只装一次，之后 install 只更新映射。"""
    global _INSTALLED
    if _INSTALLED:
        return
    _ORIG["gai"] = socket.getaddrinfo
    _ORIG["connect"] = socket.socket.connect
    _ORIG["connect_ex"] = socket.socket.connect_ex
    _ORIG["sendto"] = socket.socket.sendto
    socket.getaddrinfo = _gai  # type: ignore[assignment]
    socket.socket.connect = _connect  # type: ignore[assignment]
    socket.socket.connect_ex = _connect_ex  # type: ignore[assignment]
    socket.socket.sendto = _sendto  # type: ignore[assignment]
    _INSTALLED = True


def _rebuild_locked() -> None:
    """按属主顺序合并映射表与 DNS 端口（调用方持锁）。"""
    global _MAP, _DNS_PORT, _DNS_PORTS, _CONFLICTS
    merged: dict[tuple[str, int], tuple[str, int]] = {}
    conflicts: list[dict] = []
    for owner in _ORDER:
        for key, local in (_OWNERS.get(owner) or {}).items():
            old = merged.get(key)
            if old is not None and old != local:
                # 跨实例同名服务：保留先装的，记下来让上层提示「请照抄 _tunnel.json 本地端口」
                conflicts.append({"key": key, "kept": old, "ignored": local, "owner": owner})
                continue
            merged[key] = local
    _MAP = merged                     # 整体换引用：读者要么看到旧表要么看到新表，不会撞见半表
    ports: list[int] = []
    for owner in _ORDER:
        port = int(_DNS_OWNERS.get(owner) or 0)
        if port and port not in ports:
            ports.append(port)
    _DNS_PORTS = ports
    _DNS_PORT = ports[0] if ports else 0
    _CONFLICTS = conflicts
    if merged or ports:
        _install_locked()


def conflicts() -> list[dict]:
    """最近一次重建时的跨属主同名服务冲突（供 netbridge 给模型提示）。"""
    with _LOCK:
        return list(_CONFLICTS)


def dns_ports() -> list[int]:
    with _LOCK:
        return list(_DNS_PORTS)


def _touch_locked(owner: str) -> None:
    if owner not in _ORDER:
        _ORDER.append(owner)


def set_dns_port(port: int | None, owner: str = "") -> None:
    """指定隧道 DNS 的本地 UDP 端口（0/None 表示该属主关闭内网 DNS）。

    `owner` 区分不同实例/进程内隧道：supervisor 并发跑多题时各桥各自登记，
    撤掉一个不会误关别人的 DNS（旧调用方不传 owner，行为与从前一致）。
    """
    owner = str(owner or "")
    with _LOCK:
        if int(port or 0):
            _DNS_OWNERS[owner] = int(port or 0)
            _touch_locked(owner)
        else:
            _DNS_OWNERS.pop(owner, None)
        _rebuild_locked()


def dns_port() -> int:
    return _DNS_PORT


def _owner_dropped_locked(owner: str) -> None:
    if owner in _ORDER:
        _ORDER.remove(owner)


def install(mapping: dict[tuple[str, int], tuple[str, int]] | None,
            owner: str = "") -> dict:
    """安装/更新某个属主的映射表（同一属主原地替换；不同属主取并集）。

    返回**该属主自己的**映射（不含别人的条目；要看全量用 ``current()``）。
    不传 owner 的老调用方（solver / 单题脚本）语义不变：每次 install 覆盖自己那一份。
    """
    owner = str(owner or "")
    normalized: dict[tuple[str, int], tuple[str, int]] = {}
    for (host, port), (local_host, local_port) in (mapping or {}).items():
        normalized[(_norm_host(host), int(port))] = (str(local_host), int(local_port))
    with _LOCK:
        if normalized:
            _OWNERS[owner] = normalized
            _touch_locked(owner)
        else:
            _OWNERS.pop(owner, None)
            _owner_dropped_locked(owner)   # 空映射不留空属主，避免顺序表无限增长
        _rebuild_locked()
        return dict(normalized)


def clear(owner: str | None = None) -> None:
    """撤映射：不传 owner = 全清（老语义）；传 owner = 只撤该属主。"""
    with _LOCK:
        if owner is None:
            _OWNERS.clear()
            _DNS_OWNERS.clear()
            _ORDER.clear()
        else:
            key = str(owner or "")
            _OWNERS.pop(key, None)
            _DNS_OWNERS.pop(key, None)
            _owner_dropped_locked(key)
        _rebuild_locked()


def current() -> dict[tuple[str, int], tuple[str, int]]:
    with _LOCK:
        return dict(_MAP)


def restore() -> None:
    """测试用：恢复原始 socket 实现（映射/DNS 也要清干净，否则测试互相污染）。"""
    global _INSTALLED, _MAP, _DNS_PORT, _DNS_PORTS, _CONFLICTS
    with _LOCK:
        _MAP = {}
        _DNS_PORT = 0
        _OWNERS.clear()
        _DNS_OWNERS.clear()
        _ORDER.clear()
        _DNS_PORTS.clear()
        _CONFLICTS.clear()
        if _INSTALLED:
            socket.getaddrinfo = _ORIG["gai"]  # type: ignore[assignment]
            socket.socket.connect = _ORIG["connect"]  # type: ignore[assignment]
            socket.socket.connect_ex = _ORIG["connect_ex"]  # type: ignore[assignment]
            socket.socket.sendto = _ORIG["sendto"]  # type: ignore[assignment]
            _INSTALLED = False