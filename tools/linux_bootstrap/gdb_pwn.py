#!/usr/bin/env python3
"""pwndbg-lite：`gdb -batch -q -x gdb_pwn.py -ex run -ex pwnrecon ./poc < payload.bin`

纯 gdb Python 扩展，不依赖 pwndbg/pwntools；镜像里只要有 gdb 就能用（headless 宿主机没有 gdb，
所以这套只在提交镜像/容器内跑）。命令：

  pwnrecon        保护/映射/寄存器/栈顶 一屏（checksec 的运行时版）
  pwnmaps [pat]   进程映射（可过滤 libc/heap/stack），并给 libc / PIE 基址
  pwnoff          用 pwntools cyclic 反查 RIP/RSP 值对应的偏移（崩溃后第一条命令）
  pwnstack [n]    栈顶 n 个 qword（默认 12）
  pwnchunks [n]   heap 上连续 chunk 的 size/flags/fd/bk（tcache/fastbin 观察用）
  pwnio           _IO_2_1_stdout_ / _IO_list_all / vtable 指针（FSOP house-of-apple 用）
  pwnbins         有 libc 符号时打印 main_arena fastbins；没有就提示退化方案

为什么不依赖 pwndbg：镜像只带 gdb 批处理（无界面、无 Python 第三方库），装 pwndbg 需要
网络 + pip；这套 200 行脚本覆盖 80% 的「看现场」需求，且不占镜像体积。
"""
import gdb

ALPHABET = "abcdefghijklmnopqrstuvwxyz"
LOOKUP = {c: i for i, c in enumerate(ALPHABET)}


def _cyclic(length: int) -> bytes:
    out = bytearray()
    i = 0
    while len(out) < length:
        out += bytes(ord(ALPHABET[(i // (26 ** j)) % 26]) for j in (0, 1, 2, 3))
        i += 1
    return bytes(out[:length])


def _cyclic_offset(value: int, size: int = 8) -> int:
    raw = int(value).to_bytes(size, "little")
    for start in range(0, max(1, len(raw) - 3)):
        chunk = raw[start:start + 4]
        if len(chunk) < 4 or any(chr(c) not in LOOKUP for c in chunk):
            continue
        idx = sum(LOOKUP[chr(chunk[j])] * (26 ** j) for j in range(4))
        pos = _cyclic(idx * 4 + 4).find(chunk)
        if pos >= 0:
            return pos
    return -1


def _mem(addr: int, size: int) -> bytes:
    return bytes(gdb.selected_inferior().read_memory(addr, size))


def _ptr(addr: int) -> int:
    return int.from_bytes(_mem(addr, 8), "little")


def _eval(expr: str):
    try:
        return gdb.parse_and_eval(expr)
    except gdb.error:
        return None


class _Cmd(gdb.Command):
    def invoke(self, arg, from_tty):
        try:
            self.run(arg.split())
        except gdb.error as exc:
            print("[gdb_pwn] error: %s" % exc)


class PwnRecon(_Cmd):
    def __init__(self):
        super().__init__("pwnrecon", gdb.COMMAND_USER)

    def run(self, args):
        print("== pwnrecon ==")
        print(gdb.execute("info proc mappings", to_string=True).strip()[-2500:])
        for reg in ("rip", "rsp", "rbp", "rdi", "rsi", "rdx", "rcx"):
            val = _eval("$" + reg)
            if val is not None:
                print("%-4s = %#x" % (reg, int(val)))
        rsp = int(_eval("$rsp"))
        print("\n-- stack top --")
        print(gdb.execute("x/12gx $rsp", to_string=True).rstrip())
        print("\n(hint) 崩溃后先 pwnoff 反查偏移；堆题 pwnchunks；FSOP 用 pwnio")


class PwnMaps(_Cmd):
    def __init__(self):
        super().__init__("pwnmaps", gdb.COMMAND_USER)

    def run(self, args):
        pat = args[0] if args else ""
        out = gdb.execute("info proc mappings", to_string=True)
        for line in out.splitlines():
            if not pat or pat in line:
                print(line)
        for name in ("libc.so", "libc-"):
            for line in out.splitlines():
                if name in line and line.strip().split()[0].startswith("0x"):
                    print("[gdb_pwn] %s 基址 ≈ %s" % (name, line.strip().split()[0]))
                    break
        print("[gdb_pwn] PIE 基址：映射里最短的 r-xp 段起点（或 info files 的 Entry point 段）")


class PwnOff(_Cmd):
    def __init__(self):
        super().__init__("pwnoff", gdb.COMMAND_USER)

    def run(self, args):
        regs = args or ["rip", "rsp"]
        for reg in regs:
            val = _eval("$" + reg)
            if val is None:
                continue
            raw = int(val)
            off = _cyclic_offset(raw)
            if off >= 0:
                print("[gdb_pwn] $%s = %#x -> cyclic 偏移 %d（覆盖到这里的偏移 = %d）"
                      % (reg, raw, off, off))
            else:
                # 寄存器值本身不是 pattern 时，看栈上是否有 pattern（返回地址被覆盖的常见情形）
                try:
                    mem = _mem(int(_eval("$rsp")), 8 * 16)
                except gdb.error:
                    continue
                for i in range(0, len(mem), 8):
                    off2 = _cyclic_offset(int.from_bytes(mem[i:i + 8], "little"))
                    if off2 >= 0:
                        print("[gdb_pwn] $rsp+%d 的值为 pattern -> 偏移 %d" % (i, off2))
                        break


class PwnStack(_Cmd):
    def __init__(self):
        super().__init__("pwnstack", gdb.COMMAND_USER)

    def run(self, args):
        n = int(args[0]) if args else 12
        print(gdb.execute("x/%dgx $rsp" % n, to_string=True).rstrip())


class PwnChunks(_Cmd):
    def __init__(self):
        super().__init__("pwnchunks", gdb.COMMAND_USER)

    def run(self, args):
        n = int(args[0]) if args else 8
        maps = gdb.execute("info proc mappings", to_string=True)
        heap = None
        for line in maps.splitlines():
            if "heap" in line and line.strip().split()[0].startswith("0x"):
                heap = int(line.strip().split()[0], 16)
                break
        if heap is None:
            print("[gdb_pwn] 没找到 [heap] 段（程序还没 malloc？）")
            return
        cur = heap
        for _ in range(n):
            try:
                prev_size = _ptr(cur)
                size = _ptr(cur + 8)
            except gdb.error:
                break
            if size == 0:
                break
            flags = size & 0x7
            real = size & ~0x7
            try:
                fd = _ptr(cur + 16)
                bk = _ptr(cur + 24)
            except gdb.error:
                fd = bk = 0
            print("chunk %#x size=%#x (real %#x, prev_inuse=%d) fd=%#x bk=%#x"
                  % (cur, size, real, flags & 1, fd, bk))
            if real == 0:
                break
            cur += real


class PwnIo(_Cmd):
    def __init__(self):
        super().__init__("pwnio", gdb.COMMAND_USER)

    def run(self, args):
        for name in ("_IO_2_1_stdout_", "_IO_2_1_stdin_", "_IO_2_1_stderr_", "_IO_list_all"):
            sym = _eval("&" + name)
            print("%-18s %s" % (name, ("%#x" % int(sym)) if sym is not None else "(无符号)"))
        print("[gdb_pwn] 无符号时：libc 基址 + 已知偏移（2.31 stdout=0x1ed6a0 / list_all=0x1f5720，")
        print("          2.35 stdout=0x21a780 / list_all=0x21a680，按实际 libc 版本核对）")


class PwnBins(_Cmd):
    def __init__(self):
        super().__init__("pwnbins", gdb.COMMAND_USER)

    def run(self, args):
        ma = _eval("&main_arena")
        if ma is None:
            print("[gdb_pwn] 没有 libc 调试符号（strip 的 libc）：")
            print("  退化方案：pwnchunks 手动走链表；或按 libc 版本硬编码 main_arena 偏移后 x/gx 基址+偏移")
            return
        base = int(ma)
        print("main_arena @ %#x" % base)
        for i in range(10):
            b = base + 0x10 + i * 8
            try:
                print("fastbin[%d] @ %#x -> %#x" % (i, b, _ptr(b)))
            except gdb.error:
                break


for _cls in (PwnRecon, PwnMaps, PwnOff, PwnStack, PwnChunks, PwnIo, PwnBins):
    _cls()
print("[gdb_pwn] 命令就绪：pwnrecon / pwnmaps / pwnoff / pwnstack / pwnchunks / pwnio / pwnbins")