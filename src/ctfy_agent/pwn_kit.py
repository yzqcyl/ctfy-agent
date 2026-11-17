#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PWN 方向纯 stdlib 工具库：ELF/libc 符号与版本识别、ASan shadow 计算、ret2libc 地址推导。

为什么单独一个模块
------------------
PWN 真实题 0/4 的复盘里，反复出现的不是「数学不会」而是三类机械工作：
  1. 拿到题目附件 `libc.so.6` 后要手算 system/"/bin/sh" 偏移（agent 每次重写一遍，还容易错）；
  2. ASan 题里要算 fake stack / 真实栈的 shadow 地址与解封字节（clean-room 卡的就是这步）；
  3. 泄漏一个符号地址后要反推 libc 基址、再算 system/one-gadget/ROP gadget 地址。
这些是确定性计算，做成工具后模型不用在 `shell` 里现写、也不会算错。

本模块**不冒充完整 ELF 库**：只解析 64/32 位小端 ELF 的段表/节表/符号表/note/字符串，
够 CTF 用；解析失败给明确报错，不猜。所有输出都可以在 `tests/smoke_pwn_kit.py` 里复算。

对外入口
--------
* ``libc_info(data, path="")``      -> dict   libc 版本 / build-id / 关键符号偏移 / "/bin/sh"
* ``ret2libc(data, symbol, leaked)``-> dict   基址 + system + "/bin/sh" + 常见 gadget
* ``asan_shadow(addr, size, bits=64)`` -> dict shadow 地址、区间、需要写的解封字节
* ``assets()``                      -> dict   团队 pwn 资产目录与文件清单

错误统一抛 ``PwnKitError``（工具层转成可读文本，不抛栈）。
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

__all__ = [
    "PwnKitError",
    "Elf",
    "libc_info",
    "ret2libc",
    "asan_shadow",
    "assets",
    "PWN_ASSETS_DIR",
]

PWN_ASSETS_DIR = Path(__file__).resolve().parent / "pwn_assets"

#: ret2libc 最常需要的那批符号（缺哪个会明确列出来）
KEY_LIBC_SYMBOLS = (
    "system", "puts", "printf", "read", "write", "execve", "open", "openat",
    "malloc", "free", "__libc_start_main", "__libc_system", "setvbuf", "gets",
)

_SHT_SYMTAB, _SHT_STRTAB, _SHT_NOTE, _SHT_DYNSYM = 2, 3, 7, 11
_SHF_EXECINSTR = 0x4

#: 常见 gadget 的字节模式（不含 ret2csu；只做「有就用」的启发式，命中会给反汇编不可信提示）
_GADGET_PATTERNS = {
    "pop_rdi": b"\x5f\xc3",
    "pop_rsi": b"\x5e\xc3",
    "pop_rdx": b"\x5a\xc3",
    "pop_rax": b"\x58\xc3",
    "syscall_ret": b"\x0f\x05\xc3",
    "ret": b"\xc3",
}


class PwnKitError(ValueError):
    """pwnkit 的可预期错误（文件不是 ELF、符号缺失、区间非法等）。"""


# ---------------------------------------------------------------- ELF 解析


class Elf:
    """最小 ELF 解析器：只做 CTF 需要的读取，不执行、不做重定位。"""

    def __init__(self, data: bytes):
        if len(data) < 52 or data[:4] != b"\x7fELF":
            raise PwnKitError("不是 ELF 文件（缺少 \\x7fELF 魔数）")
        self.data = data
        self.is64 = data[4] == 2
        if data[5] not in (1, 2):
            raise PwnKitError("ELF 数据编码未知（只支持小端/大端标准值 1/2）")
        self.endian = "<" if data[5] == 1 else ">"
        e = self.endian
        if self.is64:
            if len(data) < 64:
                raise PwnKitError("ELF64 头不完整")
            (self.e_type, self.e_machine, _, self.e_entry, self.e_phoff, self.e_shoff,
             self.e_flags, _, self.e_phentsize, self.e_phnum, self.e_shentsize,
             self.e_shnum, self.e_shstrndx) = struct.unpack_from(e + "HHIQQQIHHHHHH", data, 16)
        else:
            (self.e_type, self.e_machine, _, self.e_entry, self.e_phoff, self.e_shoff,
             self.e_flags, _, self.e_phentsize, self.e_phnum, self.e_shentsize,
             self.e_shnum, self.e_shstrndx) = struct.unpack_from(e + "HHIIIIIHHHHHH", data, 16)
        self._sections: list[dict] | None = None
        self._symbols: list[dict] | None = None

    # -- 节表 -------------------------------------------------------
    def sections(self) -> list[dict]:
        if self._sections is not None:
            return self._sections
        out: list[dict] = []
        e = self.endian
        if self.e_shoff and self.e_shnum and self.e_shentsize:
            fmt = e + ("IIQQQQIIQQ" if self.is64 else "IIIIIIIIII")
            size = struct.calcsize(fmt)
            for i in range(self.e_shnum):
                off = self.e_shoff + i * self.e_shentsize
                if off + size > len(self.data):
                    break
                (name, sh_type, flags, addr, offset, sh_size, link, info,
                 _align, entsize) = struct.unpack_from(fmt, self.data, off)
                out.append({
                    "index": i, "name_off": name, "type": sh_type, "flags": flags,
                    "addr": addr, "offset": offset, "size": sh_size,
                    "link": link, "info": info, "entsize": entsize,
                })
        # 补名字
        names = b""
        if self.e_shstrndx < len(out):
            s = out[self.e_shstrndx]
            names = self.data[s["offset"]:s["offset"] + s["size"]]
        for s in out:
            s["name"] = _cstr(names, s["name_off"])
        self._sections = out
        return out

    def section(self, name: str) -> dict | None:
        for s in self.sections():
            if s["name"] == name:
                return s
        return None

    # -- 符号表 -----------------------------------------------------
    def symbols(self) -> list[dict]:
        if self._symbols is not None:
            return self._symbols
        out: list[dict] = []
        e = self.endian
        for s in self.sections():
            if s["type"] not in (_SHT_SYMTAB, _SHT_DYNSYM):
                continue
            entsize = s["entsize"] or (24 if self.is64 else 16)
            strtab = b""
            if s["link"] < len(self.sections()):
                t = self.sections()[s["link"]]
                strtab = self.data[t["offset"]:t["offset"] + t["size"]]
            count = s["size"] // entsize if entsize else 0
            for i in range(count):
                off = s["offset"] + i * entsize
                if off + entsize > len(self.data):
                    break
                if self.is64:
                    st_name, st_info, st_other, st_shndx, st_value, st_size = \
                        struct.unpack_from(e + "IBBHQQ", self.data, off)
                else:
                    st_name, st_value, st_size, st_info, st_other, st_shndx = \
                        struct.unpack_from(e + "IIIBBH", self.data, off)
                name = _cstr(strtab, st_name)
                if not name:
                    continue
                out.append({
                    "name": name, "value": st_value, "size": st_size,
                    "type": st_info & 0xF, "bind": st_info >> 4, "shndx": st_shndx,
                })
        self._symbols = out
        return out

    def find_symbol(self, name: str) -> dict | None:
        # 先找 FUNC/普通符号；同名多份时取第一个已定义的（shndx != 0）
        cands = [s for s in self.symbols() if s["name"] == name and s["shndx"] != 0]
        return cands[0] if cands else None

    # -- 地址换算 ---------------------------------------------------
    def vaddr_of_offset(self, file_off: int) -> int | None:
        for s in self.sections():
            if s["addr"] and s["type"] != 8 and s["offset"] <= file_off < s["offset"] + max(1, s["size"]):
                return s["addr"] + (file_off - s["offset"])
        return None

    def executable_sections(self) -> list[dict]:
        return [s for s in self.sections() if s["flags"] & _SHF_EXECINSTR and s["size"]]

    # -- 其它 -------------------------------------------------------
    def notes(self) -> list[tuple[str, int, bytes]]:
        out = []
        for s in self.sections():
            if s["type"] != _SHT_NOTE:
                continue
            off, end = s["offset"], s["offset"] + s["size"]
            while off + 12 <= end:
                namesz, descsz, ntype = struct.unpack_from(self.endian + "III", self.data, off)
                off += 12
                name = self.data[off:off + namesz].rstrip(b"\x00")
                off += (namesz + 3) & ~3
                desc = self.data[off:off + descsz]
                off += (descsz + 3) & ~3
                out.append((name.decode("latin-1", "replace"), ntype, desc))
        return out

    def machine_name(self) -> str:
        return {0x3E: "x86-64", 0x03: "i386", 0x28: "arm", 0xB7: "aarch64"}.get(self.e_machine, f"machine0x{self.e_machine:x}")


def _cstr(blob: bytes, off: int) -> str:
    if off < 0 or off >= len(blob):
        return ""
    end = blob.find(b"\x00", off)
    if end < 0:
        end = len(blob)
    return blob[off:end].decode("latin-1", "replace")


# ---------------------------------------------------------------- libc 识别


_VERSION_RES = (
    re.compile(rb"GNU C Library[^\x00\n]{0,120}"),
    re.compile(rb"glibc[^\x00\n]{0,100}", re.I),
    re.compile(rb"Release version[^\x00\n]{0,60}"),
)


def _version_strings(data: bytes) -> list[str]:
    found: list[str] = []
    for rx in _VERSION_RES:
        for m in rx.finditer(data):
            s = m.group(0).decode("utf-8", "replace").strip()
            if s and s not in found:
                found.append(s)
        if found:
            break  # 「GNU C Library」命中就够，不必再扫更泛的模式
    return found[:4]


def _binsh_offsets(elf: Elf, limit: int = 4) -> list[dict]:
    out = []
    start = 0
    while len(out) < limit:
        idx = elf.data.find(b"/bin/sh\x00", start)
        if idx < 0:
            break
        vaddr = elf.vaddr_of_offset(idx)
        out.append({"file_off": idx, "vaddr": vaddr, "vaddr_hex": hex(vaddr) if vaddr else None})
        start = idx + 1
    return out


def libc_info(data: bytes, path: str = "") -> dict:
    """解析 libc.so.6：版本串 / build-id / 关键符号偏移 / "/bin/sh" 位置。"""
    elf = Elf(data)
    symbols: dict[str, dict] = {}
    for name in KEY_LIBC_SYMBOLS:
        sym = elf.find_symbol(name)
        if sym:
            symbols[name] = {"value": sym["value"], "hex": hex(sym["value"]),
                             "size": sym["size"], "type": sym["type"]}
    build_id = ""
    for name, ntype, desc in elf.notes():
        if name == "GNU" and ntype == 3 and desc:
            build_id = desc.hex()
            break
    missing = [n for n in KEY_LIBC_SYMBOLS if n not in symbols]
    return {
        "file": path,
        "class": "ELF64" if elf.is64 else "ELF32",
        "machine": elf.machine_name(),
        "version": _version_strings(elf.data),
        "build_id": build_id,
        "symbols": symbols,
        "missing_symbols": missing,
        "binsh": _binsh_offsets(elf),
        "used_by": "ret2libc：泄漏任一已知符号地址 → 基址 = 泄漏值 − 该符号偏移",
    }


# ---------------------------------------------------------------- ret2libc


def _find_gadgets(elf: Elf, limit: int = 2) -> dict[str, list[str]]:
    """在可执行节里按字节模式找常见 gadget（启发式；只给偏移，不保证无前缀）。"""
    out: dict[str, list[str]] = {}
    for name, pat in _GADGET_PATTERNS.items():
        hits: list[int] = []
        for sec in elf.executable_sections():
            blob = elf.data[sec["offset"]:sec["offset"] + sec["size"]]
            start = 0
            while len(hits) < limit:
                idx = blob.find(pat, start)
                if idx < 0:
                    break
                hits.append(sec["addr"] + idx)
                start = idx + 1
        if hits:
            out[name] = [hex(h) for h in hits]
    return out


def ret2libc(data: bytes, symbol: str, leaked: int, path: str = "") -> dict:
    """给「泄漏到的某个 libc 符号地址」反推基址 + system + "/bin/sh" + 常见 gadget。"""
    elf = Elf(data)
    sym = elf.find_symbol(symbol)
    if not sym:
        near = sorted({s["name"] for s in elf.symbols() if symbol.lower() in s["name"].lower()})[:8]
        raise PwnKitError(f"libc 里没有符号 {symbol!r}（相近：{near or '无'}）")
    if sym["value"] <= 0:
        raise PwnKitError(f"符号 {symbol!r} 地址为 0（可能是 IFUNC/未定义符号）")
    if int(leaked) < sym["value"]:
        raise PwnKitError(
            f"泄漏值 {hex(int(leaked))} 小于符号偏移 {hex(sym['value'])}，"
            "通常说明泄漏的不是该符号的运行地址")
    base = int(leaked) - sym["value"]
    out = {
        "symbol": symbol,
        "symbol_offset": hex(sym["value"]),
        "leaked": hex(int(leaked)),
        "base": hex(base),
        "base_dec": base,
        "libc": path,
    }
    for name in ("system", "__libc_system", "execve"):
        s = elf.find_symbol(name)
        if s:
            out[name] = hex(base + s["value"])
    binsh = _binsh_offsets(elf, limit=1)
    if binsh and binsh[0]["vaddr"]:
        out["binsh"] = hex(base + binsh[0]["vaddr"])
        out["binsh_offset"] = hex(binsh[0]["vaddr"])
    gadgets = _find_gadgets(elf)
    if gadgets:
        # 所有 gadget 都是 libc 内偏移，统一加基址（没有「保持偏移」的例外）。
        out["gadgets"] = {k: [hex(int(x, 16) + base) for x in v] for k, v in gadgets.items()}
        out["gadgets_note"] = (
            "地址已加基址；字节模式启发式，命中处前面可能有别的指令前缀，必要时用 elfpeek view=disasm 复核")
    return out


# ---------------------------------------------------------------- ASan shadow


_SHADOW_OFFSET_64 = 0x7FFF8000
_SHADOW_OFFSET_32 = 0x20000000


def asan_shadow(addr: int, size: int = 8, bits: int = 64) -> dict:
    """算 ASan shadow 地址与「写真实内存前要解封的 shadow 字节」。

    公式（x86_64，64-bit ASan）：``shadow(addr) = (addr >> 3) + 0x7fff8000``；
    每个 shadow 字节管 8 个真实字节，低 3 位记录「前 N 个字节可用」。
    """
    addr = int(addr)
    size = int(size)
    if addr <= 0 or addr > 0x7FFF_FFFF_FFFF:
        raise PwnKitError(f"地址 {hex(addr)} 不在用户态 64 位范围，无法算 shadow")
    if size <= 0 or size > 0x10000:
        raise PwnKitError(f"size={size} 不合法的解封窗口（1..65536）")
    off = _SHADOW_OFFSET_64 if int(bits) == 64 else _SHADOW_OFFSET_32
    return _asan_plan(addr, size, off, int(bits))


def _asan_plan(addr: int, size: int, shadow_offset: int, bits: int) -> dict:
    first = addr >> 3
    last = (addr + size - 1) >> 3
    shadow_start = first + shadow_offset
    plan: list[dict] = []
    for idx in range(first, last + 1):
        covered_lo = idx * 8
        lo = max(addr, covered_lo)
        hi = min(addr + size, covered_lo + 8)
        # 该 shadow 字节描述 covered_lo..covered_lo+7 的可用字节数
        if lo != covered_lo:
            # 起始地址不落在块首：从块首到目标起点之间也必须放开，否则 ASan 仍按
            # 「前缀可用 N 字节」判定并拦截目标处的写；整块放开（多余的放开无害）。
            value = 0
        elif hi == covered_lo + 8:
            value = 0  # 整 8 字节都可访问
        else:
            value = hi - covered_lo  # 前 value 个字节可访问（块尾部不足 8）
        plan.append({
            "shadow_addr": hex(idx + shadow_offset),
            "shadow_value": value,
            "value_hex": hex(value),
            "covers": f"{hex(max(covered_lo, lo))}..{hex(hi - 1)}",
        })
    return {
        "addr": hex(addr),
        "size": size,
        "bits": bits,
        "shadow_offset": hex(shadow_offset),
        "shadow_start": hex(shadow_start),
        "shadow_span": f"{hex(shadow_start)}..{hex(last + shadow_offset)}",
        "plan": plan,
        "restore_hint": "解封写入完成后把同一批 shadow 字节恢复成原 poison 值（ASan 常见 stack-use-after-return 是 0xfa/0xf1；"
                        "先读一次原值再写回，别硬编码）",
    }


# ---------------------------------------------------------------- 团队资产


def assets() -> dict:
    """团队 pwn 资产目录（泄漏链 / ASan shadow / 解封写栈模板）的文件清单。"""
    files = []
    if PWN_ASSETS_DIR.is_dir():
        for p in sorted(PWN_ASSETS_DIR.glob("*.py")):
            if p.name.startswith("__"):
                continue
            files.append({"name": p.name, "bytes": p.stat().st_size, "path": str(p)})
    return {
        "dir": str(PWN_ASSETS_DIR),
        "exists": PWN_ASSETS_DIR.is_dir(),
        "files": files,
        "usage": "shell 里 `python3 <dir>/<file> --help` 或 import（目录见 dir）；"
                 "泄漏链先跑 leak_helpers.py，ASan 题用 asan_shadow.py + pwn_stack_probe.py",
    }