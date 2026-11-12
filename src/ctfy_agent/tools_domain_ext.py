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

import os
import re

import marshal  # [rev-toolbox] bytecode 视图：.pyc / PyInstaller 的 marshal 载荷
import math  # [crypto-toolbox] isqrt/gcd（纯 stdlib 大数算法）
import socket
import struct
import time
import types  # [rev-toolbox] bytecode 视图：CodeType 判定
import zlib  # [rev-toolbox] bytecode 视图：PyInstaller/PYZ 的 zlib 载荷
from pathlib import Path

from . import pylibs as _pylibs  # [libs 2026-11-12] 进程内外挂库（z3/pycryptodome/pwntools）接入

TOOL_TEXT_MAX = int(os.environ.get("AGENT_TOOL_TEXT_MAX") or 6000)
# O-C：工具输出的硬上限（字符），env AGENT_TOOL_TEXT_MAX 可覆盖（默认 6000 = 原行为）。
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
    closed_early = False
    send_failed = ""
    try:
        # 先尝试读 banner（很多 pwn/crypto 服务先给提示再等输入），无 banner 也不失败。
        # 读到 max_recv 或 0.4s 空闲为止：大 banner 不再被硬截成 4KB。
        banner = b""
        try:
            sock.settimeout(0.4)
            while len(banner) < int(max_recv):
                chunk = sock.recv(min(4096, int(max_recv) - len(banner)))
                if not chunk:
                    closed_early = True
                    break
                banner += chunk
                if expect and expect.encode("utf-8", "surrogateescape") in banner:
                    break
        except socket.timeout:
            pass
        except OSError:
            # 连上就被 RST：典型的「明文打 TLS 端口」，先记下来，等下给提示
            if not banner:
                closed_early = True
        if raw and not closed_early:
            try:
                sock.sendall(raw)
            except OSError as exc:
                # 发送失败也要把已经读到的 banner 带回去，不能整条丢掉
                send_failed = "发送失败（%s）" % exc
        body = _recv_until(sock, expect.encode("utf-8", "surrogateescape") if expect else None,
                           float(read_timeout), int(max_recv)) if not closed_early else b""
    finally:
        try:
            sock.close()
        except OSError:
            pass
    ms = (time.time() - t0) * 1000
    parts = ["连接 %s:%s 成功（往返 %.0f ms，发送 %d 字节）" % (host, port, ms, len(raw))]
    if banner:
        parts.append(_fmt_stream(banner, "banner"))
    if send_failed:
        parts.append(send_failed)
    if body:
        parts.append(_fmt_stream(body, "回显"))
    if banner[:2] == b"\x15\x03":
        parts.append("检测到 TLS alert（0x15 03）：这个端口是 TLS 端点，请用 tls=true 重试。")
    elif not banner and not body and not send_failed:
        if closed_early:
            parts.append(
                "服务端在交换任何数据前就关闭了连接。最常见原因是**明文连了 TLS 端口**"
                "（平台 access_url 常在详情里标 tls）：请用 tls=true 重试；"
                "也可能是实例后端还没就绪，可先用 netcheck（tls=True）判定链路。")
        else:
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

# >>> [rev-toolbox 2026-11-02] 重定位 / 动态段：纯 stdlib 自研，不引入 pyelftools
#     对标 ctf-forge 的 inspect_elf（它返回 relocations + dynamic 两个字段），思路借鉴、实现独立。

# x86-64 / i386 常见重定位类型（其余机器回落成数字，不猜）
_RELOC_NAMES = {
    62: {0: "NONE", 1: "R_X86_64_64", 2: "PC32", 4: "PLT32", 5: "COPY", 6: "GLOB_DAT",
         7: "JUMP_SLOT", 8: "RELATIVE", 9: "GOTPCREL", 16: "DTPMOD64", 17: "DTPOFF64",
         18: "TPOFF64", 37: "IRELATIVE", 41: "GOTPCRELX", 42: "REX_GOTPCRELX"},
    3: {0: "NONE", 1: "R_386_32", 2: "PC32", 4: "PLT32", 5: "COPY", 6: "GLOB_DAT",
        7: "JUMP_SLOT", 8: "RELATIVE", 14: "TLS_TPOFF", 35: "TLS_DTPMOD32"},
}

# .dynamic 里 dt_tag 的含义（只列 CTF 用得上的）
_DT_NAMES = {
    0: "NULL", 1: "NEEDED", 2: "PLTRELSZ", 3: "PLTGOT", 4: "HASH", 5: "STRTAB", 6: "SYMTAB",
    7: "RELA", 8: "RELASZ", 9: "RELAENT", 10: "STRSZ", 11: "SYMENT", 12: "INIT", 13: "FINI",
    14: "SONAME", 15: "RPATH", 16: "SYMBOLIC", 17: "REL", 18: "RELSZ", 19: "RELENT",
    20: "PLTREL", 21: "DEBUG", 22: "TEXTREL", 23: "JMPREL", 24: "BIND_NOW",
    25: "INIT_ARRAY", 26: "FINI_ARRAY", 27: "INIT_ARRAYSZ", 28: "FINI_ARRAYSZ", 29: "RUNPATH",
    30: "FLAGS", 0x6FFFFEF5: "GNU_HASH", 0x6FFFFFF0: "VERSYM", 0x6FFFFFFB: "FLAGS_1",
    0x6FFFFFFE: "VERNEED", 0x6FFFFFFF: "VERNEEDNUM",
}
# 这两个 tag 的 d_val 是 .dynstr 里的偏移，要解成字符串
_DT_STRING_TAGS = frozenset((1, 14, 15, 29))  # NEEDED / SONAME / RPATH / RUNPATH

_FLAGS_1_BITS = ((0x1, "NOW"), (0x2, "GLOBAL"), (0x4, "GROUP"), (0x8, "NODELETE"),
                 (0x10, "LOADFLTR"), (0x20, "INITFIRST"), (0x40, "NOOPEN"), (0x80, "ORIGIN"),
                 (0x100, "DIRECT"), (0x8000000, "PIE"))
_FLAGS_BITS = ((0x1, "ORIGIN"), (0x2, "SYMBOLIC"), (0x4, "TEXTREL"), (0x8, "BIND_NOW"),
               (0x10, "STATIC_TLS"))


def _cstr(data: bytes, off: int, cap: int = 160) -> str:
    """从 off 读一个 NUL 结尾串（越界/无终止符都兜住，不抛错）。"""
    if off <= 0 or off >= len(data):
        return ""
    end = data.find(b"\0", off, off + cap)
    if end < 0:
        end = min(off + cap, len(data))
    return data[off:end].decode("latin-1", "replace")


def _reloc_name(machine: int, r_type: int) -> str:
    return _RELOC_NAMES.get(machine, {}).get(r_type, "type_%d" % r_type)


def _flag_words(bits, value: int) -> str:
    got = [nm for bit, nm in bits if value & bit]
    return "+".join(got) if got else ""


class _Elf:
    """纯 stdlib ELF 解析（只做 CTF 常用字段，不追求完整）。"""

    def __init__(self, data: bytes):
        if len(data) < 16 or data[:4] != b"\x7fELF":
            raise DomainToolError("不是 ELF 文件（魔数不符）")
        self.data = data
        self.is64 = data[4] == 2
        # 64 位 ELF 头需 64 字节（e_shentsize 等字段在 54..64），32 位需 52 字节；不足则拒，避免 struct.error。
        need = 64 if self.is64 else 52
        if len(data) < need:
            raise DomainToolError("ELF 头不完整（%d 位需 %d 字节，实际 %d）" % (64 if self.is64 else 32, need, len(data)))
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
                # [rev-toolbox 2026-11-02] 修 32 位布局：Elf32_Phdr 是
                # type(0) offset(4) vaddr(8) paddr(12) filesz(16) memsz(20) flags(24) align(28)。
                # 旧写法在 off+8 解 3 个 I 再按 (vaddr, filesz, flags) 赋值，实际拿到
                # (vaddr, paddr, filesz) → flags 读成 paddr、filesz 读成 filesz 的错位，
                # 导致 32 位 ELF 的 NX 恒判 True（PT_GNU_STACK 的 flags 从未读到）。
                (p_type, p_offset, p_vaddr, _pa, p_filesz, _msz, p_flags, _al) = struct.unpack_from(
                    self.endian + "IIIIIIII", self.data, off)
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

    # -- [rev-toolbox 2026-11-02] 节头 / 符号表 / 重定位 / 动态段
    def shdrs(self) -> list[tuple]:
        """[(idx, name_off, type, flags, addr, offset, size, link, info, entsize)]"""
        out: list[tuple] = []
        if not self.e_shoff or not self.e_shnum:
            return out
        for i in range(self.e_shnum):
            off = self.e_shoff + i * self.e_shentsize
            if off + self.e_shentsize > len(self.data):
                break
            if self.is64:
                fields = struct.unpack_from(self.endian + "IIQQQQIIQQ", self.data, off)
            else:
                fields = struct.unpack_from(self.endian + "IIIIIIIIII", self.data, off)
            out.append((i,) + tuple(fields))
        return out

    def section_names(self) -> list[str]:
        sh = self.shdrs()
        names = [""] * len(sh)
        if not sh or self.e_shstrndx >= len(sh):
            return names
        str_off = sh[self.e_shstrndx][5]
        for row in sh:
            names[row[0]] = _cstr(self.data, str_off + row[1])
        return names

    def _symtab(self, idx) -> list[str]:
        """按符号表**下标**返回名字（空名保留为空串）——重定位按下标引用，不能像
        symbols() 那样跳过空名，否则下标会整体错位。"""
        sh = self.shdrs()
        if not sh or idx is None or idx < 0 or idx >= len(sh):
            return []
        _i, _n, typ, _f, _a, offset, size, link, _info, _align, entsize = sh[idx]
        if typ not in (2, 11):  # SHT_SYMTAB / SHT_DYNSYM
            return []
        ent = entsize or (24 if self.is64 else 16)
        str_off = sh[link][5] if link < len(sh) else 0
        out: list[str] = []
        for pos in range(offset, min(offset + size, len(self.data)), ent):
            if pos + ent > len(self.data):
                break
            name = struct.unpack_from(self.endian + "I", self.data, pos)[0]
            out.append(_cstr(self.data, str_off + name) if name else "")
        return out

    def relocs(self) -> list[tuple[str, str, str, int, int | None]]:
        """[(节名, 类型名, 符号名, 应用位置, addend)]；覆盖 SHT_RELA(4) / SHT_REL(9)。"""
        sh = self.shdrs()
        if not sh:
            return []
        names = self.section_names()
        out: list[tuple[str, str, str, int, int | None]] = []
        for row in sh:
            _i, _n, typ, _f, _a, offset, size, link, _info, _align, entsize = row
            if typ not in (4, 9):
                continue
            rela = typ == 4
            default_ent = ((24 if self.is64 else 12) if rela else (16 if self.is64 else 8))
            ent = entsize or default_ent
            if ent < default_ent:  # 坏节头保护：entsize 过小会死循环
                ent = default_ent
            syms = self._symtab(link)
            sec_name = names[row[0]] if row[0] < len(names) else ""
            for pos in range(offset, min(offset + size, len(self.data)), ent):
                if pos + default_ent > len(self.data):
                    break
                if self.is64:
                    r_off, r_info = struct.unpack_from(self.endian + "QQ", self.data, pos)
                    addend = struct.unpack_from(self.endian + "q", self.data, pos + 16)[0] if rela else None
                    sym_idx, r_type = r_info >> 32, r_info & 0xFFFFFFFF
                else:
                    r_off, r_info = struct.unpack_from(self.endian + "II", self.data, pos)
                    addend = struct.unpack_from(self.endian + "i", self.data, pos + 8)[0] if rela else None
                    sym_idx, r_type = r_info >> 8, r_info & 0xFF
                sym = syms[sym_idx] if 0 < sym_idx < len(syms) else ""
                out.append((sec_name, _reloc_name(self.e_machine, r_type), sym, r_off, addend))
        return out

    def dynamic(self) -> list[tuple[int, int, str]]:
        """[(tag, val, 解码后的字符串)]——只读 SHT_DYNAMIC(6)；无节头时返回空（静态链接常见）。"""
        sh = self.shdrs()
        if not sh:
            return []
        out: list[tuple[int, int, str]] = []
        for row in sh:
            _i, _n, typ, _f, _a, offset, size, link, _info, _align, _entsize = row
            if typ != 6:
                continue
            str_off = sh[link][5] if link < len(sh) else 0
            ent = 16 if self.is64 else 8
            for pos in range(offset, min(offset + size, len(self.data)), ent):
                if pos + ent > len(self.data):
                    break
                if self.is64:
                    tag, val = struct.unpack_from(self.endian + "qQ", self.data, pos)
                else:
                    tag, val = struct.unpack_from(self.endian + "iI", self.data, pos)
                txt = _cstr(self.data, str_off + val) if tag in _DT_STRING_TAGS else ""
                out.append((tag, val, txt))
        return out


# >>> [rev-toolbox 2026-10-30] elfpeek 升级：新增 disasm / bytecode 视图；gadgets v2；strings 支持 utf16。
# 详细说明与验收见 docs/rev-toolbox-plan.md。capstone 为可选引擎：**v5 起才装进镜像**（v4 及以前没有——R2 项漏做），缺失时走 stdlib 退化路径。
def elfpeek(path: str, view: str = "info", min_len: int = 4, limit: int = 60,
            offset: int = 0, length: int = 128, count: int = 40, arch: str = "",
            extract: str = "", func: str = "", utf16: bool = False, filt: str = "",
            symbol: str = "", search: str = "") -> str:
    """ELF / 字节码静态探测：info / sections / symbols / strings / gadgets / disasm / bytecode / relocs / dynamic。"""
    p = Path(path)
    if not p.exists():
        raise DomainToolError("文件不存在：%s（先用 download_*_attachment 落盘到沙盒）" % path)
    data = p.read_bytes()
    view = (view or "info").strip().lower()
    if view == "bytecode":  # [rev-toolbox] .pyc / PyInstaller 不是 ELF，先分流再构造 _Elf
        return _view_bytecode(p, data, extract=extract, func=func)
    if view == "strings" and utf16:  # [rev-toolbox] 宽字符扫描不要求 ELF（任意文件可扫）
        wide = _strings_utf16(data, int(min_len), int(limit))
        return _clip("UTF-16 宽字符串 %d 条：\n%s\n（ASCII 串用 utf16=false；找不到时可能不是宽字符编码）" % (
            len(wide), "\n".join(wide) if wide else "（无）"))
    elf = _Elf(data)
    if view == "info":
        sec = elf.security()
        lines = [
            "ELF %s %s，machine=0x%x，entry=0x%x，节数=%d，字节数=%d" % (
                "64" if elf.is64 else "32", "LE" if elf.endian == "<" else "BE",
                elf.e_machine, elf.e_entry, elf.e_shnum, len(data)),
            "类型：%s（PIE=%s）" % (sec["类型"], sec["PIE"]),
            "缓解措施：NX=%s  canary=%s  RELRO=%s" % (sec["NX"], sec["canary"], sec["RELRO"]),
            _entry_line(elf),  # [rev-toolbox] 入口所在段（UPX/壳的快速线索）
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
        # [rev-toolbox] v2：capstone 真实指令边界枚举；无 capstone/非 x86 时退回原 5 模式首现扫描
        return _view_gadgets(data, elf, filt=filt, limit=int(limit))
    if view == "disasm":  # [rev-toolbox] 反汇编视图（capstone 引擎，stdlib 字节视图退化）
        return _view_disasm(data, elf, offset=int(offset or 0), length=int(length or 128),
                            count=int(count or 40), arch=arch)
    if view == "lookup":  # [pwn-toolbox] 符号/字符串精确查询（ret2libc 辅助）
        return _view_lookup(data, elf, symbol=symbol, search=search)
    if view == "relocs":  # [rev-toolbox 2026-11-02] 重定位表（PLT/GOT 映射、静态链接判定）
        return _view_relocs(elf, limit=int(limit))
    if view == "dynamic":  # [rev-toolbox 2026-11-02] 动态段（依赖库/RUNPATH/BIND_NOW/PIE）
        return _view_dynamic(elf, limit=int(limit))
    raise DomainToolError(
        "view 只支持 info/sections/symbols/strings/gadgets/disasm/bytecode/lookup/relocs/dynamic，收到 %r" % view)


# >>> [rev-toolbox 2026-10-30] ===== rev 方向工具箱 v2 实现 =====
# 本段（含文中 [rev-toolbox] 行内标记）均为 toolbox 会话新增；并行会话合并时按标记识别。
# 依赖策略：capstone 为可选引擎（**v5 起才装进镜像**；v4 及以前实际没装，靠 stdlib 退化路径工作）；缺失时不抛错。


def _phdrs(elf):
    """[(p_type, p_flags, p_offset, p_vaddr, p_filesz)]——只取 CTF 常用字段。"""
    out = []
    for i in range(elf.e_phnum):
        off = elf.e_phoff + i * elf.e_phentsize
        if off + elf.e_phentsize > len(elf.data):
            break
        if elf.is64:
            p_type, p_flags = struct.unpack_from(elf.endian + "II", elf.data, off)
            p_offset, p_vaddr, _pa, p_filesz = struct.unpack_from(elf.endian + "QQQQ", elf.data, off + 8)
        else:
            (p_type, p_offset, p_vaddr, _pa, p_filesz, _msz, p_flags, _al) = struct.unpack_from(
                elf.endian + "IIIIIIII", elf.data, off)
        out.append((p_type, p_flags, p_offset, p_vaddr, p_filesz))
    return out


def _off_to_vaddr(elf, off):
    for p_type, _fl, p_offset, p_vaddr, p_filesz in _phdrs(elf):
        if p_type == 1 and p_offset <= off < p_offset + max(p_filesz, 1):
            return p_vaddr + (off - p_offset)
    return None


def _entry_line(elf):
    """info 视图补充：入口落在哪个 PT_LOAD（UPX/加壳的快速线索）。"""
    for p_type, p_flags, p_offset, p_vaddr, p_filesz in _phdrs(elf):
        if p_type == 1 and p_vaddr <= elf.e_entry < p_vaddr + max(p_filesz, 1):
            return "入口段：PT_LOAD vaddr 0x%x..0x%x（文件 0x%x..0x%x，%s）" % (
                p_vaddr, p_vaddr + p_filesz, p_offset, p_offset + p_filesz,
                "可执行" if p_flags & 1 else "不可执行——入口在非执行段，注意 UPX/壳")
    return "入口 0x%x 未落在任何 PT_LOAD（可能加壳/手工构造）" % elf.e_entry


def _capstone_for(elf, arch=""):
    """[rev-toolbox] capstone 可选引擎：不可用返回 None，由调用方走退化路径。

    用 importlib 动态加载而非静态 import：case_stdlib_only 静态扫描禁止第三方 import。
    capstone 自 v5 起装进镜像（v4 及以前实际没装，见 docs/rev-toolbox-plan.md 的 R2 项）；
    缺失时永不 ImportError，一律走退化路径。
    """
    try:
        import importlib

        capstone = importlib.import_module("capstone")
    except Exception:
        return None
    a = (arch or "").strip().lower()
    if a in ("x86", "i386", "32"):
        return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    if a in ("x64", "x86-64", "amd64"):
        return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    if a in ("arm64", "aarch64"):
        return capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    if a == "arm":
        return capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    if elf.is64 and elf.e_machine == 62:
        return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    if elf.e_machine == 3:
        return capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    if elf.e_machine == 183:
        return capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_LITTLE_ENDIAN)
    if elf.e_machine == 40:
        return capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_ARM)
    return None


def _nearest_symbol(syms, addr):
    best_nm, best_va = "", -1
    for nm, value, _t in syms:
        if value and value <= addr and value > best_va:
            best_nm, best_va = nm, value
    if best_nm and 0 <= addr - best_va < 0x10000:
        return "<%s+0x%x>" % (best_nm, addr - best_va)
    return ""


def _view_disasm(data, elf, offset=0, length=128, count=40, arch=""):
    """[rev-toolbox] 反汇编视图：capstone 引擎；无引擎退化为字节视图（不抛错）。"""
    length = max(16, min(int(length or 128), 4096))
    count = max(10, min(int(count or 40), 200))
    target = max(0, int(offset or 0))
    if target >= len(data):  # [rev-toolbox] 超界先报错，再做段吸附
        raise DomainToolError("offset=0x%x 超出文件长度 0x%x（先 sections 视图看范围）" % (target, len(data)))
    note = ""
    exe = [(off, sz) for t, fl, off, _va, sz in _phdrs(elf) if t == 1 and (fl & 1)]
    loads = [(off, sz) for t, _fl, off, _va, sz in _phdrs(elf) if t == 1]
    if not any(o <= target < o + max(s, 1) for o, s in exe):
        pool = exe or loads
        if pool:
            near = min(pool, key=lambda o_s: abs(o_s[0] - target))[0]
            note = "（offset=0x%x 不在可执行段，已吸附到 0x%x；段范围见 info/sections）" % (target, near)
            target = near
    va = _off_to_vaddr(elf, target)
    md = _capstone_for(elf, arch)
    if md is None:
        chunk = data[target:target + length]
        lines = [
            "本环境无 capstone（v5 起镜像内置；v4 及以前没装）——退化为字节视图；优先走 symbols/strings 推理，"
            "或用 python3 按已知指令编码人工核对：",
            "文件偏移 0x%x 起 %d 字节（vaddr %s）：%s" % (
                target, len(chunk), hex(va) if va is not None else "?", _hex_preview(chunk, 256)),
        ]
        if note:
            lines.insert(0, note)
        return _clip("\n".join(lines))
    lines = [note] if note else []
    lines.append("反汇编 文件偏移 0x%x，%d 字节，vaddr %s%s" % (
        target, length, hex(va) if va is not None else "?",
        "（PIE：运行时地址=基址+此地址）" if elf.security()["PIE"] else ""))
    syms = elf.symbols()
    n = 0
    for insn in md.disasm(data[target:target + length], va if va is not None else target):
        tag = _nearest_symbol(syms, insn.address)
        lines.append("%08x  %-20s %-8s %s%s" % (
            insn.address, insn.bytes.hex()[:20], insn.mnemonic, insn.op_str,
            ("  ; %s" % tag) if tag else ""))
        n += 1
        if n >= count:
            break
    if n == 0:
        lines.append("（0 条可解码指令：偏移可能落在数据区或指令中间，换 offset 或先看 sections/symbols）")
    return _clip("\n".join(lines))


def _view_relocs(elf, limit=60):
    """[rev-toolbox 2026-11-02] 重定位视图：PLT/GOT 映射、静态链接判定、构造函数定位。"""
    rows = elf.relocs()
    if not rows:
        return _clip(
            "重定位表为空：要么静态链接（没有 .rela.* / .rel.* 节），要么节头被删/被裁。\n"
            "静态链接的 rev 题不需要 GOT 推断，直接 symbols/strings → disasm 定位校验逻辑。")
    by_sec: dict[str, list] = {}
    for sec, typ, sym, off, addend in rows:
        by_sec.setdefault(sec or "(无名节)", []).append((typ, sym, off, addend))
    order = sorted(by_sec, key=lambda s: (s != ".rela.plt", s))  # PLT 优先：rev/pwn 最常看这张
    lines = ["重定位 %d 条，分 %d 节（JUMP_SLOT 的 @地址就是 foo@got；同一节内顺序 = PLT stub 顺序）："
             % (len(rows), len(by_sec))]
    shown = 0
    for sec in order:
        items = by_sec[sec]
        lines.append("[%s] %d 条" % (sec, len(items)))
        for typ, sym, off, addend in items:
            if shown >= limit:
                lines.append("  …（已到 limit=%d，加 limit 或换节看）" % limit)
                break
            row = "  %-28s %-11s @0x%x" % (sym or "(无符号)", typ, off)
            if addend:
                row += "  addend=0x%x" % (addend & 0xFFFFFFFFFFFFFFFF)
            lines.append(row)
            shown += 1
    lines.append(
        "RELATIVE 的 addend = 修正前的文件内 vaddr（重定位后 got=载入基址+addend）；"
        "GLOB_DAT 多为数据导入（stdin/stdout/环境指针）；无符号 RELATIVE 常在 .init_array，可反推构造函数。")
    return _clip("\n".join(lines))


def _view_dynamic(elf, limit=40):
    """[rev-toolbox 2026-11-02] 动态段视图：依赖库 / RUNPATH / BIND_NOW / PIE 位。"""
    rows = elf.dynamic()
    if not rows:
        return _clip(
            "没有 SHT_DYNAMIC（无节头，或节头被删）：多数是静态链接。\n"
            "先用 info 看类型与入口段；静态题直接 symbols/strings → disasm。")
    needed = [t for tg, _v, t in rows if tg == 1 and t]
    lines = ["动态段 %d 条，NEEDED=%d（%s）" % (
        len(rows), len(needed), "、".join(needed) if needed else "无 —— 极可能静态链接或自实现加载")]
    for tag, val, txt in rows:
        if tag == 0:
            continue
        if len(lines) > limit:
            lines.append("…（已到 limit=%d）" % limit)
            break
        nm = _DT_NAMES.get(tag, "tag_0x%x" % (tag & 0xFFFFFFFF))
        if txt:
            lines.append("%-11s %s" % (nm, txt))
        elif tag == 24:
            lines.append("%-11s -    （立即绑定 → 通常 full RELRO，GOT 只读，无法改 GOT 劫持）" % nm)
        elif tag in (30, 0x6FFFFFFB):
            word = _flag_words(_FLAGS_BITS if tag == 30 else _FLAGS_1_BITS, val)
            lines.append("%-11s 0x%x%s" % (nm, val, ("  (" + word + ")") if word else ""))
        else:
            lines.append("%-11s 0x%x" % (nm, val))
    if any(tg == 15 for tg, _v, _t in rows):
        lines.append("注意：出现 RPATH（旧字段）——库搜索路径被硬编码，rev 时可据此找随题分发的 .so。")
    return _clip("\n".join(lines))


_GADGET_PATS = {  # [rev-toolbox] 原 gadgets 盲扫模式，保留为退化路径（行为原样迁移）
    "pop rdi; ret": b"\x5f\xc3", "pop rsi; ret": b"\x5e\xc3",
    "pop rdx; ret": b"\x5a\xc3", "pop rax; ret": b"\x58\xc3",
    "syscall; ret": b"\x0f\x05\xc3", "/bin/sh": b"/bin/sh\x00",
}

_GADGET_TOKEN_MAP = {
    "rdi": "pop rdi", "rsi": "pop rsi", "rdx": "pop rdx", "rax": "pop rax",
    "syscall": "syscall", "int80": "int 0x80", "leave": "leave",
}


def _gadgets_pattern_fallback(data, elf):
    rows = []
    for name, pat in _GADGET_PATS.items():
        pos = data.find(pat)
        va = _off_to_vaddr(elf, pos) if pos >= 0 else None
        rows.append("%-16s %s%s" % (name, ("0x%x" % pos) if pos >= 0 else "未找到",
                                    ("（vaddr 0x%x）" % va) if va is not None else ""))
    return rows


def _view_gadgets(data, elf, filt="", limit=60):
    """[rev-toolbox] gadgets v2：capstone 真实指令边界枚举（深度≤5）；退化时保持旧盲扫行为。"""
    tokens = [t.strip().lower() for t in (filt or "rdi|rsi|rdx|syscall|binsh").split("|") if t.strip()]
    md = _capstone_for(elf)
    if md is None or elf.e_machine not in (3, 62):
        rows = _gadgets_pattern_fallback(data, elf)
        return _clip("gadget/常量扫描（无 capstone 或非 x86，退化为固定模式首现扫描）：\n" + "\n".join(rows))
    want = {t: _GADGET_TOKEN_MAP.get(t, t) for t in tokens if t != "binsh"}
    hits = {}
    found_per = {t: 0 for t in want}
    deadline = time.monotonic() + 4.0
    segs = [(off, _off_to_vaddr(elf, off) or off, sz)
            for t, fl, off, va, sz in _phdrs(elf) if t == 1 and (fl & 1)]
    done = False
    for seg_off, seg_va, seg_sz in segs:
        blob = data[seg_off:seg_off + seg_sz]
        rets = [i for i in range(len(blob)) if blob[i] == 0xC3]
        if len(rets) > 2000:
            rets = rets[:2000]
        for r in rets:
            if done or time.monotonic() > deadline:
                break
            for start in range(max(0, r - 20), r):
                try:
                    insns = list(md.disasm(blob[start:r + 1], seg_va + start))
                except Exception:
                    continue
                if not insns or len(insns) > 5:
                    continue
                if sum(i.size for i in insns) != (r + 1 - start) or insns[-1].mnemonic != "ret":
                    continue
                chain = " ; ".join((i.mnemonic + (" " + i.op_str if i.op_str else "")) for i in insns)
                for t, needle in want.items():
                    if needle in chain and found_per[t] < 5:
                        found_per[t] += 1
                        if chain not in hits:
                            hits[chain] = (seg_off + start, seg_va + start)
                if want and all(v >= 5 for v in found_per.values()):
                    done = True
                    break
        if done or time.monotonic() > deadline:
            break
    rows = ["gadget 枚举（capstone 真实指令边界，深度≤5，只扫可执行段；filter 可换关键字）%s" % (
        "（超时截断，结果不全）" if time.monotonic() > deadline else "")]
    for t, needle in want.items():
        got = [(c, a) for c, a in hits.items() if needle in c][:5]
        if got:
            for chain, (fo, va) in got:
                rows.append("%-40s 0x%-8x vaddr 0x%x" % (chain[:40], fo, va))
        else:
            rows.append("%-40s 未找到" % needle)
    if "binsh" in tokens:
        pos, bins = 0, []
        while len(bins) < 5:
            pos = data.find(b"/bin/sh\x00", pos)
            if pos < 0:
                break
            va = _off_to_vaddr(elf, pos)
            bins.append("0x%x%s" % (pos, ("（vaddr 0x%x）" % va) if va is not None else ""))
            pos += 1
        rows.append("%-40s %s" % ("/bin/sh", ", ".join(bins) if bins else "未找到"))
    if elf.security()["PIE"]:
        rows.append("（PIE：vaddr 为链接地址，利用时加运行基址）")
    return _clip("\n".join(rows))


def _strings_utf16(data, min_len=4, limit=60):
    """[rev-toolbox] UTF-16 LE/BE 宽字符串扫描。"""
    found = []

    def scan(shift):
        out, cur = [], bytearray()
        i = shift
        while i + 1 < len(data):
            lo, hi = data[i], data[i + 1]
            if hi == 0 and 32 <= lo < 127:
                cur.append(lo)
            else:
                if len(cur) >= min_len:
                    out.append(cur.decode("latin-1"))
                cur = bytearray()
            i += 2
        if len(cur) >= min_len:
            out.append(cur.decode("latin-1"))
        return out

    found.extend(scan(0))  # LE：低字节在前、高字节 0
    found.extend(scan(1))  # BE
    found.sort(key=len, reverse=True)
    return found[:limit]


# ---------- [rev-toolbox] bytecode：.pyc / PyInstaller ----------

_PYI_COOKIE = b"MEI\x0c\x0b\x0a\x0b\x0e"


def _walk_codes(code):
    out = [code]
    for k in code.co_consts:
        if isinstance(k, types.CodeType):
            out.extend(_walk_codes(k))
    return out


def _render_code(code, func=""):
    """把 code 对象渲染成：常量池（关键词优先）+ co_names + dis 片段。"""
    import dis as _dis
    target = code
    if func:
        found = [c for c in _walk_codes(code) if c.co_name == func]
        if not found:
            names = ", ".join(sorted({c.co_name for c in _walk_codes(code)}))[:400]
            raise DomainToolError("没找到函数 %r；可用对象：%s" % (func, names))
        target = found[0]
    strs = []
    for k in target.co_consts:
        if isinstance(k, str) and len(k) >= 3:
            strs.append(k[:80])
        elif isinstance(k, int) and abs(k) > 32:
            strs.append("int:%d" % k)
        elif isinstance(k, bytes) and len(k) >= 3:
            strs.append("bytes:%r" % k[:60])
    keys = ("flag", "pass", "key", "secret", "token", "check", "correct", "wrong", "success", "admin")
    strs.sort(key=lambda s: not any(k in s.lower() for k in keys))
    names = ", ".join(target.co_names[:40])
    lines = [
        "对象：%s  变量：%s" % (target.co_name, list(target.co_varnames[:8])),
        "co_names：%s" % (names if names else "(空)"),
    ]
    if strs:
        lines.append("常量池（前 30，关键词优先）：%s" % " | ".join(strs[:30]))
    try:
        txt = _dis.Bytecode(target).dis()
    except Exception as exc:  # pragma: no cover - 罕见版本不兼容
        lines.append("dis 失败（%s）；常量池与 co_names 仍可用" % exc)
        return lines
    lines.append("dis（前 60 行）：")
    lines.append("\n".join(txt.splitlines()[:60]))
    return lines


def _pyi_parse(data):
    pos = data.rfind(_PYI_COOKIE)
    if pos < 0:
        return None, "文件里没有 PyInstaller cookie（MEI\\x0c0b0a0b0e）：不是 PyInstaller 打包，或 bootloader 版本过老"
    cookie = data[pos:pos + 88]
    if len(cookie) < 24:
        return None, "PyInstaller cookie 截断（文件过小）"
    _magic, pkg_len, toc_off, toc_len, pyvers = struct.unpack_from("<8sIIII", cookie, 0)
    cookie_size = 88 if len(cookie) >= 88 else 24
    pkg_start = pos + cookie_size - pkg_len
    if pkg_start < 0 or pkg_start + toc_off + toc_len > len(data):
        return None, "CArchive 目录越界（pkg_len=%d toc=%d+%d）——版本可能不认识" % (pkg_len, toc_off, toc_len)
    toc = data[pkg_start + toc_off:pkg_start + toc_off + toc_len]
    entries = []
    i = 0
    while i + 18 <= len(toc):
        entrylen, entrypos, csize, usize, cmprs, typ = struct.unpack_from("<IIIIBc", toc, i)
        if entrylen < 18:
            break
        name = toc[i + 18:i + entrylen].split(b"\x00")[0].decode("utf-8", "replace")
        entries.append((name, entrypos, csize, usize, cmprs, typ))
        i += entrylen
    return (pkg_start, pyvers, entries), None


def _pyz_parse(blob):
    if blob[:4] != b"PYZ\x00":
        return None, "不是 PYZ 归档（魔数 %s）" % blob[:4].hex()
    if len(blob) < 16:
        return None, "PYZ 太小"
    toc_pos = struct.unpack_from("!I", blob, len(blob) - 4)[0]
    try:
        toc = marshal.loads(blob[toc_pos:])
    except Exception as exc:
        return None, "PYZ TOC marshal 失败（%s）" % exc
    items = toc.items() if isinstance(toc, dict) else (toc if isinstance(toc, list) else [])
    entries = []
    for name, val in items:
        try:
            mtyp, off = val
            entries.append((str(name), int(off), mtyp))
        except Exception:
            continue
    return entries, None


def _view_pyinstaller(p, data, extract="", func=""):
    parsed, err = _pyi_parse(data)
    if parsed is None:
        raise DomainToolError(err)
    pkg_start, pyvers, entries = parsed
    tmap = {b"s": "py源码", b"m": "py模块", b"M": "包", b"z": "PYZ", b"b": "二进制", b"x": "数据"}
    if not extract:
        rows = ["PyInstaller CArchive（pyvers=%d，%d 条目）。提取：extract=<名字>；PYZ 内模块：extract=pyz:<模块名>" % (
            pyvers, len(entries))]
        for name, pos, csize, usize, cmprs, typ in entries[:80]:
            rows.append("  %-44s %-6s %8d→%8d %s" % (
                name[:44], tmap.get(typ, typ.decode("latin-1", "replace")), csize, usize,
                "zlib" if cmprs else ""))
        if len(entries) > 80:
            rows.append("…（共 %d 条，先看前 80；binary/dll 一般无用，找 m/M/z/s 条目）" % len(entries))
        return _clip("\n".join(rows))
    if extract.startswith("pyz:"):
        zent = [e for e in entries if e[5] == b"z"]
        if not zent:
            raise DomainToolError("归档里没有 PYZ（z）条目")
        name, pos, csize, usize, cmprs, _typ = zent[0]
        blob = data[pkg_start + pos:pkg_start + pos + csize]
        if cmprs:
            blob = zlib.decompress(blob)
        pyz_entries, perr = _pyz_parse(blob)
        if pyz_entries is None:
            raise DomainToolError(perr)
        want = extract[4:]
        hit = [e for e in pyz_entries if e[0] == want or e[0].endswith("." + want)]
        if not hit:
            raise DomainToolError("PYZ 里没有 %r；前 40 个：%s" % (want, ", ".join(e[0] for e in pyz_entries[:40])))
        mod, off, _mtyp = hit[0]
        ln = struct.unpack_from("!I", blob, off)[0]
        raw = blob[off + 4:off + 4 + ln]
        code = marshal.loads(zlib.decompress(raw))
        lines = ["PYZ 模块 %s：" % mod]
        lines.extend(_render_code(code, func=func))
        return _clip("\n".join(lines))
    hit = [e for e in entries if e[0] == extract or e[0].endswith("/" + extract)]
    if not hit:
        raise DomainToolError("没有条目 %r（不带 extract 先列目录）" % extract)
    name, pos, csize, usize, cmprs, typ = hit[0]
    blob = data[pkg_start + pos:pkg_start + pos + csize]
    if cmprs:
        try:
            blob = zlib.decompress(blob)
        except Exception as exc:
            raise DomainToolError("条目 %s 解压失败（%s）" % (name, exc))
    if typ == b"s":
        return _clip("条目 %s（源码，%d 字节）：\n%s" % (name, len(blob), blob.decode("utf-8", "replace")[:4000]))
    if typ in (b"m", b"M"):
        try:
            code = marshal.loads(blob)
        except Exception as exc:
            raise DomainToolError("条目 %s marshal 失败（%s）；前 64 字节：%s" % (name, exc, _hex_preview(blob[:64], 64)))
        lines = ["条目 %s（%s，解压 %d 字节）：" % (name, tmap.get(typ, "?"), len(blob))]
        lines.extend(_render_code(code, func=func))
        return _clip("\n".join(lines))
    outdir = p.parent / (p.name + ".extract")
    outdir.mkdir(parents=True, exist_ok=True)
    safe = name.replace("/", "_").replace("\\", "_")[-80:] or "blob.bin"
    out = outdir / safe
    out.write_bytes(blob)
    return _clip("条目 %s 已解压落盘：%s（%d 字节）\n前 64 字节：%s\n（若为 ELF：elfpeek path=%s view=info 接着看）" % (
        name, out, len(blob), _hex_preview(blob[:64], 64), out))


def _view_pyc(p, data, func=""):
    magic_ok = False
    try:
        import importlib.util as _ilu
        magic_ok = data[:4] == _ilu.MAGIC_NUMBER
    except Exception:
        pass
    body = None
    hlen = 0
    for hdr in (16, 12, 8):
        try:
            obj = marshal.loads(data[hdr:])
        except Exception:
            continue
        if isinstance(obj, types.CodeType):
            body, hlen = obj, hdr
            break
    if body is None:
        raise DomainToolError("不是可解析的 .pyc（marshal 全部失败）。前 16 字节：%s（判型见 hexpeek）" % _hex_preview(data[:16], 16))
    lines = [".pyc（头部 %d 字节；与本解释器 magic %s）" % (hlen, "一致" if magic_ok else "不一致→dis 仅作参考，常量池/co_names 仍可信")]
    lines.extend(_render_code(body, func=func))
    return _clip("\n".join(lines))


def _view_bytecode(p, data, extract="", func=""):
    """[rev-toolbox] bytecode 视图：.pyc / PyInstaller（含 ELF bootloader 的 PyInstaller）。"""
    if data[:4] == b"\x7fELF":
        if data.rfind(_PYI_COOKIE) >= 0:
            return _view_pyinstaller(p, data, extract=extract, func=func)
        raise DomainToolError("这是普通 ELF，bytecode 只处理 .pyc / PyInstaller；ELF 请用 info/disasm/symbols")
    if data[:2] == b"MZ":
        return _view_pyinstaller(p, data, extract=extract, func=func)
    return _view_pyc(p, data, func=func)


# <<< [rev-toolbox 2026-10-30] ===== rev 工具箱 v2 实现结束 =====


# >>> [pwn-toolbox 2026-10-30] ===== pwn 方向工具箱 v1：pwngen / pwnfind / elfpeek lookup =====
# 动机：pwn 方向历史 14 试 0 解。三大缺口——偏移探测靠模型手搓 python（还会撞 shell 引号守卫，
# SAMPLE-013 的教训）、ROP/ret2libc 缺符号与字符串偏移查询、payload 组装无专用通道。
# 本段全部纯 stdlib；工具经 DOMAIN_SPECS 自动注册（tools_misc_ext 聚合 + tools_local.call
# 按 _DOMAIN_NAMES 动态路由），不改 crew.py / tools_local.py。


_CYC_CACHE: dict[tuple[int, int], str] = {}


def _cyclic_seq(n: int = 4, alphabet: str = "") -> str:
    """De Bruijn 序列 B(k, n)：任意长度 n 的连续子串唯一（pwntools cyclic 同款算法/字母表）。"""
    if n < 3:  # [toolbox-fix 2026-10-30] n<3 时 3 字节针无唯一性，pwnfind 必错
        raise DomainToolError("n 需 ≥3（默认 4）")
    alph = "abcdefghijklmnopqrstuvwxyz"
    if alphabet:
        # [toolbox-fix 2026-10-30] 只保留单字节字符，防止后续 encode("latin-1") 裸崩
        uniq = "".join(sorted(c for c in set(str(alphabet)) if 0 <= ord(c) <= 255))
        if len(uniq) >= 2:
            alph = uniq
    k = len(alph)
    if k ** n > 2_000_000:  # [pwn-toolbox] n>4 时 k^n 爆炸（26^6=3.1 亿），只允许小字母表
        raise DomainToolError("n=%d 字母表 %d 太大（%d 种组合）；用 n=4（默认）或缩小 alphabet" % (n, k, k ** n))
    key = (alph, n)  # [toolbox-fix 2026-10-30] 缓存键必须含字母表本体：同长度不同字母表会串号
    if key not in _CYC_CACHE:
        a = [0] * (k * n)
        seq: list[int] = []

        def db(t: int, p: int) -> None:
            if t > n:
                if n % p == 0:
                    seq.extend(a[1:p + 1])
            else:
                a[t] = a[t - p]
                db(t + 1, p)
                for j in range(a[t - p] + 1, k):
                    a[t] = j
                    db(t + 1, t)

        db(1, 1)
        _CYC_CACHE[key] = "".join(alph[i] for i in seq)
    return _CYC_CACHE[key]


def _latin1(text, what: str) -> bytes:  # [toolbox-fix 2026-10-30] 统一入口，防 UnicodeEncodeError 裸崩
    try:
        return str(text).encode("latin-1")
    except UnicodeEncodeError:
        raise DomainToolError("%s 含多字节字符（>255）；改用 hex= 传原始字节" % what)


def pwngen(op: str = "cyclic", size: int = 256, n: int = 4, alphabet: str = "",
           parts: list | None = None, path: str = "") -> str:
    """[pwn-toolbox] payload 组装器：cyclic 模式串 / parts 拼装（绕开 shell 引号守卫）。"""
    op = (op or "cyclic").strip().lower()
    n = _to_int(n, 4, "n")  # [toolbox-fix 2026-10-30]
    if op == "cyclic":
        size = max(1, min(_to_int(size, 256, "size"), 8192))
        out = _cyclic_seq(n, alphabet).encode("latin-1")[:size]
        info = (
            "De Bruijn 模式串 %d 字节（n=%d，任意连续 %d 字节唯一）。"
            "发给服务触发覆盖后，把回显/寄存器里的 3~8 字节交给 pwnfind（同 n/alphabet）反查偏移。"
            % (len(out), n, n)
        )
        lines = [info, "payload（hex_mode=false 直接发文本）：%s" % _ascii_preview(out, 4000)]
        if path:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(out)
            lines.append("已落盘：%s" % p)
        return _clip("\n".join(lines))
    if op == "build":
        blob = bytearray()
        desc = []
        for i, part in enumerate(parts or []):
            if not isinstance(part, dict):
                raise DomainToolError("parts[%d] 必须是对象，键取 hex/raw/cyclic/pad/int" % i)
            if "hex" in part:
                try:
                    b = bytes.fromhex(str(part["hex"]).replace(" ", "").replace("\n", ""))
                except ValueError as exc:
                    raise DomainToolError("parts[%d].hex 不是合法 hex：%s" % (i, exc))
                desc.append("hex%dB" % len(b))
            elif "raw" in part:
                b = _latin1(part["raw"], "parts[%d].raw" % i)  # [toolbox-fix 2026-10-30]
                desc.append("raw%dB" % len(b))
            elif "cyclic" in part:
                try:  # [toolbox-fix 2026-10-30] 防非整数裸崩
                    cnt = max(1, min(int(part["cyclic"]), 8192))
                except (TypeError, ValueError):
                    raise DomainToolError("parts[%d].cyclic 需要整数，收到 %r" % (i, part["cyclic"]))
                b = _cyclic_seq(n, alphabet).encode("latin-1")[:cnt]
                desc.append("cyclic%dB" % len(b))
            elif "pad" in part:
                ch = (str(part["pad"]) or "A")[:1] or "A"
                try:  # [toolbox-fix 2026-10-30]
                    cnt = max(1, min(int(part.get("count") or 1), 65536))
                except (TypeError, ValueError):
                    raise DomainToolError("parts[%d].count 需要整数，收到 %r" % (i, part.get("count")))
                b = (ch * cnt).encode("latin-1")
                desc.append("pad%r×%d" % (ch, cnt))
            elif "int" in part:
                try:  # [toolbox-fix 2026-10-30]
                    width = int(part.get("width") or 8)
                except (TypeError, ValueError):
                    raise DomainToolError("parts[%d].width 需要整数（2/4/8），收到 %r" % (i, part.get("width")))
                if width not in (2, 4, 8):
                    raise DomainToolError("parts[%d].width 只支持 2/4/8，收到 %r" % (i, width))
                try:
                    v = int(part["int"]) & ((1 << (width * 8)) - 1)
                except (TypeError, ValueError) as exc:
                    raise DomainToolError("parts[%d].int 非法：%s" % (i, exc))
                b = v.to_bytes(width, "little")
                desc.append("int 0x%x→p%dLE" % (v, width * 8))
            else:
                raise DomainToolError("parts[%d] 缺键：hex/raw/cyclic/pad(count)/int(width)" % i)
            blob += b
        out = bytes(blob)
        if len(out) > 131072:
            raise DomainToolError("payload 过大（%d > 131072），分段生成" % len(out))
        lines = ["payload 组装：%s → 共 %d 字节" % (" + ".join(desc) or "(空)", len(out)),
                 "hex=%s" % out.hex(),
                 "ascii=%s" % _ascii_preview(out, 200)]
        if path:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(out)
            lines.append("已落盘：%s（tcp 用 hex_mode=true 发上面 hex；大 payload 分段）" % p)
        return _clip("\n".join(lines))
    raise DomainToolError("op 只支持 cyclic/build，收到 %r" % op)


def pwnfind(value: str = "", hex: str = "", n: int = 4, alphabet: str = "") -> str:
    """[pwn-toolbox] 把崩溃回显的 3~8 字节反查为 cyclic 模式偏移（=覆盖返回地址的填充长度）。"""
    raw = b""
    if hex:
        try:
            raw = bytes.fromhex(str(hex).replace(" ", ""))
        except ValueError as exc:
            raise DomainToolError("hex 不是合法 hex：%s" % exc)
    elif value:
        raw = _latin1(value, "value")  # [toolbox-fix 2026-10-30]
    if not raw:
        raise DomainToolError("给 value=<回显文本> 或 hex=<回显 hex>（3~8 字节）")
    if not (3 <= len(raw) <= 8):
        raise DomainToolError("需要 3~8 字节（32 位常用 4，64 位常用 6~8），收到 %d 字节" % len(raw))
    seq = _cyclic_seq(_to_int(n, 4, "n"), alphabet).encode("latin-1")  # [toolbox-fix 2026-10-30] 与 pwngen 完全同 n
    off = seq.find(raw)
    if off >= 0:
        return (
            "命中：偏移 %d（0x%x）。含义：填充 %d 字节后即是返回地址/PC，"
            "下一步 pwngen op=build（pad %d 字节 + 链）组装最终 payload。" % (off, off, off, off)
        )
    rev = raw[::-1]
    off_r = seq.find(rev)
    if off_r >= 0:
        return "命中（字节序反转=小端读取）：偏移 %d（0x%x）。填充长度 = %d。" % (off_r, off_r, off_r)
    raise DomainToolError(
        "未找到 %r：① 确认 n/alphabet 与 pwngen 一致；② 回显若倒序，把字节倒过来再查；"
        "③ 若回显是地址/乱码而非模式串字节，说明覆盖的不是返回地址，换长度或换入口重试" % raw)


def _vaddr_to_off(elf, vaddr):
    for p_type, _fl, p_offset, p_vaddr, p_filesz in _phdrs(elf):
        if p_type == 1 and p_vaddr <= vaddr < p_vaddr + max(p_filesz, 1):
            return p_offset + (vaddr - p_vaddr)
    return None


def _view_lookup(data, elf, symbol="", search=""):
    """[pwn-toolbox] elfpeek view=lookup：符号/字符串精确偏移 + libc 版本识别。"""
    lines = []
    if symbol:
        sym = symbol.strip()
        hits = [(nm, value) for nm, value, _t in elf.symbols() if nm == sym]
        if hits:
            for nm, value in hits:
                off = _vaddr_to_off(elf, value)
                lines.append("符号 %-20s vaddr 0x%-10x %s" % (
                    nm[:20], value,
                    ("文件偏移 0x%x" % off) if off is not None else "（不在文件段内：可能是导入符号，运行期解析）"))
        else:
            near = [nm for nm, _v, _t in elf.symbols() if sym.lower() in nm.lower()][:10]
            lines.append("符号 %s 未命中%s" % (sym, ("；相近：%s" % ", ".join(near)) if near else "（符号表为空或被 strip）"))
    if search:
        needle = _latin1(search, "search")  # [toolbox-fix 2026-10-30]
        pos, found = 0, []
        while len(found) < 10:
            pos = data.find(needle, pos)
            if pos < 0:
                break
            va = _off_to_vaddr(elf, pos)
            found.append("文件0x%x%s" % (pos, ("/vaddr0x%x" % va) if va is not None else ""))
            pos += 1
        lines.append("字符串 %r：%s" % (search, ", ".join(found) if found else "未找到"))
    if not symbol and not search:
        lines.append("lookup 需要 symbol=<符号名> 或 search=<字符串>（可同时给）")
    vpos = data.find(b"GNU C Library")
    if vpos >= 0:
        head = max(data.rfind(b"\n", 0, vpos), data.rfind(b"\x00", 0, vpos)) + 1
        ends = [x for x in (data.find(b"\n", vpos), data.find(b"\x00", vpos)) if x >= 0]
        tail = min(ends) if ends else len(data)
        ver = data[head:tail].decode("latin-1", "replace").strip()
        lines.append("libc 版本：%s" % ver[:120])
    has_sys = any(nm == "system" for nm, _v, _t in elf.symbols())
    if has_sys and data.find(b"/bin/sh") >= 0:
        lines.append("ret2libc 要素齐：泄漏运行基址后 system=基址+（上面 vaddr−最小 PT_LOAD vaddr），"
                     "字符串偏移同理；无泄漏先打 puts/write@plt 输出 GOT")
    return _clip("\n".join(lines))


# <<< [pwn-toolbox 2026-10-30] ===== pwn 工具箱 v1 实现结束 =====


# >>> [crypto-toolbox 2026-10-30] ===== crypto 方向工具箱 v1：rsatool / aesblock / prng =====
# 动机：crypto 历史 47 试 10 解（21%），瓶颈不是思路而是算力/原语——镜像无 gmpy2/sympy/z3/pycryptodome，
# 模型只能每次手写大数算法与 AES，步数烧光。本段用纯 stdlib 提供 CTF 高频原语，工具经 DOMAIN_SPECS
# 自动注册（同 pwn/rev），不改 crew.py / tools_local.py。


def _int_of(value) -> int:
    """解析大整数：支持十进制、0x 十六进制、带空白的文本。"""
    if isinstance(value, int):
        return value
    text = str(value).strip().replace(" ", "").replace("_", "")
    if not text:
        raise DomainToolError("整数参数为空")
    neg = text.startswith("-")
    body = text[1:] if neg else text
    # 明确 0x/0X 前缀 → 十六进制；否则按十进制，禁止把前导 0 的十进制（如 "0123"）误当十六进制。
    if body.lower().startswith("0x"):
        radix = 16
        body = body[2:]
    else:
        radix = 10
    try:
        val = int(body, radix)
    except ValueError:
        # 仅当十进制解析失败且文本像纯十六进制（0-9a-f，且不含前导 0 的十进制歧义）时回退十六进制
        stripped = body.lstrip("0") or "0"
        if radix == 10 and re.fullmatch(r"[0-9a-fA-F]+", body) and any(c in "abcdefABCDEF" for c in body):
            try:
                val = int(body, 16)
            except ValueError:
                raise DomainToolError("不是合法整数：%r" % text[:40])
        else:
            raise DomainToolError("不是合法整数：%r" % text[:40])
    return -val if neg else val


def _to_int(value, default: int, what: str) -> int:  # [toolbox-fix 2026-10-30] 整数参数统一入口：None/空→默认值，垃圾→DomainToolError（顺带支持 0x 前缀）
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return int(value)
    try:
        return _int_of(value)
    except DomainToolError:
        raise DomainToolError("参数 %s 需要整数（支持 0x 前缀），收到 %r" % (what, value))


def _to_float(value, default: float, what: str) -> float:
    """浮点参数统一入口：None/空→默认值，垃圾→DomainToolError（0x 不支持，超时/秒数用）。"""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        raise DomainToolError("参数 %s 需要数字，收到 %r" % (what, value)) from None


def _hexbytes(text, what: str) -> bytes:  # [toolbox-fix 2026-10-30] hex 参数统一入口，防 ValueError 裸崩
    try:
        return bytes.fromhex(str(text or "").replace(" ", ""))
    except ValueError as exc:
        raise DomainToolError("%s 不是合法 hex：%s" % (what, exc))


def _bytes_of(n: int) -> bytes:
    if n < 0:
        raise DomainToolError("不支持负数转字节")
    if n == 0:
        return b"\x00"
    return n.to_bytes((n.bit_length() + 7) // 8, "big")


def _render_int_result(label: str, value: int, extra=None) -> str:
    raw = _bytes_of(value)
    printable = "".join(chr(b) if 32 <= b < 127 else "." for b in raw[:200])
    lines = ["%s = %d" % (label, value), "  hex=%s" % raw.hex()[:400], "  ascii=%s" % printable]
    if extra:
        lines.extend(extra)
    return _clip("\n".join(lines))


def _iroot(n: int, k: int):
    """整数 k 次根（floor）；精确时返回 (root, True)。"""
    if n < 0:
        raise DomainToolError("负数开方不支持")
    if k < 2:
        raise DomainToolError("k 需 ≥2")
    if n < 2:
        return n, True
    x = 1 << ((n.bit_length() + k - 1) // k)
    while True:
        y = ((k - 1) * x + n // x ** (k - 1)) // k
        if y >= x:
            break
        x = y
    while pow(x + 1, k) <= n:
        x += 1
    while pow(x, k) > n:
        x -= 1
    return x, pow(x, k) == n


def _egcd(a: int, b: int):
    if b == 0:
        return a, 1, 0
    g, x1, y1 = _egcd(b, a % b)
    return g, y1, x1 - (a // b) * y1


def _modinv(a: int, m: int) -> int:
    g, x, _ = _egcd(a % m, m)
    if g != 1:
        raise DomainToolError("modinv 不存在（gcd=%d）" % g)
    return x % m


def _crt(residues, moduli):
    """中国剩余定理：返回 (x, M)；moduli 两两互素。"""
    x, m = 0, 1
    for r, mod in zip(residues, moduli):
        g, inv, _ = _egcd(m % mod, mod)
        if g != 1:
            raise DomainToolError("CRT 模数不互素（gcd=%d）" % g)
        t = ((r - x) % mod) * inv % mod
        x += m * t
        m *= mod
    return x % m, m


def _primes_upto(limit: int):
    sieve = bytearray([1]) * (limit + 1)
    sieve[0] = sieve[1] = 0
    for i in range(2, int(limit ** 0.5) + 1):
        if sieve[i]:
            sieve[i * i::i] = bytearray(len(sieve[i * i::i]))
    return [i for i in range(2, limit + 1) if sieve[i]]


def _fermat_factor(n: int, limit: int = 200000):
    if n % 2 == 0:
        return 2, n // 2
    a = math.isqrt(n)
    if a * a < n:
        a += 1
    for _ in range(max(1, limit)):
        b2 = a * a - n
        b = math.isqrt(b2)
        if b * b == b2:
            return a - b, a + b
        a += 1
    return None


def _pollard_pm1(n: int, bound: int = 10000):
    a = 2
    for p in _primes_upto(bound):
        pk = p
        while pk * p <= bound:
            pk *= p
        a = pow(a, pk, n)
        g = math.gcd(a - 1, n)
        if 1 < g < n:
            return g, n // g
    return None


def _pollard_rho(n: int, limit: int = 200000):
    if n % 2 == 0:
        return 2, n // 2
    if n % 3 == 0:
        return 3, n // 3
    for c in (1, 3, 5, 7, 11, 13):
        x, y, d = 2, 2, 1
        for _ in range(max(1, limit)):
            x = (x * x + c) % n
            y = (y * y + c) % n
            y = (y * y + c) % n
            d = math.gcd(abs(x - y), n)
            if 1 < d < n:
                return d, n // d
            if d == n:
                break
    return None


def _wiener(n: int, e: int):
    """Wiener 攻击：d 很小时用 e/n 连分数收敛子求 phi → 分解。"""
    def cf(num, den, max_terms=200):
        terms = []
        while den and len(terms) < max_terms:
            terms.append(num // den)
            num, den = den, num % den
        return terms

    terms = cf(e, n)
    # 收敛子递推：h_i = a_i*h_{i-1} + h_{i-2}，h_{-2}=0,h_{-1}=1；k_{-2}=1,k_{-1}=0（k_0=1）
    num_hp, num_hc = 0, 1
    den_hp, den_hc = 1, 0
    for t in terms:
        num_hp, num_hc = num_hc, t * num_hc + num_hp
        den_hp, den_hc = den_hc, t * den_hc + den_hp
        k, d = num_hc, den_hc
        if k == 0 or d == 0:
            continue
        if (e * d - 1) % k:
            continue
        phi = (e * d - 1) // k
        s = n - phi + 1
        disc = s * s - 4 * n
        if disc < 0:
            continue
        r = math.isqrt(disc)
        if r * r != disc:
            continue
        p, q = (s + r) // 2, (s - r) // 2
        if p * q == n:
            return p, q, d
    return None


def rsatool(op: str = "factor", n: str = "", e: str = "", c: str = "",
            e1: str = "", e2: str = "", c1: str = "", c2: str = "",
            ns=None, cs=None, B: int = 10000, limit: int = 200000) -> str:
    """[crypto-toolbox] RSA 攻击集（纯 stdlib）：small_e/common_modulus/wiener/fermat/pollard_pm1/pollard_rho/hastad/crt/factor。"""
    op = (op or "factor").strip().lower()
    B = _to_int(B, 10000, "B")        # [toolbox-fix 2026-10-30] 防非整数裸崩
    limit = _to_int(limit, 200000, "limit")  # [toolbox-fix 2026-10-30]
    if op == "small_e":
        ee, cc = _int_of(e), _int_of(c)
        m, exact = _iroot(cc, ee)
        if not exact:
            raise DomainToolError("c 不是 %d 次幂（小 e 开方失败）；试试 hastad/common_modulus" % ee)
        return _render_int_result("m", m)
    if op == "common_modulus":
        nn = _int_of(n)
        a1, a2, b1, b2 = _int_of(e1), _int_of(e2), _int_of(c1), _int_of(c2)
        g, s, t = _egcd(a1, a2)
        if g != 1:
            raise DomainToolError("gcd(e1,e2)=%d≠1：不是标准共模场景" % g)

        def _pow_signed(base: int, exp: int, mod: int) -> int:
            if exp >= 0:
                return pow(base, exp, mod)
            return pow(_modinv(base, mod), -exp, mod)

        m = _pow_signed(b1, s, nn) * _pow_signed(b2, t, nn) % nn
        return _render_int_result("m", m)
    if op == "wiener":
        got = _wiener(_int_of(n), _int_of(e))
        if not got:
            raise DomainToolError("Wiener 未命中（d 不小或参数不对）")
        p, q, d = got
        return _clip("\n".join([
            "Wiener 命中：d = %d" % d, "p = %d" % p, "q = %d" % q,
            "（p*q==n：%s）" % (p * q == _int_of(n)),
        ]))
    if op == "fermat":
        got = _fermat_factor(_int_of(n), int(limit or 200000))
        if not got:
            raise DomainToolError("Fermat 在 %d 次内未分解（p,q 不够接近；换 pollard_rho/pollard_pm1）" % limit)
        p, q = got
        return _clip("Fermat 分解：\np = %d\nq = %d\n（p*q==n：%s）" % (p, q, p * q == _int_of(n)))
    if op == "pollard_pm1":
        got = _pollard_pm1(_int_of(n), int(B or 10000))
        if not got:
            raise DomainToolError("p-1 在 B=%d 内不平滑；加大 B 或换 pollard_rho" % B)
        p, q = got
        return _clip("p-1 分解：\np = %d\nq = %d" % (p, q))
    if op == "pollard_rho":
        got = _pollard_rho(_int_of(n), int(limit or 200000))
        if not got:
            raise DomainToolError("rho 在 %d 次内未分解；加大 limit 或换 fermat/p-1" % limit)
        p, q = got
        return _clip("rho 分解：\np = %d\nq = %d" % (p, q))
    if op == "hastad":
        mods = [_int_of(x) for x in (ns or [])]
        ciphers = [_int_of(x) for x in (cs or [])]
        ee = _int_of(e)
        if len(mods) != len(ciphers) or not mods:
            raise DomainToolError("hastad 需要 ns 与 cs 等长非空")
        if len(mods) < ee:
            raise DomainToolError("Håstad 需要至少 e=%d 组（当前 %d）" % (ee, len(mods)))
        x, _m = _crt(ciphers, mods)
        m, exact = _iroot(x, ee)
        if not exact:
            raise DomainToolError("CRT 组合后不是 %d 次幂（可能明文超出或组数不足）" % ee)
        return _render_int_result("m", m)
    if op == "crt":
        residues = [_int_of(x) for x in (cs or [])]
        mods = [_int_of(x) for x in (ns or [])]
        if len(residues) != len(mods) or not mods:
            raise DomainToolError("crt 需要 ns 与 cs 等长非空")
        x, m = _crt(residues, mods)
        return _render_int_result("x", x, ["模 M = %d" % m])
    if op == "factor":
        nn = _int_of(n)
        for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53):
            if nn % p == 0:
                return _clip("小因子：\np = %d\nq = %d" % (p, nn // p))
        for fn, arg in ((_fermat_factor, min(int(limit or 200000), 50000)),
                        (_pollard_pm1, int(B or 10000)),
                        (_pollard_rho, int(limit or 200000))):
            got = fn(nn, arg)
            if got:
                return _clip("分解成功（%s）：\np = %d\nq = %d" % (fn.__name__, got[0], got[1]))
        raise DomainToolError("常规弱点均未分解；考虑格攻击/Coppersmith（本工具不做）")
    raise DomainToolError("op 只支持 small_e/common_modulus/wiener/fermat/pollard_pm1/pollard_rho/hastad/crt/factor")


# ---------- [crypto-toolbox] AES（纯 stdlib，块/模式原语） ----------

_AES_SBOX = (
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5, 0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    0xca, 0x82, 0xc9, 0x7d, 0xfa, 0x59, 0x47, 0xf0, 0xad, 0xd4, 0xa2, 0xaf, 0x9c, 0xa4, 0x72, 0xc0,
    0xb7, 0xfd, 0x93, 0x26, 0x36, 0x3f, 0xf7, 0xcc, 0x34, 0xa5, 0xe5, 0xf1, 0x71, 0xd8, 0x31, 0x15,
    0x04, 0xc7, 0x23, 0xc3, 0x18, 0x96, 0x05, 0x9a, 0x07, 0x12, 0x80, 0xe2, 0xeb, 0x27, 0xb2, 0x75,
    0x09, 0x83, 0x2c, 0x1a, 0x1b, 0x6e, 0x5a, 0xa0, 0x52, 0x3b, 0xd6, 0xb3, 0x29, 0xe3, 0x2f, 0x84,
    0x53, 0xd1, 0x00, 0xed, 0x20, 0xfc, 0xb1, 0x5b, 0x6a, 0xcb, 0xbe, 0x39, 0x4a, 0x4c, 0x58, 0xcf,
    0xd0, 0xef, 0xaa, 0xfb, 0x43, 0x4d, 0x33, 0x85, 0x45, 0xf9, 0x02, 0x7f, 0x50, 0x3c, 0x9f, 0xa8,
    0x51, 0xa3, 0x40, 0x8f, 0x92, 0x9d, 0x38, 0xf5, 0xbc, 0xb6, 0xda, 0x21, 0x10, 0xff, 0xf3, 0xd2,
    0xcd, 0x0c, 0x13, 0xec, 0x5f, 0x97, 0x44, 0x17, 0xc4, 0xa7, 0x7e, 0x3d, 0x64, 0x5d, 0x19, 0x73,
    0x60, 0x81, 0x4f, 0xdc, 0x22, 0x2a, 0x90, 0x88, 0x46, 0xee, 0xb8, 0x14, 0xde, 0x5e, 0x0b, 0xdb,
    0xe0, 0x32, 0x3a, 0x0a, 0x49, 0x06, 0x24, 0x5c, 0xc2, 0xd3, 0xac, 0x62, 0x91, 0x95, 0xe4, 0x79,
    0xe7, 0xc8, 0x37, 0x6d, 0x8d, 0xd5, 0x4e, 0xa9, 0x6c, 0x56, 0xf4, 0xea, 0x65, 0x7a, 0xae, 0x08,
    0xba, 0x78, 0x25, 0x2e, 0x1c, 0xa6, 0xb4, 0xc6, 0xe8, 0xdd, 0x74, 0x1f, 0x4b, 0xbd, 0x8b, 0x8a,
    0x70, 0x3e, 0xb5, 0x66, 0x48, 0x03, 0xf6, 0x0e, 0x61, 0x35, 0x57, 0xb9, 0x86, 0xc1, 0x1d, 0x9e,
    0xe1, 0xf8, 0x98, 0x11, 0x69, 0xd9, 0x8e, 0x94, 0x9b, 0x1e, 0x87, 0xe9, 0xce, 0x55, 0x28, 0xdf,
    0x8c, 0xa1, 0x89, 0x0d, 0xbf, 0xe6, 0x42, 0x68, 0x41, 0x99, 0x2d, 0x0f, 0xb0, 0x54, 0xbb, 0x16,
)
_AES_RCON = (0x8d, 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36)
# [crypto-toolbox] 逆 S 盒（由正表反推，避免再抄 256 项）
_AES_INV_SBOX = [0] * 256
for _i, _v in enumerate(_AES_SBOX):
    _AES_INV_SBOX[_v] = _i


def _aes_mul(a: int, b: int) -> int:
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        hi = a & 0x80
        a = (a << 1) & 0xFF
        if hi:
            a ^= 0x1B
        b >>= 1
    return r


def _aes_expand(key: bytes):
    nk = len(key) // 4
    if nk not in (4, 6, 8) or len(key) != nk * 4:
        raise DomainToolError("AES key 需 16/24/32 字节，收到 %d" % len(key))
    nr = nk + 6
    w = [list(key[4 * i:4 * i + 4]) for i in range(nk)]
    for i in range(nk, 4 * (nr + 1)):
        temp = list(w[i - 1])
        if i % nk == 0:
            temp = temp[1:] + temp[:1]
            temp = [_AES_SBOX[b] for b in temp]
            temp[0] ^= _AES_RCON[i // nk]
        elif nk > 6 and i % nk == 4:
            temp = [_AES_SBOX[b] for b in temp]
        w.append([w[i - nk][j] ^ temp[j] for j in range(4)])
    return [bytes(sum(w[4 * i:4 * i + 4], [])) for i in range(nr + 1)], nr


def _aes_add_rk(s, rk):
    for i in range(16):
        s[i] ^= rk[i]


def _aes_shift_rows(s):
    out = [0] * 16
    for r in range(4):
        for c in range(4):
            out[r + 4 * c] = s[r + 4 * ((c + r) % 4)]
    return out


def _aes_inv_shift_rows(s):
    out = [0] * 16
    for r in range(4):
        for c in range(4):
            out[r + 4 * c] = s[r + 4 * ((c - r) % 4)]
    return out


def _aes_sub(s):
    return [_AES_SBOX[b] for b in s]


def _aes_inv_sub(s):
    inv = _AES_INV_SBOX
    return [inv[b] for b in s]


def _aes_mix(s):
    out = list(s)
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
        out[4 * c] = _aes_mul(a0, 2) ^ _aes_mul(a1, 3) ^ a2 ^ a3
        out[4 * c + 1] = a0 ^ _aes_mul(a1, 2) ^ _aes_mul(a2, 3) ^ a3
        out[4 * c + 2] = a0 ^ a1 ^ _aes_mul(a2, 2) ^ _aes_mul(a3, 3)
        out[4 * c + 3] = _aes_mul(a0, 3) ^ a1 ^ a2 ^ _aes_mul(a3, 2)
    return out


def _aes_inv_mix(s):
    out = list(s)
    for c in range(4):
        a0, a1, a2, a3 = s[4 * c], s[4 * c + 1], s[4 * c + 2], s[4 * c + 3]
        out[4 * c] = _aes_mul(a0, 14) ^ _aes_mul(a1, 11) ^ _aes_mul(a2, 13) ^ _aes_mul(a3, 9)
        out[4 * c + 1] = _aes_mul(a0, 9) ^ _aes_mul(a1, 14) ^ _aes_mul(a2, 11) ^ _aes_mul(a3, 13)
        out[4 * c + 2] = _aes_mul(a0, 13) ^ _aes_mul(a1, 9) ^ _aes_mul(a2, 14) ^ _aes_mul(a3, 11)
        out[4 * c + 3] = _aes_mul(a0, 11) ^ _aes_mul(a1, 13) ^ _aes_mul(a2, 9) ^ _aes_mul(a3, 14)
    return out


def _aes_encrypt_block(block: bytes, key: bytes) -> bytes:
    rks, nr = _aes_expand(key)
    s = list(block)
    _aes_add_rk(s, rks[0])
    for rnd in range(1, nr):
        s = _aes_sub(s)
        s = _aes_shift_rows(s)
        s = _aes_mix(s)
        _aes_add_rk(s, rks[rnd])
    s = _aes_sub(s)
    s = _aes_shift_rows(s)
    _aes_add_rk(s, rks[nr])
    return bytes(s)


def _aes_decrypt_block(block: bytes, key: bytes) -> bytes:
    rks, nr = _aes_expand(key)
    s = list(block)
    _aes_add_rk(s, rks[nr])
    for rnd in range(nr - 1, 0, -1):
        s = _aes_inv_shift_rows(s)
        s = _aes_inv_sub(s)
        _aes_add_rk(s, rks[rnd])
        s = _aes_inv_mix(s)
    s = _aes_inv_shift_rows(s)
    s = _aes_inv_sub(s)
    _aes_add_rk(s, rks[0])
    return bytes(s)


def _aes_chunks(data: bytes, what: str):
    if len(data) % 16:
        raise DomainToolError("%s 长度 %d 不是 16 的倍数（先 pkcs7_pad）" % (what, len(data)))
    return [data[i:i + 16] for i in range(0, len(data), 16)]


def _aes_xor(a: bytes, b: bytes) -> bytes:
    if len(a) != len(b):
        raise DomainToolError("异或长度不等（%d vs %d）" % (len(a), len(b)))
    return bytes(x ^ y for x, y in zip(a, b))


def aesblock(op: str = "ecb_encrypt", key: str = "", data: str = "", iv: str = "",
             nonce: str = "", counter: int = 0, known: str = "", target: str = "") -> str:
    """[crypto-toolbox] AES 纯 stdlib 原语：ECB/CBC/CTR、异或、CBC bit-flip、PKCS7。"""
    op = (op or "ecb_encrypt").strip().lower()
    key_b = _hexbytes(key, "key")  # [toolbox-fix 2026-10-30] 统一入口：坏 hex 报 ToolError 而非裸崩
    data_b = _hexbytes(data, "data")
    if op in ("pkcs7_pad", "pkcs7_unpad"):
        blk = 16
        if op == "pkcs7_pad":
            pad = blk - len(data_b) % blk
            out = data_b + bytes([pad]) * pad
            return "PKCS7 填充后 %d 字节：\nhex=%s" % (len(out), out.hex())
        if not data_b or len(data_b) % blk:
            raise DomainToolError("unpad 输入长度需为 16 倍数且非空")
        pad = data_b[-1]
        if not (1 <= pad <= blk) or data_b[-pad:] != bytes([pad]) * pad:
            raise DomainToolError("PKCS7 填充非法（末字节 %d）" % pad)
        out = data_b[:-pad]
        return "去填充后 %d 字节：\nhex=%s\nascii=%s" % (len(out), out.hex(), _ascii_preview(out, 200))
    if op == "xor":
        other = _hexbytes(iv, "iv")  # [toolbox-fix 2026-10-30]
        out = _aes_xor(data_b, other)
        return "异或结果 %d 字节：\nhex=%s\nascii=%s" % (len(out), out.hex(), _ascii_preview(out, 200))
    if op == "bitflip":
        iv_b = _hexbytes(iv, "iv")  # [toolbox-fix 2026-10-30]
        kn, tg = (known or ""), (target or "")
        if not kn or len(kn) != len(tg) or len(kn) > len(iv_b):
            raise DomainToolError("bitflip 需要 known/target 等长且 ≤ iv 长度：iv=%d known=%d target=%d"
                                  % (len(iv_b), len(kn), len(tg)))
        if max((ord(c) for c in kn + tg), default=0) > 255:  # [toolbox-fix 2026-10-30] 多字节字符会让 bytearray 赋值裸崩
            raise DomainToolError("bitflip 的 known/target 只支持单字节字符（0-255）；用 python 处理多字节")
        out = bytearray(iv_b)
        n = len(kn)
        for i in range(n):
            out[i] ^= ord(kn[i]) ^ ord(tg[i])
        return _clip("CBC bit-flip：把前块（IV/密文块）改为\nhex=%s\n已知明文 %r → 目标 %r"
                     % (bytes(out).hex(), kn, tg))
    if not key_b:
        raise DomainToolError("需要 key（hex，16/24/32 字节）")
    if op == "ecb_encrypt":
        out = b"".join(_aes_encrypt_block(c, key_b) for c in _aes_chunks(data_b, "data"))
    elif op == "ecb_decrypt":
        out = b"".join(_aes_decrypt_block(c, key_b) for c in _aes_chunks(data_b, "data"))
    elif op == "cbc_encrypt":
        iv_b = _hexbytes(iv, "iv")  # [toolbox-fix 2026-10-30]
        if len(iv_b) != 16:
            raise DomainToolError("CBC 需要 16 字节 IV（hex）")
        prev, chunks = iv_b, []
        for c in _aes_chunks(data_b, "data"):
            prev = _aes_encrypt_block(_aes_xor(c, prev), key_b)
            chunks.append(prev)
        out = b"".join(chunks)
    elif op == "cbc_decrypt":
        iv_b = _hexbytes(iv, "iv")  # [toolbox-fix 2026-10-30]
        if len(iv_b) != 16:
            raise DomainToolError("CBC 需要 16 字节 IV（hex）")
        prev, chunks = iv_b, []
        for c in _aes_chunks(data_b, "data"):
            chunks.append(_aes_xor(_aes_decrypt_block(c, key_b), prev))
            prev = c
        out = b"".join(chunks)
    elif op == "ctr":
        nonce_b = _hexbytes(nonce, "nonce")  # [toolbox-fix 2026-10-30]
        if len(nonce_b) > 16:
            raise DomainToolError("nonce 最长 16 字节")
        base = int.from_bytes(nonce_b, "big") if nonce_b else 0
        chunks, n = [], _to_int(counter, 0, "counter")  # [toolbox-fix 2026-10-30]
        for c in _aes_chunks(data_b, "data"):
            # [toolbox-fix 2026-10-30] 2^128 溢出掩码，防 OverflowError 裸崩
            ks = _aes_encrypt_block(((base + n) & ((1 << 128) - 1)).to_bytes(16, "big"), key_b)
            chunks.append(_aes_xor(c, ks))
            n += 1
        out = b"".join(chunks)
    else:
        raise DomainToolError("op 只支持 ecb_encrypt/ecb_decrypt/cbc_encrypt/cbc_decrypt/ctr/xor/bitflip/pkcs7_pad/pkcs7_unpad")
    return _clip("%s 输出 %d 字节：\nhex=%s\nascii=%s" % (op, len(out), out.hex(), _ascii_preview(out, 200)))


# ---------- [crypto-toolbox] MT19937 / LCG 状态恢复 ----------

_MT_N, _MT_M = 624, 397
_MT_MATRIX_A, _MT_UPPER, _MT_LOWER = 0x9908B0DF, 0x80000000, 0x7FFFFFFF


class _MT19937:
    def __init__(self, seed: int | None = None):
        self.mt = [0] * _MT_N
        self.idx = _MT_N
        if seed is not None:
            self.seed(seed)

    def seed(self, s: int) -> None:
        self.mt[0] = s & 0xFFFFFFFF
        for i in range(1, _MT_N):
            self.mt[i] = (1812433253 * (self.mt[i - 1] ^ (self.mt[i - 1] >> 30)) + i) & 0xFFFFFFFF
        self.idx = _MT_N

    def _gen(self) -> None:
        for i in range(_MT_N):
            y = (self.mt[i] & _MT_UPPER) | (self.mt[(i + 1) % _MT_N] & _MT_LOWER)
            self.mt[i] = self.mt[(i + _MT_M) % _MT_N] ^ (y >> 1)
            if y & 1:
                self.mt[i] ^= _MT_MATRIX_A
        self.idx = 0

    def next(self) -> int:
        if self.idx >= _MT_N:
            self._gen()
        y = self.mt[self.idx]
        self.idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y & 0xFFFFFFFF


def _mt_unshift_right(y: int, shift: int) -> int:
    x = 0
    for i in range(31, -1, -1):
        bit = (y >> i) & 1
        if i + shift < 32:
            bit ^= (x >> (i + shift)) & 1
        x |= bit << i
    return x


def _mt_unshift_left(y: int, shift: int, mask: int) -> int:
    x = 0
    for i in range(32):
        bit = (y >> i) & 1
        if i - shift >= 0:
            bit ^= ((x >> (i - shift)) & 1) & ((mask >> i) & 1)
        x |= bit << i
    return x


def _mt_untemper(y: int) -> int:
    y = _mt_unshift_right(y, 18)
    y = _mt_unshift_left(y, 15, 0xEFC60000)
    y = _mt_unshift_left(y, 7, 0x9D2C5680)
    y = _mt_unshift_right(y, 11)
    return y & 0xFFFFFFFF


def prng(op: str = "mt_predict", seed: int = 0, count: int = 10, outputs=None,
         m: int = 0) -> str:
    """[crypto-toolbox] PRNG 状态恢复：MT19937 预测 / LCG 参数恢复与预测。"""
    op = (op or "mt_predict").strip().lower()
    vals = [_int_of(x) for x in (outputs or [])]
    cnt = max(1, min(_to_int(count, 10, "count"), 2000))  # [toolbox-fix 2026-10-30]
    if op == "mt_seed":
        seed_int = _to_int(seed, 0, "seed")  # [toolbox-fix 2026-10-30]
        gen = _MT19937(seed_int)
        outs = [gen.next() for _ in range(cnt)]
        # [toolbox-fix 2026-10-30] 大 count 会生成 20KB 文本直灌上下文，必须 _clip（平台 6000 字符契约）
        return _clip("MT19937(seed=%d) 前 %d 个输出：\n%s" % (seed_int, cnt, ", ".join(str(v) for v in outs)))
    if op == "mt_predict":
        if len(vals) < 624:
            raise DomainToolError("MT19937 状态恢复需要 ≥624 个连续 32-bit 输出（当前 %d）" % len(vals))
        gen = _MT19937()
        gen.mt = [_mt_untemper(v & 0xFFFFFFFF) for v in vals[:624]]
        gen.idx = 624
        outs = [gen.next() for _ in range(cnt)]
        return _clip("恢复状态成功（%d 个输出），后续 %d 个预测值：\n%s" % (len(vals), cnt, ", ".join(str(v) for v in outs)))
    if op == "lcg_recover":
        if len(vals) < 5:
            raise DomainToolError("LCG 参数恢复需要 ≥5 个连续输出（当前 %d）" % len(vals))
        mod = _to_int(m, 0, "m")  # [toolbox-fix 2026-10-30]
        notes = []
        if not mod:
            ds = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
            cands = []
            for i in range(len(ds) - 2):
                k = ds[i + 2] * ds[i] - ds[i + 1] * ds[i + 1]
                if k:
                    cands.append(abs(k))
            if not cands:
                raise DomainToolError("无法从输出推出模数；请显式给 m=")
            mod = 0
            for c in cands:
                mod = math.gcd(mod, c)
            notes.append("（由差值 GCD 推测模数，可能含多余因子；不对请给 m=）")
        a = (vals[2] - vals[1]) * _modinv((vals[1] - vals[0]) % mod, mod) % mod
        c = (vals[1] - a * vals[0]) % mod
        ok = all((a * vals[i] + c) % mod == vals[i + 1] % mod for i in range(len(vals) - 1))
        if not ok:
            raise DomainToolError("恢复的 a,c 无法复现序列（模数可能不对；给 m=）")
        gen_vals = []
        x = vals[-1]
        for _ in range(cnt):
            x = (a * x + c) % mod
            gen_vals.append(x)
        head = "LCG 恢复成功：m = %d" % mod + "".join(notes)
        return _clip("\n".join([
            head, "a = %d\nc = %d" % (a, c),
            "校验通过；后续 %d 个预测值：\n%s" % (cnt, ", ".join(str(v) for v in gen_vals)),
        ]))
    raise DomainToolError("op 只支持 mt_seed/mt_predict/lcg_recover")


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


# ---------------------------------------------------------------- 回调监听
#
# 为什么需要这个工具（2026-11-12 实测）：SAMPLE-024 是「靶场回连我们」的题型
# （beacon 把 flag 投递到 ctfy-callback:4444）。当时 agent 的工具面只有主动查询
# （tcp/http_request），没有「起监听等回连」，本地只能靠人手起 wc -l/nc 才解出来。
# 正式比赛容器在靶场网内、回调名字会解析到本容器，但 agent 依旧需要能 bind+accept，
# 否则这类题（exfil beacon / SSRF 回调 / reverse connection）直接 0 分。
#
# 单线程 agent 的关键顺序问题：必须先监听、再触发靶场回连。所以本工具允许把
# 「一次触发动作」内置进来（trigger_url / trigger_host），绑好端口后先触发再 accept，
# 这样即使靶场要等回调完成才回包也不会死锁（TCP backlog 会先把连接接住）。


def _local_ipv4s() -> list[str]:
    """本机 IPv4 地址：题面要我们给出回调地址时，模型需要知道该报哪个 IP。"""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = str(info[4][0])
            if ip not in ips:
                ips.append(ip)
    except OSError:
        pass
    if not [ip for ip in ips if not ip.startswith("127.")]:
        # 容器里 gethostname() 常只解析到 127.0.1.1；用一次「选路」拿出口地址（不真的发包）。
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                probe.connect(("192.0.2.1", 9))  # TEST-NET-1：只让内核选路由，不产生通信
                ip = str(probe.getsockname()[0])
            finally:
                probe.close()
            if ip and ip not in ips:
                ips.append(ip)
        except OSError:
            pass
    return ips


def _run_trigger(trigger_url: str = "", trigger_headers=None, trigger_body: str = "",
                 trigger_host: str = "", trigger_port: int = 0, trigger_payload: str = "",
                 trigger_hex: bool = False, timeout: float = 6.0) -> str:
    """监听就绪后自动触发一次外部动作（http / raw tcp），返回一句话结果（失败不阻塞监听）。"""
    url = (trigger_url or "").strip()
    if url:
        import urllib.error
        import urllib.request

        headers = {str(k): str(v) for k, v in (trigger_headers or {}).items()}
        data = None
        if trigger_body:
            data = trigger_body.encode("utf-8", "surrogateescape")
            headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=("POST" if data is not None else "GET"))
        try:
            # 目标白名单已在 LocalTools 侧校验（与 http_request 同一套），此处只做网络请求。
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                body = resp.read(1024)
                status = getattr(resp, "status", None) or resp.getcode()
                return "触发 HTTP %s → %s，回包 %d 字节" % (url, status, len(body))
        except Exception as exc:  # noqa: BLE001 - 触发失败不阻断监听
            return "触发 HTTP %s 失败：%s: %s（监听继续等回连）" % (url, type(exc).__name__, exc)
    host = (trigger_host or "").strip()
    if host:
        port = _to_int(trigger_port, 0, "trigger_port")
        if not (0 < port < 65536):
            return "trigger_port 非法（%r），跳过触发" % (trigger_port,)
        payload = _hexbytes(trigger_payload, "trigger_payload") if trigger_hex else \
            trigger_payload.encode("utf-8", "surrogateescape")
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            try:
                if payload:
                    sock.sendall(payload)
                try:
                    sock.settimeout(0.5)
                    sock.recv(1024)  # 顺手收一下回显（很多服务会先回一段）
                except (socket.timeout, OSError):
                    pass
            finally:
                sock.close()
            return "触发 TCP %s:%d 已发送 %d 字节" % (host, port, len(payload))
        except OSError as exc:
            return "触发 TCP %s:%d 失败：%s: %s（监听继续等回连）" % (host, port, type(exc).__name__, exc)
    return ""


def listen(port: int, bind: str = "", proto: str = "tcp", timeout: float = 30.0,
           max_conns: int = 1, read_timeout: float = 3.0, max_recv: int = 8192,
           reply: str = "", hex_mode: bool = False, send_first: bool = False,
           trigger_url: str = "", trigger_headers=None, trigger_body: str = "",
           trigger_host: str = "", trigger_port: int = 0, trigger_payload: str = "",
           trigger_hex: bool = False) -> str:
    """起本地监听等回连（TCP/UDP），可选先自动触发一次请求/发包。"""
    port = _to_int(port, 0, "port")
    if not (0 < port < 65536):
        raise DomainToolError("端口非法：%r" % (port,))
    if port < 1024:
        raise DomainToolError(
            "端口 %d < 1024：容器内是非 root 用户（uid 10001），绑不上特权端口。"
            "回调端口一般由题面/实例 services 给定（如 4444/1337/8080），换一个 ≥1024 的。" % port)

    bind = (bind or "").strip()
    local_ips = _local_ipv4s()
    if bind in ("", "0.0.0.0", "*", "all"):
        bind_host = ""            # 所有网卡
    elif bind in ("localhost", "127.0.0.1"):
        bind_host = "127.0.0.1"
    elif bind in local_ips:
        bind_host = bind
    else:
        raise DomainToolError(
            "bind=%r 不是本机地址。留空=所有网卡；或填本机 IP（%s）。" % (bind, ", ".join(local_ips) or "未识别到"))

    proto = (proto or "tcp").strip().lower()
    if proto not in ("tcp", "udp"):
        raise DomainToolError("proto 只支持 tcp / udp，收到 %r" % (proto,))
    timeout = max(1.0, min(_to_float(timeout, 30.0, "timeout"), 120.0))
    read_timeout = max(0.2, min(_to_float(read_timeout, 3.0, "read_timeout"), timeout))
    max_conns = max(1, min(_to_int(max_conns, 1, "max_conns"), 8))
    max_recv = max(1, min(_to_int(max_recv, 8192, "max_recv"), MAX_READ))
    reply_bytes = _hexbytes(reply, "reply") if (hex_mode and reply) else \
        (reply or "").encode("utf-8", "surrogateescape")

    head = ["[监听] %s %s:%d 已就绪（等待最多 %.0fs，最多 %d 个%s）" % (
        proto.upper(), bind_host or "0.0.0.0", port, timeout, max_conns,
        "连接" if proto == "tcp" else "数据报")]
    if local_ips:
        head.append("本机地址（题面要你提供回调地址时用这些）：%s" % ", ".join(local_ips))

    if proto == "tcp":
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind((bind_host, port))
            srv.listen(max_conns)
        except OSError as exc:
            srv.close()
            raise DomainToolError("监听 %s:%d 失败：%s: %s（端口被占用就换一个；回调端口以题面为准）"
                                  % (bind_host or "0.0.0.0", port, type(exc).__name__, exc)) from None
    else:
        srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            srv.bind((bind_host, port))
        except OSError as exc:
            srv.close()
            raise DomainToolError("UDP 绑定 %s:%d 失败：%s: %s"
                                  % (bind_host or "0.0.0.0", port, type(exc).__name__, exc)) from None

    triggered = _run_trigger(
        trigger_url=trigger_url, trigger_headers=trigger_headers, trigger_body=trigger_body,
        trigger_host=trigger_host, trigger_port=trigger_port, trigger_payload=trigger_payload,
        trigger_hex=trigger_hex,
    )
    if triggered:
        head.append("[触发] " + triggered)

    results: list[str] = []
    got = 0
    deadline = time.time() + timeout
    try:
        while got < max_conns:
            remain = deadline - time.time()
            if remain <= 0:
                break
            srv.settimeout(max(0.05, remain))
            if proto == "tcp":
                try:
                    conn, peer = srv.accept()
                except socket.timeout:
                    break
                except OSError as exc:
                    results.append("accept 失败：%s: %s" % (type(exc).__name__, exc))
                    break
                got += 1
                data = b""
                sent = 0
                note = ""
                with conn:
                    try:
                        conn.settimeout(max(0.2, min(read_timeout, max(0.2, deadline - time.time()))))
                        if send_first and reply_bytes:
                            conn.sendall(reply_bytes)
                            sent = len(reply_bytes)
                        while len(data) < max_recv:
                            chunk = conn.recv(min(4096, max_recv - len(data)))
                            if not chunk:
                                break
                            data += chunk
                            # request/response 型对端（SSRF 回调、curl 等）在等我们回包，
                            # 「读满/读超时后再回」会让它先超时 —— 收到第一块就回。
                            if reply_bytes and not sent:
                                conn.sendall(reply_bytes)
                                sent = len(reply_bytes)
                    except socket.timeout:
                        note = "（读超时，可能对端还在等我们）"
                    except OSError as exc:
                        note = "（连接异常：%s）" % type(exc).__name__
                results.append("连接 #%d 来自 %s:%s：收到 %d 字节%s%s" % (
                    got, peer[0], peer[1], len(data),
                    ("，已回送 %d 字节" % sent) if sent else "", note))
                if data:
                    results.append("  hex:   " + _hex_preview(data))
                    results.append("  ascii: " + _ascii_preview(data))
            else:
                try:
                    data, peer = srv.recvfrom(max_recv)
                except socket.timeout:
                    break
                except OSError as exc:
                    results.append("recvfrom 失败：%s: %s" % (type(exc).__name__, exc))
                    break
                got += 1
                sent = 0
                if reply_bytes:
                    try:
                        srv.sendto(reply_bytes, peer)
                        sent = len(reply_bytes)
                    except OSError as exc:
                        results.append("回送失败：%s: %s" % (type(exc).__name__, exc))
                results.append("数据报 #%d 来自 %s:%s：收到 %d 字节%s" % (
                    got, peer[0], peer[1], len(data), ("，已回送 %d 字节" % sent) if sent else ""))
                results.append("  hex:   " + _hex_preview(data))
                results.append("  ascii: " + _ascii_preview(data))
    finally:
        try:
            srv.close()
        except OSError:
            pass

    if got == 0:
        results.append("（%.0fs 内没有任何%s到达）" % (timeout, "连接" if proto == "tcp" else "数据报"))
        results.append(
            "排查顺序：① 题面/start_instance 的 services 里回调地址与端口是否一致；"
            "② 是不是要先触发一次（用 trigger_url 或 trigger_host 让它回连，本工具会把触发放在监听之后）；"
            "③ 多网卡时 bind 选错（试 bind=<上面的本机地址>）；④ 端口 <1024 或已被占用。")
    return _clip("\n".join([*head, *results]))


# ---------------------------------------------------------------- 外挂库工具（z3 / pycryptodome / pwntools）
# [libs 2026-11-12] 动机：镜像只带 capstone，CRYPTO/PWN 长期解不动，收尾结论反复是
# 「本地没有库只能纯 stdlib」。tools/fetch_pylibs.py 把 pycryptodome/sympy/gmpy2/z3/pwntools
# 装到 tools/pylibs（git 忽略、不进镜像），此前只有 shell 子进程能用；这里接进进程内工具面：
#   z3solve → rev 的 flag 校验器 / crypto 模方程 / pwn 约束（纯 stdlib 做不到，最高价值）
#   pycrypt → pycryptodome 的 AES-GCM/CFB/OFB、3DES 等（aesblock 只覆盖 ECB/CBC/CTR）
#   pwnrop  → pwntools 的 ELF checksec / plt-got / ROP 链拼接（不用手拼 p64 链）
# 缺库时统一抛「怎么装」的可回显错误（不回退成错误结果）；纯 stdlib 工具不受影响。


def _z3_value(z3, value) -> str:
    """把 z3 模型值格式化成「十进制 + hex（+ 可打印预览）」。"""
    try:
        if z3.is_int_value(value):
            num = value.as_long()
            text = "%d (0x%x)" % (num, num) if num >= 0 else "%d" % num
            if 0 <= num < (1 << 64):
                width = max(1, (num.bit_length() + 7) // 8)
                raw = num.to_bytes(width, "big")
                printable = "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
                if any(32 <= b < 127 for b in raw):
                    text += "  ascii≈%r" % printable
            return text
        if z3.is_bv_value(value):
            num = value.as_long()
            size = value.size()
            return "0x%x (%d)  [bv%d]" % (num, num, size)
        if z3.is_string_value(value):
            return repr(value.as_string())
        if z3.is_rational_value(value):
            return "%s/%s" % (value.numerator_as_long(), value.denominator_as_long())
        return str(value)
    except Exception:  # noqa: BLE001 - 模型值格式化失败不该让整次求解失败
        return str(value)


def z3solve(smt2: str = "", vars: str = "", timeout: float = 10.0, count: int = 1) -> str:
    """用 z3 解 SMT-LIB2 约束（rev flag 校验器 / crypto 模方程 / pwn 约束）。"""
    z3 = _pylibs.import_or_none("z3")
    if z3 is None:
        raise DomainToolError(_pylibs.unavailable_hint("z3-solver", "z3"))
    text = str(smt2 or "").strip()
    if not text:
        raise DomainToolError(
            "需要 smt2（SMT-LIB2 文本：至少 declare-const + assert；z3py 里 `solver.sexpr()` "
            "的输出可直接贴）。例：`(declare-const x Int)(assert (= x 7))`")
    if len(text) > 200_000:
        raise DomainToolError("smt2 太长（%d 字符 > 200000）：只贴与未知数相关的约束，别把整份反编译结果粘进来" % len(text))
    try:
        assertions = z3.parse_smt2_string(text)
    except z3.Z3Exception as exc:
        raise DomainToolError("SMT-LIB2 解析失败：%s" % exc)
    budget = max(1.0, min(float(timeout or 10.0), 30.0))
    want = max(1, min(_to_int(count, 1, "count"), 5))
    names = [v.strip() for v in str(vars or "").replace(";", ",").split(",") if v.strip()]
    solver = z3.Solver()
    solver.set("timeout", int(budget * 1000))
    try:
        solver.add(assertions)
    except z3.Z3Exception as exc:
        raise DomainToolError("约束添加失败：%s" % exc)

    out: list[str] = []
    solutions = 0
    while solutions < want:
        result = solver.check()
        if result == z3.unsat:
            out.append("unsat（无解）" if solutions == 0 else "枚举 %d 组解后 unsat（没有更多解）" % solutions)
            break
        if result == z3.unknown:
            out.append("unknown（超时/理论不支持）：%s" % (solver.reason_unknown() or "无原因"))
            break
        model = solver.model()
        solutions += 1
        out.append("解 #%d（sat）：" % solutions)
        decls = {str(d.name()): d for d in model.decls()}
        for name in (names or list(decls)):
            decl = decls.get(name)
            if decl is None:
                out.append("  %s = （未出现在模型里；检查 vars 拼写）" % name)
                continue
            value = model.eval(z3.Const(name, decl.range()), model_completion=True)
            out.append("  %s = %s" % (name, _z3_value(z3, value)))
        # 阻塞当前解继续枚举
        block = []
        for name, decl in decls.items():
            const = z3.Const(name, decl.range())
            block.append(const != model.eval(const, model_completion=True))
        if not block:
            break  # 没有常量可阻塞（纯布尔断言），不可能有第二个不同的解
        solver.add(z3.Or(*block) if len(block) > 1 else block[0])
    return _clip("\n".join(out))


def _native_guard(what: str, fn):
    """执行可能触发原生模块加载的调用；被系统策略拦截时转成可回显的工具错误。

    pycryptodome/pwntools 的 .pyd/.dll 在 import 阶段常常不加载、真正 new 对象时才加载，
    此时 Windows Smart App Control 会抛 WinError 4551 的 OSError——不兜住就会裸穿透到模型层。
    """
    try:
        return fn()
    except DomainToolError:
        raise
    except Exception as exc:  # noqa: BLE001
        hint = _pylibs.blocked_hint(what, exc)
        if hint:
            raise DomainToolError(hint)
        raise


def pycrypt(op: str = "aes_encrypt", mode: str = "cbc", key: str = "", data: str = "",
            iv: str = "", nonce: str = "", aad: str = "", tag: str = "") -> str:
    """pycryptodome 原语的受控入口（原生库被系统策略拦截时给可操作提示）。"""
    return _native_guard("pycryptodome", lambda: _pycrypt_impl(
        op=op, mode=mode, key=key, data=data, iv=iv, nonce=nonce, aad=aad, tag=tag))


def _pycrypt_impl(op: str = "aes_encrypt", mode: str = "cbc", key: str = "", data: str = "",
                  iv: str = "", nonce: str = "", aad: str = "", tag: str = "") -> str:
    """pycryptodome 原语：AES 的 GCM/CFB/OFB/EAX 模式、3DES（aesblock 之外的补齐）。"""
    aes_mod = _pylibs.import_or_none("Crypto.Cipher.AES")
    if aes_mod is None:
        raise DomainToolError(_pylibs.unavailable_hint("pycryptodome", "Crypto.Cipher.AES"))
    op = (op or "aes_encrypt").strip().lower()
    mode = (mode or "cbc").strip().lower()
    key_b = _hexbytes(key, "key")
    data_b = _hexbytes(data, "data")
    if not key_b:
        raise DomainToolError("需要 key（hex）")
    if not data_b:
        raise DomainToolError("需要 data（hex）")

    if op in ("des3_encrypt", "des3_decrypt"):
        des3_mod = _pylibs.import_or_none("Crypto.Cipher.DES3")
        if des3_mod is None:
            raise DomainToolError(_pylibs.unavailable_hint("pycryptodome", "Crypto.Cipher.DES3"))
        if len(key_b) not in (16, 24):
            raise DomainToolError("3DES key 需要 16 或 24 字节（hex），当前 %d" % len(key_b))
        mode = mode if mode in ("ecb", "cbc") else "cbc"
        iv_b = _hexbytes(iv, "iv")
        pad_mod = _pylibs.import_or_none("Crypto.Util.Padding")
        if pad_mod is None:
            raise DomainToolError(_pylibs.unavailable_hint("pycryptodome", "Crypto.Util.Padding"))
        if op == "des3_encrypt":
            payload = pad_mod.pad(data_b, des3_mod.block_size)
            if mode == "ecb":
                cipher = des3_mod.new(key_b, des3_mod.MODE_ECB)
            else:
                if len(iv_b) != 8:
                    raise DomainToolError("3DES-CBC 需要 8 字节 IV（hex），当前 %d" % len(iv_b))
                cipher = des3_mod.new(key_b, des3_mod.MODE_CBC, iv_b)
            out = cipher.encrypt(payload)
            return "3DES-%s 加密：%d 字节明文 → %d 字节密文\nhex=%s" % (
                mode.upper(), len(data_b), len(out), out.hex())
        if mode == "ecb":
            cipher = des3_mod.new(key_b, des3_mod.MODE_ECB)
        else:
            if len(iv_b) != 8:
                raise DomainToolError("3DES-CBC 需要 8 字节 IV（hex），当前 %d" % len(iv_b))
            cipher = des3_mod.new(key_b, des3_mod.MODE_CBC, iv_b)
        plain = cipher.decrypt(data_b)
        try:
            plain = pad_mod.unpad(plain, des3_mod.block_size)
        except ValueError:
            pass  # 保留原始明文，下面标注
        return "3DES-%s 解密：%d 字节密文 → %d 字节明文\nhex=%s\nascii=%s" % (
            mode.upper(), len(data_b), len(plain), plain.hex(), _ascii_preview(plain, 300))

    if op not in ("aes_encrypt", "aes_decrypt"):
        raise DomainToolError("未知 op=%r（可用 aes_encrypt/aes_decrypt/des3_encrypt/des3_decrypt）" % op)
    if len(key_b) not in (16, 24, 32):
        raise DomainToolError("AES key 需要 16/24/32 字节（hex），当前 %d" % len(key_b))
    nonce_b = _hexbytes(nonce, "nonce")
    iv_b = _hexbytes(iv, "iv")
    aad_b = _hexbytes(aad, "aad")
    tag_b = _hexbytes(tag, "tag")
    pad_mod = _pylibs.import_or_none("Crypto.Util.Padding")
    if pad_mod is None:
        raise DomainToolError(_pylibs.unavailable_hint("pycryptodome", "Crypto.Util.Padding"))

    def _new_cipher():
        if mode == "ecb":
            return aes_mod.new(key_b, aes_mod.MODE_ECB)
        if mode in ("cbc", "cfb", "ofb", "ctr"):
            if not iv_b:
                raise DomainToolError("mode=%s 需要 iv（hex，16 字节）" % mode)
            table = {
                "cbc": aes_mod.MODE_CBC, "cfb": aes_mod.MODE_CFB,
                "ofb": aes_mod.MODE_OFB, "ctr": aes_mod.MODE_CTR,
            }
            if mode == "ctr":
                # pycryptodome 的 CTR 只接受 ≤15 字节 nonce；16 字节就按「完整计数器初值」处理
                if len(iv_b) == 16:
                    counter_mod = _pylibs.import_or_none("Crypto.Util.Counter")
                    if counter_mod is not None:
                        return aes_mod.new(key_b, aes_mod.MODE_CTR,
                                           counter=counter_mod.new(128, initial_value=int.from_bytes(iv_b, "big")))
                if len(iv_b) > 15:
                    raise DomainToolError("CTR 的 iv 需 ≤15 字节（nonce）或 16 字节（完整计数器初值），当前 %d" % len(iv_b))
                return aes_mod.new(key_b, table[mode], nonce=iv_b)
            return aes_mod.new(key_b, table[mode], iv_b)
        if mode in ("gcm", "eax"):
            nonce_use = nonce_b or iv_b or b"\x00" * 12
            table = {"gcm": aes_mod.MODE_GCM, "eax": aes_mod.MODE_EAX}
            return aes_mod.new(key_b, table[mode], nonce=nonce_use)
        raise DomainToolError("未知 mode=%r（可用 ecb/cbc/cfb/ofb/ctr/gcm/eax）" % mode)

    if op == "aes_encrypt":
        cipher = _new_cipher()
        payload = data_b
        if mode in ("ecb", "cbc"):
            payload = pad_mod.pad(data_b, aes_mod.block_size)
        if mode in ("gcm", "eax"):
            if aad_b:
                cipher.update(aad_b)
            out = cipher.encrypt(payload)
            tag_out = cipher.digest()
            return (
                "AES-%s 加密：%d 字节 → %d 字节\nnonce=%s\nhex=%s\ntag=%s\n"
                "（解密时把 nonce/tag 原样带回，aad 若用过也要带回）"
                % (mode.upper(), len(data_b), len(out), (nonce_b or iv_b or b"\x00" * 12).hex(),
                   out.hex(), tag_out.hex()))
        out = cipher.encrypt(payload)
        note = "（已 PKCS7 填充到 %d 字节）" % len(payload) if mode in ("ecb", "cbc") else ""
        return "AES-%s 加密：%d 字节 → %d 字节%s\nhex=%s" % (
            mode.upper(), len(data_b), len(out), note, out.hex())

    cipher = _new_cipher()
    if mode in ("gcm", "eax"):
        if not tag_b:
            raise DomainToolError("AEAD 解密需要 tag（hex）")
        if aad_b:
            cipher.update(aad_b)
        try:
            plain = cipher.decrypt_and_verify(data_b, tag_b)
        except ValueError as exc:
            raise DomainToolError("认证失败（key/nonce/tag/aad 不匹配）：%s" % exc)
        return "AES-%s 解密（认证通过）：%d 字节明文\nhex=%s\nascii=%s" % (
            mode.upper(), len(plain), plain.hex(), _ascii_preview(plain, 300))
    plain = cipher.decrypt(data_b)
    note = ""
    if mode in ("ecb", "cbc") and pad_mod is not None:
        try:
            plain = pad_mod.unpad(plain, aes_mod.block_size)
        except ValueError:
            note = "（PKCS7 去填充失败，返回原始明文）"
    return "AES-%s 解密：%d 字节密文 → %d 字节明文%s\nhex=%s\nascii=%s" % (
        mode.upper(), len(data_b), len(plain), note, plain.hex(), _ascii_preview(plain, 300))


def _pwn_module():
    """惰性拿 pwntools 顶层模块（缺失返回 None）。"""
    return _pylibs.import_or_none("pwn")


def pwnrop(path: str = "", op: str = "rop", func: str = "", args: str = "",
           arg_strings: str = "", filt: str = "", limit: int = 40) -> str:
    """pwntools ELF/ROP 的受控入口（原生库被系统策略拦截时给可操作提示）。"""
    return _native_guard("pwntools", lambda: _pwnrop_impl(
        path=path, op=op, func=func, args=args,
        arg_strings=arg_strings, filt=filt, limit=limit))


def _pwnrop_impl(path: str = "", op: str = "rop", func: str = "", args: str = "",
                 arg_strings: str = "", filt: str = "", limit: int = 40) -> str:
    """pwntools：ELF checksec / plt-got / ROP gadget 搜索 / 自动拼 ROP 链。"""
    pwn = _pwn_module()
    if pwn is None:
        raise DomainToolError(_pylibs.unavailable_hint("pwntools", "pwn"))
    p = Path(path or "")
    if not path or not p.exists():
        raise DomainToolError("文件不存在：%s（先用 download_*_attachment 落盘到沙盒）" % path)
    op = (op or "rop").strip().lower()
    try:
        pwn.context.log_level = "error"
        elf = pwn.ELF(str(p), checksec=False)
    except Exception as exc:  # noqa: BLE001 - 非 ELF/解析失败都回显
        raise DomainToolError("ELF 解析失败：%s" % exc)
    lim = max(1, min(_to_int(limit, 40, "limit"), 200))

    if op == "checksec":
        return _clip("ELF：%s\n%s" % (p, "\n".join(
            line for line in str(elf.checksec()).splitlines() if line.strip())))
    if op == "symbols":
        rows = []
        for name, addr in sorted(elf.plt.items()):
            rows.append("plt  %-28s 0x%x" % (name, addr))
        for name, addr in sorted(elf.got.items()):
            rows.append("got  %-28s 0x%x" % (name, addr))
        for name, addr in sorted(elf.symbols.items()):
            if name in elf.plt or name.startswith("_"):
                continue
            rows.append("sym  %-28s 0x%x" % (name, addr))
        if not rows:
            return "没有可枚举的符号（静态链接/去符号）"
        return _clip("符号 %d 条（前 %d）：\n%s" % (len(rows), lim, "\n".join(rows[:lim])))
    if op == "gadgets":
        rop = pwn.ROP(elf)
        filters = [f.strip() for f in str(filt or "").replace(";", ",").split(",") if f.strip()]
        lines = []
        for gadget in rop.gadgets.values():
            insns = "; ".join(gadget.insns)
            if filters and not any(f in insns for f in filters):
                continue
            lines.append("0x%x  %s" % (gadget.address, insns))
            if len(lines) >= lim:
                break
        if not lines:
            return "没有匹配的 gadget（filt=%r；可留空列出前 %d 个）" % (filt, lim)
        return _clip("gadget %d 个：\n%s" % (len(lines), "\n".join(lines)))
    if op == "rop":
        target = str(func or "").strip()
        if not target:
            raise DomainToolError("op=rop 需要 func（如 system / execve / win）")
        argv: list[int] = []
        for token in str(args or "").replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            try:
                argv.append(int(token, 0))
            except ValueError:
                raise DomainToolError("args 里的 %r 不是整数（0x 前缀可）" % token)
        for token in str(arg_strings or "").split(","):
            token = token.strip()
            if not token:
                continue
            addr = next(elf.search(token.encode()), None)
            if addr is None:
                raise DomainToolError("ELF 里找不到字符串 %r" % token)
            argv.append(int(addr))
        try:
            rop = pwn.ROP(elf)
            rop.call(target, argv)
        except Exception as exc:  # noqa: BLE001 - 符号缺失/链拼不出来都回显
            raise DomainToolError("ROP 链拼接失败：%s（检查 func/参数个数/PIE；PIE 需先拿到基址）" % exc)
        chain = rop.chain()
        return _clip(
            "ROP 链 %d 字节（%d 个 gadget）：\nhex=%s\n%s"
            % (len(chain), len(rop.gadgets), chain.hex(), str(rop.dump())[:2000]))
    raise DomainToolError("未知 op=%r（可用 checksec/symbols/gadgets/rop）" % op)


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

# >>> [rev-toolbox 2026-10-30] elfpeek 规格升级：disasm / bytecode / gadgets v2 / utf16
ELFPEEK_SPEC = {
    "type": "function",
    "function": {
        "name": "elfpeek",
        "description": (
            "纯 stdlib 的 ELF/字节码静态探测（容器里没有 objdump/file/readelf）。"
            "反汇编走 capstone，v5 起镜像内置；缺失时 disasm/gadgets 自动退化（不报错）。"
            "view=info 看架构/入口/PIE/NX/canary/RELRO/入口段；sections/symbols 看节表与符号；"
            "strings 看字符串（utf16=true 扫宽字符串）；"
            "disasm 反汇编（offset/length/count/arch，无 capstone 自动退化字节视图）；"
            "gadgets 枚举 ROP 链（filter=rdi|rsi|rdx|syscall|binsh）；"
            "bytecode 处理 .pyc / PyInstaller（先不带 extract 列目录，再 extract=<名字>，PYZ 用 extract=pyz:<模块名>，func=只看某函数）；"
            "lookup 精确查符号/字符串偏移（ret2libc：symbol=system、search=/bin/sh，附 libc 版本行）；"
            "relocs 看重定位表（JUMP_SLOT 的 @地址就是 foo@got、静态链接判定、.init_array 构造函数）；"
            "dynamic 看动态段（NEEDED 依赖库、RUNPATH/RPATH、BIND_NOW→full RELRO、FLAGS_1 的 PIE 位）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "沙盒内文件路径（相对沙盒根）"},
                "view": {"type": "string", "enum": ["info", "sections", "symbols", "strings", "gadgets", "disasm", "bytecode", "lookup", "relocs", "dynamic"]},
                "min_len": {"type": "integer", "description": "strings 视图的最小串长（默认 4）"},
                "limit": {"type": "integer", "description": "最多输出条目数（默认 60）"},
                "offset": {"type": "integer", "description": "disasm：起始文件偏移（默认吸附到可执行段起点）"},
                "length": {"type": "integer", "description": "disasm：反汇编字节数（默认 128，上限 4096）"},
                "count": {"type": "integer", "description": "disasm：最多指令条数（默认 40，上限 200）"},
                "arch": {"type": "string", "description": "disasm：覆盖架构 x86/x64/arm/arm64（默认按 ELF 头自动判）"},
                "filter": {"type": "string", "description": "gadgets：'|' 分隔关键字，如 rdi|rsi|rdx|syscall|binsh"},
                "extract": {"type": "string", "description": "bytecode：PyInstaller 条目名（PYZ 内模块用 pyz:模块名）"},
                "func": {"type": "string", "description": "bytecode：只反汇编指定函数名（不带则看模块级）"},
                "utf16": {"type": "boolean", "description": "strings：扫 UTF-16 宽字符串"},
                "symbol": {"type": "string", "description": "lookup：精确符号名（如 system；无命中给相近清单）"},
                "search": {"type": "string", "description": "lookup：要查的字符串（如 /bin/sh），返回全部出现偏移"},
            },
            "required": ["path"],
        },
    },
}
# <<< [rev-toolbox 2026-10-30]

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

# >>> [pwn-toolbox 2026-10-30] pwngen / pwnfind 规格（经 DOMAIN_SPECS 自动注册，无需改 crew）
PWNGEN_SPEC = {
    "type": "function",
    "function": {
        "name": "pwngen",
        "description": (
            "纯 stdlib 的 pwn payload 组装器（不要在 shell 里手搓 python 拼串，引号守卫会拦）。"
            "op=cyclic 生成 De Bruijn 模式串（发超长触发覆盖，回显交给 pwnfind 反查精确偏移）；"
            "op=build 用 parts 数组拼 payload：{hex:\"4142\"} / {raw:\"...\"} / {pad:\"A\",count:64} / "
            "{int:0x401000,width:8}（小端 p64）/ {cyclic:128}；path= 落盘到沙盒。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["cyclic", "build"], "description": "cyclic=模式串；build=拼装"},
                "size": {"type": "integer", "description": "cyclic：字节数（默认 256，上限 8192）"},
                "n": {"type": "integer", "description": "模式串子序列长度（默认 4；32 位用 4，64 位常用 8）"},
                "alphabet": {"type": "string", "description": "自定义字母表（默认 a-z；pwnfind 需一致）"},
                "parts": {"type": "array", "items": {"type": "object"}, "description": "build：部件数组"},
                "path": {"type": "string", "description": "可选：落盘到沙盒内此路径"},
            },
            "required": ["op"],
        },
    },
}

PWNFIND_SPEC = {
    "type": "function",
    "function": {
        "name": "pwnfind",
        "description": (
            "把崩溃回显/覆盖返回地址的 3~8 字节反查成 cyclic 模式偏移（= 覆盖返回地址前的填充长度）。"
            "给 value=<回显文本> 或 hex=<回显 hex>；自动尝试字节序反转。n/alphabet 要与 pwngen 一致。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "value": {"type": "string", "description": "回显的文本字节（如 '6Aa7'）"},
                "hex": {"type": "string", "description": "回显的 hex 字节（如 '36616137'）"},
                "n": {"type": "integer", "description": "与 pwngen 相同的 n（默认 4）"},
                "alphabet": {"type": "string", "description": "与 pwngen 相同的字母表（默认 a-z）"},
            },
        },
    },
}
# <<< [pwn-toolbox 2026-10-30]

# >>> [crypto-toolbox 2026-10-30] rsatool / aesblock / prng 规格（同 pwn：经 DOMAIN_SPECS 自动注册）
RSATOOL_SPEC = {
    "type": "function",
    "function": {
        "name": "rsatool",
        "description": (
            "纯 stdlib 的 RSA 攻击集。op=small_e（c 开 e 次根）、common_modulus（同 n 两组 e1/c1/e2/c2）、"
            "wiener（d 小，连分数）、fermat（p,q 接近）、pollard_pm1（p-1 平滑，B 给上界）、"
            "pollard_rho（通用分解，limit 次）、hastad（低指数广播：ns/cs 数组 + e）、crt（模方程）、"
            "factor（小因子/fermat/p-1/rho 自动轮试）。大整数支持 0x 前缀。命中给明文 hex/ascii。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string",
                       "enum": ["small_e", "common_modulus", "wiener", "fermat", "pollard_pm1",
                                "pollard_rho", "hastad", "crt", "factor"]},
                "n": {"type": "string", "description": "RSA 模数"},
                "e": {"type": "string", "description": "公钥指数"},
                "c": {"type": "string", "description": "密文"},
                "e1": {"type": "string", "description": "common_modulus：e1"},
                "e2": {"type": "string", "description": "common_modulus：e2"},
                "c1": {"type": "string", "description": "common_modulus：c1"},
                "c2": {"type": "string", "description": "common_modulus：c2"},
                "ns": {"type": "array", "items": {"type": "string"}, "description": "hastad/crt：模数数组"},
                "cs": {"type": "array", "items": {"type": "string"}, "description": "hastad/crt：密文/余数数组"},
                "B": {"type": "integer", "description": "p-1 平滑界（默认 10000）"},
                "limit": {"type": "integer", "description": "fermat/rho 迭代上限（默认 200000）"},
            },
            "required": ["op"],
        },
    },
}

AESBLOCK_SPEC = {
    "type": "function",
    "function": {
        "name": "aesblock",
        "description": (
            "纯 stdlib AES 原语（无 pycryptodome 也能打分组密码题）。op=ecb_encrypt/ecb_decrypt/"
            "cbc_encrypt/cbc_decrypt/ctr（key, data, iv/nonce, counter 均为 hex）、xor（data 与 iv 两块异或）、"
            "bitflip（CBC 已知明文前块翻转：给 iv/known/target 同长，返回新前块 hex）、pkcs7_pad/pkcs7_unpad。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string",
                       "enum": ["ecb_encrypt", "ecb_decrypt", "cbc_encrypt", "cbc_decrypt", "ctr",
                                "xor", "bitflip", "pkcs7_pad", "pkcs7_unpad"]},
                "key": {"type": "string", "description": "key（hex，16/24/32 字节）"},
                "data": {"type": "string", "description": "数据（hex）"},
                "iv": {"type": "string", "description": "CBC IV（hex，16 字节）/ xor 第二操作数 / bitflip 原前块"},
                "nonce": {"type": "string", "description": "CTR 起始块（hex）"},
                "counter": {"type": "integer", "description": "CTR 起始计数（默认 0）"},
                "known": {"type": "string", "description": "bitflip：已知明文（单块内文本）"},
                "target": {"type": "string", "description": "bitflip：目标明文（同长）"},
            },
            "required": ["op"],
        },
    },
}

PRNG_SPEC = {
    "type": "function",
    "function": {
        "name": "prng",
        "description": (
            "纯 stdlib PRNG 状态恢复/预测。op=mt_seed（seed 给前 count 个 32-bit 输出，校验用）、"
            "mt_predict（给 ≥624 个连续输出 → 恢复状态并预测后续 count 个）、"
            "lcg_recover（给 ≥5 个连续输出，可选 m → 恢复 a/c/m 并预测后续）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["mt_seed", "mt_predict", "lcg_recover"]},
                "seed": {"type": "integer", "description": "mt_seed：种子"},
                "count": {"type": "integer", "description": "预测/生成个数（默认 10，上限 200）"},
                "outputs": {"type": "array", "items": {"type": "integer"},
                            "description": "已观测的连续输出列表"},
                "m": {"type": "integer", "description": "LCG 模数（不给则试推）"},
            },
            "required": ["op"],
        },
    },
}
# <<< [crypto-toolbox 2026-10-30]

# [libs 2026-11-12] 外挂库工具（z3 / pycryptodome / pwntools）：缺库时给「怎么装」的可回显错误。
Z3SOLVE_SPEC = {
    "type": "function",
    "function": {
        "name": "z3solve",
        "description": (
            "用 z3 求解 SMT-LIB2 约束 —— rev 的 flag 校验器、crypto 的模方程、pwn 的约束都靠它。"
            "smt2 直接贴 SMT-LIB2 文本（z3py 里 `print(solver.sexpr())` 的输出可整段复制）；"
            "vars 用逗号列出要看的变量（留空=模型里全部）；count>1 可枚举多组解（同一约束的不同候选）。"
            "本机无 z3 时按提示装（tools/pylibs），不会退回错误答案。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "smt2": {"type": "string",
                         "description": "SMT-LIB2 全文：declare-const/declare-fun + assert；"
                                        "如 `(declare-const x Int)(assert (= (+ x 3) 10))`"},
                "vars": {"type": "string", "description": "要输出的变量名，逗号分隔（默认模型全部）"},
                "timeout": {"type": "number", "description": "求解超时秒（默认 10，上限 30）"},
                "count": {"type": "integer", "description": "枚举解的个数（默认 1，上限 5）"},
            },
            "required": ["smt2"],
        },
    },
}

PYCRYPT_SPEC = {
    "type": "function",
    "function": {
        "name": "pycrypt",
        "description": (
            "pycryptodome 原语（补齐 aesblock 没有的模式）：op=aes_encrypt/aes_decrypt，"
            "mode=ecb/cbc/cfb/ofb/ctr/gcm/eax（gcm/eax 会自动产出/校验 tag，aad 可选，"
            "ecb/cbc 自动 PKCS7）；op=des3_encrypt/des3_decrypt（mode ecb/cbc）。"
            "key/data/iv/nonce/aad/tag 都是 hex。缺库时提示安装路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string",
                       "enum": ["aes_encrypt", "aes_decrypt", "des3_encrypt", "des3_decrypt"]},
                "mode": {"type": "string",
                         "enum": ["ecb", "cbc", "cfb", "ofb", "ctr", "gcm", "eax"],
                         "description": "默认 cbc；gcm/eax 走 AEAD（tag）"},
                "key": {"type": "string", "description": "key（hex；AES 16/24/32 字节，3DES 16/24）"},
                "data": {"type": "string", "description": "输入数据（hex）：加密=明文，解密=密文"},
                "iv": {"type": "string", "description": "CBC/CFB/OFB 的 IV 或 CTR 的 nonce（hex）"},
                "nonce": {"type": "string", "description": "GCM/EAX 的 nonce（hex；不给则 iv 或全零 12 字节）"},
                "aad": {"type": "string", "description": "GCM/EAX 附加认证数据（hex，可选）"},
                "tag": {"type": "string", "description": "GCM/EAX 解密所需的 tag（hex）"},
            },
            "required": ["op", "key", "data"],
        },
    },
}

PWNROP_SPEC = {
    "type": "function",
    "function": {
        "name": "pwnrop",
        "description": (
            "pwntools 的 ELF/ROP 工具：op=checksec（PIE/NX/canary/RELRO）、symbols（plt/got/函数地址）、"
            "gadgets（filt 过滤指令序列，如 \"pop rdi; ret\"）、rop（按 func + args 自动拼 ROP 链并给 hex）。"
            "rop 示例：func=system, arg_strings=/bin/sh → 直接得到调 system('/bin/sh') 的链。"
            "PIE 程序先 leak 基址再用（本工具只给链，不含交互）。缺库时提示安装路径。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "ELF 文件路径（沙盒内）"},
                "op": {"type": "string", "enum": ["checksec", "symbols", "gadgets", "rop"]},
                "func": {"type": "string", "description": "op=rop：要调用的函数名（system/execve/后门函数名）"},
                "args": {"type": "string", "description": "op=rop：整数参数，逗号分隔（支持 0x 前缀）"},
                "arg_strings": {"type": "string", "description": "op=rop：要在 ELF 里搜索的字符串参数（逗号分隔，如 /bin/sh）"},
                "filt": {"type": "string", "description": "op=gadgets：指令过滤（逗号分隔子串）"},
                "limit": {"type": "integer", "description": "最多输出条数（默认 40，上限 200）"},
            },
            "required": ["path", "op"],
        },
    },
}

LISTEN_SPEC = {
    "type": "function",
    "function": {
        "name": "listen",
        "description": (
            "在本地起监听端口等靶场主动回连，取回它发来的数据（回调类题专用）。"
            "典型场景：beacon/靶场把 flag 外带回来、SSRF 回调、reverse connection、"
            "题面说「把结果投递到你的 host:port」而那个 host 就是本容器。"
            "关键顺序：本工具先 bind 再执行一次可选触发（trigger_url 或 trigger_host），然后 accept，"
            "所以「先触发再等回连」这种单线程做不了的顺序被内置解决了。"
            "proto=tcp|udp；bind 留空=所有网卡；reply=收到后回送（如 HTTP 200 响应），"
            "send_first=true 则连上立刻发；阻塞最多 timeout 秒（默认 30、上限 120）。"
            "返回里会列出本机地址——题面要你给回调地址时用它；端口 <1024 绑不上（容器非 root）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "port": {"type": "integer", "description": "回调端口（题面/实例 services 给定，如 4444）"},
                "bind": {"type": "string", "description": "绑定地址，默认留空=所有网卡（多网卡/非 root 场景可填本机 IP）"},
                "proto": {"type": "string", "enum": ["tcp", "udp"], "description": "协议，默认 tcp"},
                "timeout": {"type": "number", "description": "总共等多少秒（默认 30，上限 120）"},
                "max_conns": {"type": "integer", "description": "最多收几个连接/数据报（默认 1，上限 8）"},
                "read_timeout": {"type": "number", "description": "单连接读空闲超时秒（默认 3）"},
                "max_recv": {"type": "integer", "description": "每个连接最多读多少字节（默认 8192）"},
                "reply": {"type": "string", "description": "收到数据后回送的内容（hex_mode=true 时按 hex 解析）"},
                "hex_mode": {"type": "boolean", "description": "reply 是否 hex 编码"},
                "send_first": {"type": "boolean", "description": "true=连上立刻发 reply（服务端先要 banner/命令时用）"},
                "trigger_url": {"type": "string", "description": "监听就绪后自动请求一次该 URL（触发靶场回连；只允许平台实例地址）"},
                "trigger_body": {"type": "string", "description": "trigger_url 的 POST body（给了就发 POST）"},
                "trigger_headers": {"type": "object", "description": "trigger_url 的额外请求头（字符串键值）"},
                "trigger_host": {"type": "string", "description": "监听就绪后自动向该 host 发一次 TCP（与 trigger_url 二选一）"},
                "trigger_port": {"type": "integer", "description": "trigger_host 的端口"},
                "trigger_payload": {"type": "string", "description": "发往 trigger_host 的内容"},
                "trigger_hex": {"type": "boolean", "description": "trigger_payload 是否 hex 编码"},
            },
            "required": ["port"],
        },
    },
}

DOMAIN_SPECS = [TCP_SPEC, TCP_BATCH_SPEC, ELFPEEK_SPEC, NETCHECK_SPEC, PWNGEN_SPEC, PWNFIND_SPEC,
                RSATOOL_SPEC, AESBLOCK_SPEC, PRNG_SPEC, LISTEN_SPEC,
                Z3SOLVE_SPEC, PYCRYPT_SPEC, PWNROP_SPEC]

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
            # [rev-toolbox] 新增 disasm/bytecode/gadgets-v2/utf16 参数透传
            # [toolbox-fix 2026-10-30] 整数参数改走 _to_int：垃圾值报 ToolError（顺带支持 "0x40" 这类 hex 字符串 offset）
            return elfpeek(
                str(args.get("path", "")), view=str(args.get("view") or "info"),
                min_len=_to_int(args.get("min_len"), 4, "min_len"),
                limit=_to_int(args.get("limit"), 60, "limit"),
                offset=_to_int(args.get("offset"), 0, "offset"),
                length=_to_int(args.get("length"), 128, "length"),
                count=_to_int(args.get("count"), 40, "count"),
                arch=str(args.get("arch") or ""),
                extract=str(args.get("extract") or ""),
                func=str(args.get("func") or ""),
                utf16=bool(args.get("utf16", False)),
                filt=str(args.get("filter") or ""),
                symbol=str(args.get("symbol") or ""),
                search=str(args.get("search") or ""),
            )
        if name == "pwngen":  # [pwn-toolbox]（[toolbox-fix 2026-10-30] size/n 传原值，函数内 _to_int）
            return pwngen(
                op=str(args.get("op") or "cyclic"),
                size=args.get("size") if args.get("size") is not None else 256,
                n=args.get("n") if args.get("n") is not None else 4,
                alphabet=str(args.get("alphabet") or ""),
                parts=args.get("parts") if isinstance(args.get("parts"), list) else None,
                path=str(args.get("path") or ""),
            )
        if name == "pwnfind":  # [pwn-toolbox]
            return pwnfind(
                value=str(args.get("value") or ""), hex=str(args.get("hex") or ""),
                n=args.get("n") if args.get("n") is not None else 4,
                alphabet=str(args.get("alphabet") or ""),
            )
        if name == "rsatool":  # [crypto-toolbox]
            return rsatool(
                op=str(args.get("op") or "factor"),
                n=str(args.get("n") or ""), e=str(args.get("e") or ""), c=str(args.get("c") or ""),
                e1=str(args.get("e1") or ""), e2=str(args.get("e2") or ""),
                c1=str(args.get("c1") or ""), c2=str(args.get("c2") or ""),
                ns=args.get("ns") if isinstance(args.get("ns"), list) else None,
                cs=args.get("cs") if isinstance(args.get("cs"), list) else None,
                B=args.get("B") if args.get("B") is not None else 10000,
                limit=args.get("limit") if args.get("limit") is not None else 200000,
            )
        if name == "aesblock":  # [crypto-toolbox]
            return aesblock(
                op=str(args.get("op") or "ecb_encrypt"),
                key=str(args.get("key") or ""), data=str(args.get("data") or ""),
                iv=str(args.get("iv") or ""), nonce=str(args.get("nonce") or ""),
                counter=args.get("counter") if args.get("counter") is not None else 0,
                known=str(args.get("known") or ""), target=str(args.get("target") or ""),
            )
        if name == "prng":  # [crypto-toolbox]
            return prng(
                op=str(args.get("op") or "mt_predict"),
                seed=args.get("seed") if args.get("seed") is not None else 0,
                count=args.get("count") if args.get("count") is not None else 10,
                outputs=args.get("outputs") if isinstance(args.get("outputs"), list) else None,
                m=args.get("m") if args.get("m") is not None else 0,
            )
        if name == "netcheck":
            port = args.get("port")
            return netcheck(str(args.get("host", "")), int(port) if port else None,
                            tls=bool(args.get("tls", False)))
        if name == "listen":
            headers = args.get("trigger_headers")
            return listen(
                port=_to_int(args.get("port"), 0, "port"),
                bind=str(args.get("bind") or ""),
                proto=str(args.get("proto") or "tcp"),
                timeout=_to_float(args.get("timeout"), 30.0, "timeout"),
                max_conns=_to_int(args.get("max_conns"), 1, "max_conns"),
                read_timeout=_to_float(args.get("read_timeout"), 3.0, "read_timeout"),
                max_recv=_to_int(args.get("max_recv"), 8192, "max_recv"),
                reply=str(args.get("reply") or ""),
                hex_mode=bool(args.get("hex_mode", False)),
                send_first=bool(args.get("send_first", False)),
                trigger_url=str(args.get("trigger_url") or ""),
                trigger_headers=headers if isinstance(headers, dict) else None,
                trigger_body=str(args.get("trigger_body") or ""),
                trigger_host=str(args.get("trigger_host") or ""),
                trigger_port=_to_int(args.get("trigger_port"), 0, "trigger_port"),
                trigger_payload=str(args.get("trigger_payload") or ""),
                trigger_hex=bool(args.get("trigger_hex", False)),
            )
        if name == "z3solve":  # [libs 2026-11-12]
            return z3solve(
                smt2=str(args.get("smt2") or ""),
                vars=str(args.get("vars") or ""),
                timeout=_to_float(args.get("timeout"), 10.0, "timeout"),
                count=_to_int(args.get("count"), 1, "count"),
            )
        if name == "pycrypt":  # [libs 2026-11-12]
            return pycrypt(
                op=str(args.get("op") or "aes_encrypt"),
                mode=str(args.get("mode") or "cbc"),
                key=str(args.get("key") or ""), data=str(args.get("data") or ""),
                iv=str(args.get("iv") or ""), nonce=str(args.get("nonce") or ""),
                aad=str(args.get("aad") or ""), tag=str(args.get("tag") or ""),
            )
        if name == "pwnrop":  # [libs 2026-11-12]
            return pwnrop(
                path=str(args.get("path") or ""),
                op=str(args.get("op") or "rop"),
                func=str(args.get("func") or ""),
                args=str(args.get("args") or ""),
                arg_strings=str(args.get("arg_strings") or ""),
                filt=str(args.get("filt") or ""),
                limit=_to_int(args.get("limit"), 40, "limit"),
            )
    except DomainToolError as exc:
        _fail(str(exc))
    except (ValueError, TypeError, struct.error, OverflowError) as exc:
        # [toolbox-fix 2026-11-03] 畸形参数/畸形输入（如 60 字节 ELF 头、port="80abc"、seed="123"）
        # 会触发裸异常穿透到模型层，统一包装成可回显的工具错误。
        _fail("参数或输入非法：%s" % exc)
    _fail("未知领域工具：%s（可用：%s）" % (name, ", ".join(sorted(DOMAIN_NAMES))))
