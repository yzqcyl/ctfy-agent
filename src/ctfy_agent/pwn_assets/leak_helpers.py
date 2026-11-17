#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""团队资产：泄漏链辅助（面向 clean-room 这类 ASan 记录管理器）。

解决 2026-11-16 二轮止步的两个具体卡点：
  1) "泄漏读取不足 8 字节" —— 统一用 ``read8()``：读满 8 字节并**断言长度**，短读直接
     报出长度/地址/返回原样，不许静默继续；
  2) PIE/libc 基址换算 —— 用 ``ElfOffsets`` / ``LibcOffsets`` 直接查 ELF 符号与 GOT 槽偏移，
     不用手算 readelf 输出；``base_from()`` 从泄漏值反推基址。

用法（在题目沙盒里）::

    from leak_helpers import MenuClient, ElfOffsets, LibcOffsets, read8, u64, p64

    elf = ElfOffsets("challenge")          # 附件 PIE 二进制
    libc = LibcOffsets("libc.so.6")
    print("main", hex(elf.sym("main")), "got.malloc", hex(elf.got("malloc")))
    print("libc.openat", hex(libc.sym("openat")))

    cli = MenuClient("127.0.0.1", 12345)   # clean-room 菜单协议
    cli.expect(b"> ")
    # ... 用自己的任意读原语实现 read_fn(addr) -> bytes ...
    # leak = read8(read_fn, pie_base + elf.got("malloc"))

自检：``python3 leak_helpers.py challenge libc.so.6`` 会打印关键偏移。
"""
from __future__ import annotations

import socket
import struct
import subprocess
import sys


def u64(data: bytes) -> int:
    assert len(data) >= 8, f"u64 需要 8 字节，实际 {len(data)}: {data!r}"
    return struct.unpack("<Q", data[:8])[0]


def p64(value: int) -> bytes:
    return struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF)


def hexdump(data: bytes, base: int = 0, limit: int = 128) -> str:
    lines = []
    view = data[:limit]
    for off in range(0, len(view), 16):
        chunk = view[off:off + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk.ljust(16, b"\x00"))
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base + off:08x}  {hexpart}  {text}")
    if len(data) > limit:
        lines.append(f"... ({len(data)} bytes total, 只显示前 {limit})")
    return "\n".join(lines)


class _Elf:
    """pyelftools 读符号/GOT；没有 pyelftools 时退回 readelf 文本解析。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self._syms: dict[str, int] = {}
        self._got: dict[str, int] = {}
        self._relatives: dict[int, int] = {}
        self._load()

    def _load(self) -> None:
        try:
            from elftools.elf.elffile import ELFFile  # noqa: PLC0415

            with open(self.path, "rb") as fh:
                elf = ELFFile(fh)
                for section_name in (".symtab", ".dynsym"):
                    section = elf.get_section_by_name(section_name)
                    if section is None:
                        continue
                    for sym in section.iter_symbols():
                        name = sym.name
                        value = int(sym["st_value"] or 0)
                        if name and value and name not in self._syms:
                            self._syms[name] = value
                dynsym = elf.get_section_by_name(".dynsym")
                for section_name in (".rela.plt", ".rela.dyn"):
                    section = elf.get_section_by_name(section_name)
                    if section is None:
                        continue
                    for rel in section.iter_relocations():
                        try:
                            offset = int(rel["r_offset"] or 0)
                            rel_type = int(rel["r_info_type"] or 0)
                            if rel_type == 8:      # R_X86_64_RELATIVE：槽位值 = 基址 + addend
                                self._relatives[offset] = int(rel["r_addend"] or 0)
                                continue
                            sym_index = int(rel["r_info_sym"] or 0)
                            if not sym_index or dynsym is None:
                                continue
                            name = dynsym.get_symbol(sym_index).name
                        except Exception:  # noqa: BLE001 - 单条 relocation 格式差异
                            continue
                        if name and name not in self._got:
                            self._got[name] = offset
            return
        except Exception:  # noqa: BLE001 - 没有 pyelftools / 非 ELF 就走 readelf
            self._load_readelf()

    def _load_readelf(self) -> None:
        out = subprocess.run(["readelf", "-sW", self.path], capture_output=True, text=True).stdout
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 8 and parts[3] == "FUNC":
                try:
                    self._syms.setdefault(parts[7], int(parts[1], 16))
                except ValueError:
                    continue
        got_out = subprocess.run(["readelf", "-rW", self.path], capture_output=True,
                                 text=True).stdout
        for line in got_out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            if parts[2] == "R_X86_64_JUMP_SLOT" and len(parts) >= 5:
                try:
                    self._got.setdefault(parts[4], int(parts[0], 16))
                except (ValueError, IndexError):
                    continue
            elif parts[2] == "R_X86_64_RELATIVE" and len(parts) >= 4:
                try:
                    self._relatives.setdefault(int(parts[0], 16), int(parts[3], 16))
                except ValueError:
                    continue

    def sym(self, name: str) -> int:
        if name not in self._syms:
            raise KeyError(f"{self.path} 里没有符号 {name!r}（用 readelf -s 看可用符号）")
        return self._syms[name]

    def got(self, name: str) -> int:
        if not self._got:
            self._load_readelf()
        if name not in self._got:
            raise KeyError(f"{self.path} GOT 里没有 {name!r}（用 readelf -r 看）")
        return self._got[name]

    def has(self, name: str) -> bool:
        return name in self._syms

    def base_from(self, leaked: int, symbol: str) -> int:
        """leaked 是运行时该符号地址 → 返回模块基址（PIE/共享库都适用）。"""
        return leaked - self.sym(symbol)

    def relative_addend(self, slot_offset: int) -> int:
        """R_X86_64_RELATIVE 槽位（文件偏移 slot_offset）里的 addend。

        PIE 运行时该槽位值 = 模块基址 + addend，所以泄漏到它就能反推基址。
        """
        if not self._relatives:
            self._load_readelf()
        if slot_offset not in self._relatives:
            raise KeyError(f"{self.path} 在偏移 {slot_offset:#x} 没有 RELATIVE 重定位")
        return self._relatives[slot_offset]

    def pie_base_from_slot(self, slot_offset: int, leaked: int) -> int:
        """泄漏到 slot_offset 处的指针值 leaked → PIE 基址。"""
        return leaked - self.relative_addend(slot_offset)

    def relatives(self) -> dict[int, int]:
        """全部 RELATIVE 槽位 {槽位文件偏移: addend}，按偏移排序（找"已知位置的 PIE 指针"用）。"""
        if not self._relatives:
            self._load_readelf()
        return dict(sorted(self._relatives.items()))

    def first_relatives(self, count: int = 10) -> list[tuple[int, int]]:
        return list(self.relatives().items())[:count]


class ElfOffsets(_Elf):
    """题目二进制（通常是 PIE）：sym()/got() 返回的是**文件内偏移**，加基址才是运行时地址。"""


class LibcOffsets(_Elf):
    """附件 libc：sym() 返回符号偏移；base_from(leaked_addr, "malloc") 得 libc base。"""


class MenuClient:
    """clean-room 那类行菜单服务的客户端：expect/sendline/recv 定长。"""

    def __init__(self, host: str, port: int, timeout: float = 8.0) -> None:
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def recv_some(self, n: int = 4096) -> bytes:
        data = self.sock.recv(n)
        if not data:
            raise EOFError("对端关闭连接")
        self.buf += data
        return data

    def expect(self, needle: bytes, max_bytes: int = 65536) -> bytes:
        while needle not in self.buf:
            if len(self.buf) > max_bytes:
                raise RuntimeError(
                    f"超过 {max_bytes} 字节仍没等到 {needle!r}；当前尾部={self.buf[-80:]!r}")
            self.recv_some()
        idx = self.buf.index(needle) + len(needle)
        out, self.buf = self.buf[:idx], self.buf[idx:]
        return out

    def sendline(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode()
        self.sock.sendall(data + b"\n")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def read8(read_fn, addr: int) -> int:
    """任意读原语的统一收口：读 8 字节，长度不足直接抛错（而不是返回残值）。

    read_fn(addr) 返回 bytes。2026-11-16 的 exploit_b 就是静默拿到 <8 字节继续算，
    结果基址全错；这里把"短读"变成显式错误，附上长度/地址/hex 方便定位。
    """
    raw = read_fn(addr)
    if not isinstance(raw, (bytes, bytearray)):
        raise TypeError(f"read_fn 应返回 bytes，实际 {type(raw)}")
    if len(raw) < 8:
        raise ValueError(f"短读：addr={addr:#x} 只拿到 {len(raw)} 字节 {bytes(raw)!r}"
                         f"（检查读取长度/MAX_ECHO_TOTAL/预算，或换逐字节拼接）")
    return u64(bytes(raw))


def _selftest() -> int:
    if len(sys.argv) < 2:
        print("usage: python3 leak_helpers.py <challenge> [libc.so.6]")
        return 2
    elf = ElfOffsets(sys.argv[1])
    print("== challenge ==")
    for name in ("main", "puts", "malloc", "free"):
        if elf.has(name):
            print(f"  sym {name:12s} off={elf.sym(name):#x}")
    for name in ("malloc", "free", "puts"):
        try:
            print(f"  got {name:12s} off={elf.got(name):#x}")
        except KeyError:
            pass
    try:
        rel = elf.first_relatives(3)
        print(f"  RELATIVE 槽位 {len(elf.relatives())} 个，前几个："
              + "，".join(f"{off:#x}->{add:#x}" for off, add in rel))
    except KeyError:
        pass
    if len(sys.argv) >= 3:
        libc = LibcOffsets(sys.argv[2])
        print("== libc ==")
        for name in ("__libc_start_main", "openat", "read", "write", "system"):
            if libc.has(name):
                print(f"  sym {name:20s} off={libc.sym(name):#x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())