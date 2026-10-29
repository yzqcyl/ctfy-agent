# -*- coding: utf-8 -*-
"""领域扩展工具：TCP 交互 / ELF 静态解析 / 连通性诊断。

服务对象（此前三个方向是空的）：
- pwn（SAMPLE-003/012 等）：栈溢出需要「连 TCP + 发 payload + 收回显」与 ELF 偏移解析
- crypto（SAMPLE-004 等）：padding oracle 需要对同一 TCP 服务做数百次「发两块密文 → 看响应」
- reverse（SAMPLE-010/011 等）：容器里没有 objdump/file/binwalk，需要纯 stdlib 的 ELF 探测

设计约束：
1. 纯 stdlib（struct/socket/select），不引第三方依赖，符合「镜像只带 python3 stdlib」。
2. 合规：所有网络工具只允许访问 LocalTools 已登记的实例端点（allowed_hosts/allowed_endpoints），
   与 http_request 同一套白名单，避免被提示词注入变成任意主机扫描器。
3. 输出 ≤ 6000 字符（与 MISC 工具一致），二进制一律「hex 前 N 字节 + 可打印预览」。

注册方式（刻意选择零改动接线）：本模块的 SPECS 会被 tools_misc_ext.MISC_EXT_SPECS 聚合，
而 crew.py 已有「按角色 local 名单过滤 MISC_EXT_SPECS」的门禁循环，因此无需改 crew.py。
"""
from __future__ import annotations

import socket
import struct
import time
from pathlib import Path

TOOL_TEXT_MAX = 6000
MAX_READ = 65536


class DomainToolError(Exception):
    """可回显给模型的工具错误（由 call_domain 转成框架 ToolError）。"""


def _tool_error() -> type[BaseException]:
    """惰性取框架的 ToolError，避免与 tools_local 循环导入（同 tools_misc_ext 的做法）。"""
    try:
        from .tools_local import ToolError  # type: ignore

        return ToolError
    except Exception:  # noqa: BLE001
        return RuntimeError


def _fail(msg: str):
    raise _tool_error()(msg)


def _clip(text: str, limit: int = TOOL_TEXT_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（输出已截断，共 %d 字符；用更小的 max_recv/limit 或 offset 分段读）" % len(text)


def _ascii_preview(data: bytes, limit: int = 512) -> str:
    out = []
    for b in data[:limit]:
        out.append(chr(b) if 32 <= b < 127 else ("\\n" if b == 10 else ("\\r" if b == 13 else ".")))
    return "".join(out)


def _hex_preview(data: bytes, limit: int = 256) -> str:
    return data[:limit].hex()


def _fmt_stream(data: bytes, label: str = "recv") -> str:
    return "%s %d 字节\n  hex: %s\n  ascii: %s" % (
        label, len(data), _hex_preview(data), _ascii_preview(data))


# ---------------------------------------------------------------- TCP


def _connect(host: str, port: int, timeout: float, tls: bool = False, sni: str = "") -> socket.socket:
    """建立 TCP（可选 TLS+SNI）。平台 TCP 题有三种接入方式：明文端口 / 443+tls_sni / vpn。"""
    try:
        raw = socket.create_connection((host, int(port)), timeout=timeout)
    except socket.gaierror as exc:
        hint = ""
        if host.lower().endswith(".internal"):
            hint = (
                "\n提示：该主机是 *.internal（平台内网/VPN 实例）。本机若未接入题目 VPN 隧道，"
                "DNS 阶段就会失败——这不是你的攻击脚本问题，先确认隧道/换平台容器内运行。"
            )
        raise DomainToolError("DNS 解析失败：%s（%s）%s" % (host, exc, hint)) from None
    except (ConnectionRefusedError, TimeoutError, OSError) as exc:
        raise DomainToolError("TCP 连接 %s:%s 失败：%s: %s" % (host, port, type(exc).__name__, exc)) from None
    if not tls:
        return raw
    try:
        import ssl

        ctx = ssl.create_default_context()
        ctx.check_hostname = False          # CTF 自签证书常见，只做信道加密不验身份
        ctx.verify_mode = ssl.CERT_NONE
        return ctx.wrap_socket(raw, server_hostname=(sni or host))
    except Exception as exc:  # noqa: BLE001
        try:
            raw.close()
        except OSError:
            pass
        raise DomainToolError("TLS 握手失败（%s: %s）。该端点若不是 TLS，请去掉 tls=true；"
                              "若是 443+tls_sni 接入，确认 sni 填的是服务端要求的主机名。" % (
                                  type(exc).__name__, exc)) from None


def _recv_until(sock: socket.socket, expect: bytes | None, read_timeout: float, max_recv: int) -> bytes:
    """读到 expect 出现 / 空闲超时 / 收满 max_recv 为止。"""
    buf = b""
    sock.settimeout(read_timeout)
    deadline = time.time() + read_timeout
    while len(buf) < max_recv:
        try:
            chunk = sock.recv(min(4096, max_recv - len(buf)))
        except socket.timeout:
            break
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        if expect and expect in buf:
            break
        if time.time() > deadline + read_timeout:
            break
    return buf


def tcp(host: str, port: int, payload: str = "", hex_mode: bool = False,
        expect: str = "", read_timeout: float = 3.0, max_recv: int = 4096,
        total_timeout: float = 20.0, tls: bool = False, sni: str = "") -> str:
    """单次 TCP 往返：连接 → 发送 payload → 收集回显。

    payload 为文本或 hex（hex_mode=true 时按 hex 解码，常用于二进制 exploit）。
    expect 非空时，收到该子串立即返回（服务端有 banner/多段输出时省时间）。
    tls=true 时做 TLS 握手（平台 access=tls_sni 的 443 接入），sni 默认为 host。
    """
    if not (0 < int(port) < 65536):
        raise DomainToolError("端口非法：%r" % (port,))
    if hex_mode:
        try:
            raw = bytes.fromhex(payload.strip())
        except ValueError as exc:
            raise DomainToolError("hex_mode 下 payload 不是合法 hex：%s" % exc) from None
    else:
        raw = payload.encode("utf-8", "surrogateescape")

    t0 = time.time()
    sock = _connect(host, int(port), min(10.0, total_timeout), tls=bool(tls), sni=sni)
    try:
        # 先尝试读 banner（很多 pwn/crypto 服务先给提示再等输入），无 banner 也不失败
        try:
            sock.settimeout(0.4)
            banner = sock.recv(4096)
        except (socket.timeout, OSError):
            banner = b""
        if raw:
            try:
                sock.sendall(raw)
            except OSError as exc:
                raise DomainToolError("发送失败：%s" % exc) from None
        body = _recv_until(sock, expect.encode("utf-8", "surrogateescape") if expect else None,
                           float(read_timeout), int(max_recv))
    finally:
        try:
            sock.close()
        except OSError:
            pass
    ms = (time.time() - t0) * 1000
    parts = ["连接 %s:%s 成功（往返 %.0f ms，发送 %d 字节）" % (host, port, ms, len(raw))]
    if banner:
        parts.append(_fmt_stream(banner, "banner"))
    if body:
        parts.append(_fmt_stream(body, "回显"))
    elif not banner:
        parts.append("未收到任何数据（连接建立但服务端未回包；可能要先发协议头/换端口）")
    if expect and body and expect.encode() in body:
        parts.append("已命中 expect=%r" % expect)
    return _clip("\n".join(parts))


def tcp_batch(host: str, port: int, payloads: list[str], hex_mode: bool = True,
              read_timeout: float = 2.0, max_recv: int = 1024,
              same_conn: bool = True, total_timeout: float = 40.0,
              tls: bool = False, sni: str = "") -> str:
    """批量往返：对同一服务顺序发 N 个 payload（padding-oracle / 爆破常用）。

    返回每个 payload 的响应摘要（长度 + 短 hex），让模型能对比差异而不用逐条调用。
    """
    if not payloads:
        raise DomainToolError("payloads 为空")
    if len(payloads) > 200:
        raise DomainToolError("一次最多 200 个 payload（当前 %d），请分批" % len(payloads))
    t0 = time.time()
    rows: list[str] = []
    sock = _connect(host, int(port), min(10.0, total_timeout), tls=bool(tls), sni=sni)
    try:
        sock.settimeout(0.3)
        try:
            sock.recv(4096)
        except (socket.timeout, OSError):
            pass
        for i, item in enumerate(payloads):
            if time.time() - t0 > total_timeout:
                rows.append("…超时停止，已发 %d/%d" % (i, len(payloads)))
                break
            raw = bytes.fromhex(item.strip()) if hex_mode else item.encode("utf-8", "surrogateescape")
            try:
                sock.sendall(raw)
            except OSError as exc:
                rows.append("#%d 发送失败：%s" % (i, exc))
                break
            body = _recv_until(sock, None, float(read_timeout), int(max_recv))
            rows.append("#%d 发 %d 字节 → 收 %d 字节 hex=%s ascii=%r" % (
                i, len(raw), len(body), body[:48].hex(), _ascii_preview(body, 60)))
            if not same_conn:
                sock.close()
                sock = _connect(host, int(port), min(10.0, total_timeout), tls=bool(tls), sni=sni)
                sock.settimeout(0.3)
                try:
                    sock.recv(4096)
                except (socket.timeout, OSError):
                    pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    head = "批量往返 %s:%s，%d 个 payload（same_conn=%s），总耗时 %.1fs" % (
        host, port, len(payloads), same_conn, time.time() - t0)
    return _clip("\n".join([head] + rows))


# ---------------------------------------------------------------- ELF


class _Elf:
    """纯 stdlib ELF 解析（只做 CTF 常用字段，不追求完整）。"""

    def __init__(self, data: bytes):
        if len(data) < 52 or data[:4] != b"\x7fELF":
            raise DomainToolError("不是 ELF 文件（魔数不符）")
        self.data = data
        self.is64 = data[4] == 2
        self.endian = "<" if data[5] == 1 else ">"
        self.e_type = struct.unpack_from(self.endian + "H", data, 16)[0]
        self.e_machine = struct.unpack_from(self.endian + "H", data, 18)[0]
        if self.is64:
            (self.e_entry, self.e_phoff, self.e_shoff) = struct.unpack_from(self.endian + "QQQ", data, 24)[:3]
            self.e_phentsize, self.e_phnum, self.e_shentsize, self.e_shnum, self.e_shstrndx = struct.unpack_from(
                self.endian + "HHHHH", data, 54)
        else:
            (self.e_entry, self.e_phoff, self.e_shoff) = struct.unpack_from(self.endian + "III", data, 24)[:3]
            self.e_phentsize, self.e_phnum, self.e_shentsize, self.e_shnum, self.e_shstrndx = struct.unpack_from(
                self.endian + "HHHHH", data, 42)

    # -- 段/节
    def sections(self) -> list[tuple[str, int, int, int]]:
        out: list[tuple[str, int, int, int]] = []
        if not self.e_shoff or not self.e_shnum:
            return out
        raw = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            if off + self.e_shentsize > len(self.data):
                break
            if self.is64:
                name, typ, flags, addr, offset, size = struct.unpack_from(self.endian + "IIQQQQ", self.data, off)
            else:
                name, typ, flags, addr, offset, size = struct.unpack_from(self.endian + "IIIIII", self.data, off)
            raw.append((name, typ, flags, addr, offset, size))
        strtab_off = strtab_size = 0
        if self.e_shstrndx < len(raw):
            strtab_off, strtab_size = raw[self.e_shstrndx][4], raw[self.e_shstrndx][5]
        for name, typ, flags, addr, offset, size in raw:
            nm = ""
            if strtab_off and (strtab_off + name) < len(self.data):
                end = self.data.find(b"\0", strtab_off + name)
                nm = self.data[strtab_off + name:end if end > 0 else strtab_off + name + 64].decode("latin-1", "replace")
            out.append((nm, addr, offset, size))
        return out

    def symbols(self) -> list[tuple[str, int, int]]:
        out: list[tuple[str, int, int]] = []
        secs = []
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            if off + self.e_shentsize > len(self.data):
                break
            if self.is64:
                name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from(
                    self.endian + "IIQQQQIIQQ", self.data, off)
            else:
                name, typ, flags, addr, offset, size, link, info, align, entsize = struct.unpack_from(
                    self.endian + "IIIIIIIIII", self.data, off)
            secs.append((typ, offset, size, link, entsize or (24 if self.is64 else 16)))
        for typ, offset, size, link, entsize in secs:
            if typ not in (2, 11):  # SHT_SYMTAB / SHT_DYNSYM
                continue
            if link >= len(secs):
                continue
            str_off = secs[link][1]
            for pos in range(offset, min(offset + size, len(self.data)), entsize):
                if pos + entsize > len(self.data):
                    break
                if self.is64:
                    name, info, other, shndx, value, sz = struct.unpack_from(self.endian + "IBBHQQ", self.data, pos)
                else:
                    name, value, sz, info, other, shndx = struct.unpack_from(self.endian + "IIIBBH", self.data, pos)
                if not name:
                    continue
                end = self.data.find(b"\0", str_off + name)
                nm = self.data[str_off + name:end if end > 0 else str_off + name + 64].decode("latin-1", "replace")
                out.append((nm, value, info >> 4))
        return out

    def strings(self, min_len: int = 4, limit: int = 80) -> list[str]:
        found, cur = [], b""
        for b in self.data:
            if 32 <= b < 127:
                cur += bytes([b])
            else:
                if len(cur) >= min_len:
                    found.append(cur.decode("latin-1"))
                cur = b""
        if len(cur) >= min_len:
            found.append(cur.decode("latin-1"))
        # 优先含关键词的串
        keys = ("flag", "FLAG", "pass", "admin", "secret", "key", "token", "/bin/sh", "debug")
        found.sort(key=lambda s: (not any(k in s for k in keys), -len(s)))
        return found[:limit]

    def security(self) -> dict:
        sec = {"PIE": self.e_type == 3, "类型": {1: "ET_REL", 2: "ET_EXEC", 3: "ET_DYN"}.get(self.e_type, str(self.e_type))}
        canary = False
        relro = False
        nx = None
        for i in range(self.e_phnum):
            off = self.e_phoff + i * self.e_phentsize
            if off + self.e_phentsize > len(self.data):
                break
            if self.is64:
                p_type, p_flags = struct.unpack_from(self.endian + "II", self.data, off)
                p_vaddr, p_filesz = struct.unpack_from(self.endian + "QQ", self.data, off + 16)
            else:
                p_type, p_offset = struct.unpack_from(self.endian + "II", self.data, off)
                p_vaddr, p_filesz, p_flags = struct.unpack_from(self.endian + "III", self.data, off + 8)
            if p_type == 0x6474E551:  # PT_GNU_STACK
                nx = not (p_flags & 1)  # 可执行栈 => NX 关
            if p_type == 0x6474E552:  # PT_GNU_RELRO
                relro = True
        for nm, _v, _t in self.symbols():
            if nm == "__stack_chk_fail":
                canary = True
            if nm == "__stack_chk_guard":
                canary = True
        sec["canary"] = canary
        sec["RELRO"] = relro
        sec["NX"] = nx
        return sec


def elfpeek(path: str, view: str = "info", min_len: int = 4, limit: int = 60) -> str:
    """ELF 静态探测：info / sections / symbols / strings / gadgets。"""
    p = Path(path)
    if not p.exists():
        raise DomainToolError("文件不存在：%s（先用 download_*_attachment 落盘到沙盒）" % path)
    data = p.read_bytes()
    elf = _Elf(data)
    view = (view or "info").strip().lower()
    if view == "info":
        sec = elf.security()
        lines = [
            "ELF %s %s，machine=0x%x，entry=0x%x，节数=%d，字节数=%d" % (
                "64" if elf.is64 else "32", "LE" if elf.endian == "<" else "BE",
                elf.e_machine, elf.e_entry, elf.e_shnum, len(data)),
            "类型：%s（PIE=%s）" % (sec["类型"], sec["PIE"]),
            "缓解措施：NX=%s  canary=%s  RELRO=%s" % (sec["NX"], sec["canary"], sec["RELRO"]),
        ]
        secs = elf.sections()
        names = [s[0] for s in secs if s[0]]
        lines.append("节：%s" % ", ".join(names[:24]))
        syms = [s[0] for s in elf.symbols() if s[0]]
        funcs = [s for s in syms if not s.startswith("_")][:30]
        lines.append("符号（前 30，非 _ 开头）：%s" % ", ".join(funcs) if funcs else "符号表为空（可能被 strip）")
        return _clip("\n".join(lines))
    if view == "sections":
        rows = ["%-20s %10s %10s %10s" % ("name", "addr", "offset", "size")]
        for nm, addr, off, size in elf.sections():
            if nm:
                rows.append("%-20s %10s %10s %10s" % (nm, hex(addr), hex(off), size))
        return _clip("\n".join(rows))
    if view == "symbols":
        rows = ["%-40s %10s %s" % ("name", "value", "type")]
        for nm, value, typ in elf.symbols()[: int(limit)]:
            rows.append("%-40s %10s %s" % (nm[:40], hex(value), {0: "NOTYPE", 1: "OBJECT", 2: "FUNC"}.get(typ, typ)))
        if len(rows) == 1:
            rows.append("（符号表为空：可能被 strip，用 strings/gadgets 视图）")
        return _clip("\n".join(rows))
    if view == "strings":
        found = elf.strings(int(min_len), int(limit))
        return _clip("字符串 %d 条（优先含 flag/pass/key 等）：\n%s" % (len(found), "\n".join(found)))
    if view == "gadgets":
        pats = {
            "pop rdi; ret": b"\x5f\xc3", "pop rsi; ret": b"\x5e\xc3",
            "pop rdx; ret": b"\x5a\xc3", "pop rax; ret": b"\x58\xc3",
            "syscall; ret": b"\x0f\x05\xc3", "/bin/sh": b"/bin/sh\x00",
        }
        rows = []
        for name, pat in pats.items():
            pos = data.find(pat)
            rows.append("%-16s %s" % (name, ("0x%x" % pos) if pos >= 0 else "未找到"))
        return _clip("gadget/常量扫描：\n" + "\n".join(rows))
    raise DomainToolError("view 只支持 info/sections/symbols/strings/gadgets，收到 %r" % view)


# ---------------------------------------------------------------- 连通性诊断


def netcheck(host: str, port: int | None = None, tls: bool = False) -> str:
    """DNS + TCP 连通性诊断：区分「域名解析失败（VPN/内网）」与「端口不通/被拒」。"""
    host = (host or "").strip()
    if not host:
        raise DomainToolError("host 为空")
    lines = ["目标 %s%s" % (host, (":%s" % port) if port else "")]
    t0 = time.time()
    try:
        infos = socket.getaddrinfo(host, int(port) if port else None, proto=socket.IPPROTO_TCP)
        dns_ms = (time.time() - t0) * 1000
        addrs = sorted({str(i[4][0]) for i in infos})
        lines.append("DNS：成功（%.0f ms）→ %s" % (dns_ms, ", ".join(addrs)))
    except socket.gaierror as exc:
        lines.append("DNS：失败（%s: %s）" % (type(exc).__name__, exc))
        if host.lower().endswith(".internal"):
            lines.append(
                "判断：*.internal 是平台内网/VPN 实例。本机没有隧道时 DNS 必然失败——"
                "不是攻击面问题。需要：平台容器内运行（平台已打通隧道），或本机接入题目 VPN。"
            )
        else:
            lines.append("判断：域名无法解析，确认拼写/是否需要 vpn 或 hosts 映射。")
        return _clip("\n".join(lines))
    if port:
        t1 = time.time()
        try:
            sock = _connect(host, int(port), 5.0, tls=bool(tls), sni=host)
            conn_ms = (time.time() - t1) * 1000
            peer = sock.getpeername()
            try:
                sock.close()
            except OSError:
                pass
            if tls:
                lines.append("TLS：握手成功（%.0f ms）" % conn_ms)
            lines.append("TCP：可连接（%.0f ms）peer=%s:%s" % (conn_ms, peer[0], peer[1]))
            lines.append("判断：链路正常，可以直接用 tcp 工具发 payload%s。" % ("（记得 tls=true）" if tls else ""))
        except DomainToolError as exc:
            lines.append("连接失败：%s" % exc)
            lines.append("判断：DNS 正常但端口/TLS 不通——检查实例是否已启动、端口是否正确、是否该用 tls=true。")
        except (ConnectionRefusedError, TimeoutError, OSError) as exc:
            lines.append("TCP：连接失败（%s: %s）" % (type(exc).__name__, exc))
            lines.append("判断：DNS 正常但端口不通——检查实例是否已启动/端口是否正确（看 start_instance 返回的 services）。")
    return _clip("\n".join(lines))


# ---------------------------------------------------------------- 规格与分发


TCP_SPEC = {
    "type": "function",
    "function": {
        "name": "tcp",
        "description": (
            "对平台实例的 TCP 服务做一次往返：连接、发送 payload（文本或 hex）、收集回显。"
            "pwn 打栈溢出、crypto 连 padding oracle 服务、misc 测连通性都用它。"
            "只能连本题 start_instance 返回的 host:port（白名单外一律拒绝）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "实例主机（start_instance 返回的 surface.services[].host）"},
                "port": {"type": "integer", "description": "实例端口"},
                "payload": {"type": "string", "description": "要发送的内容；hex_mode=true 时按 hex 串解析"},
                "hex_mode": {"type": "boolean", "description": "payload 是否 hex 编码（二进制 exploit 用 true）"},
                "expect": {"type": "string", "description": "可选：收到该子串立即返回"},
                "read_timeout": {"type": "number", "description": "读回显的空闲超时秒（默认 3.0）"},
                "max_recv": {"type": "integer", "description": "最多读多少字节（默认 4096）"},
                "tls": {"type": "boolean", "description": "是否先做 TLS 握手（access=tls_sni 的 443 接入用 true）"},
                "sni": {"type": "string", "description": "TLS SNI 主机名，默认等于 host"},
            },
            "required": ["host", "port"],
        },
    },
}

TCP_BATCH_SPEC = {
    "type": "function",
    "function": {
        "name": "tcp_batch",
        "description": (
            "对同一 TCP 服务顺序发多个 payload 并汇总每个响应（padding oracle / 盲爆破用）。"
            "一次最多 200 个；返回每条的响应长度与短 hex，便于对比差异。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer"},
                "payloads": {"type": "array", "items": {"type": "string"}, "description": "payload 列表（hex_mode 默认 true）"},
                "hex_mode": {"type": "boolean", "description": "payload 是否 hex（默认 true）"},
                "read_timeout": {"type": "number", "description": "每条读超时秒（默认 2.0）"},
                "same_conn": {"type": "boolean", "description": "是否复用同一连接（默认 true；服务端每连接单次交互时置 false）"},
                "tls": {"type": "boolean", "description": "是否 TLS（access=tls_sni 时用 true）"},
                "sni": {"type": "string", "description": "TLS SNI 主机名，默认等于 host"},
            },
            "required": ["host", "port", "payloads"],
        },
    },
}

ELFPEEK_SPEC = {
    "type": "function",
    "function": {
        "name": "elfpeek",
        "description": (
            "纯 stdlib 的 ELF 静态探测（容器里没有 objdump/file）。"
            "view=info 看架构/入口/PIE/NX/canary/RELRO 与节名；sections/symbols/strings/gadgets 分别看节表、符号、字符串、常用 ROP gadget 偏移。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "沙盒内文件路径（相对沙盒根）"},
                "view": {"type": "string", "enum": ["info", "sections", "symbols", "strings", "gadgets"]},
                "min_len": {"type": "integer", "description": "strings 视图的最小串长（默认 4）"},
                "limit": {"type": "integer", "description": "最多输出条目数（默认 60）"},
            },
            "required": ["path"],
        },
    },
}

NETCHECK_SPEC = {
    "type": "function",
    "function": {
        "name": "netcheck",
        "description": (
            "连通性诊断：DNS 是否解析、TCP 端口是否可连。"
            "遇到连不上时先用它判断是「*.internal 需要 VPN」还是「端口/实例问题」，不要盲目重试烧预算。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "port": {"type": "integer", "description": "可选；给了就测 TCP 连接"},
                "tls": {"type": "boolean", "description": "可选；给了 port 且该端口是 443+tls_sni 接入时用 true"},
            },
            "required": ["host"],
        },
    },
}

DOMAIN_SPECS = [TCP_SPEC, TCP_BATCH_SPEC, ELFPEEK_SPEC, NETCHECK_SPEC]

DOMAIN_NAMES = {s["function"]["name"] for s in DOMAIN_SPECS}


def call_domain(name: str, arguments: dict | None = None) -> str:
    """按工具名分发。参数已由 LocalTools 侧做过白名单/签名过滤。"""
    args = dict(arguments or {})
    try:
        if name == "tcp":
            return tcp(
                str(args.get("host", "")), int(args.get("port") or 0),
                payload=str(args.get("payload") or ""),
                hex_mode=bool(args.get("hex_mode", False)),
                expect=str(args.get("expect") or ""),
                read_timeout=float(args.get("read_timeout", 3.0)),
                max_recv=int(args.get("max_recv", 4096)),
                tls=bool(args.get("tls", False)),
                sni=str(args.get("sni") or ""),
            )
        if name == "tcp_batch":
            return tcp_batch(
                str(args.get("host", "")), int(args.get("port") or 0),
                [str(x) for x in (args.get("payloads") or [])],
                hex_mode=bool(args.get("hex_mode", True)),
                read_timeout=float(args.get("read_timeout", 2.0)),
                same_conn=bool(args.get("same_conn", True)),
                tls=bool(args.get("tls", False)),
                sni=str(args.get("sni") or ""),
            )
        if name == "elfpeek":
            return elfpeek(str(args.get("path", "")), view=str(args.get("view") or "info"),
                           min_len=int(args.get("min_len", 4)), limit=int(args.get("limit", 60)))
        if name == "netcheck":
            port = args.get("port")
            return netcheck(str(args.get("host", "")), int(port) if port else None,
                            tls=bool(args.get("tls", False)))
    except DomainToolError as exc:
        _fail(str(exc))
    _fail("未知领域工具：%s（可用：%s）" % (name, ", ".join(sorted(DOMAIN_NAMES))))
