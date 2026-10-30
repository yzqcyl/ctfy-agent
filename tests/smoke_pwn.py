# -*- coding: utf-8 -*-
"""[pwn-toolbox 2026-10-30] pwn 方向工具箱 v1 回归（独立可跑，未挂入 tests/smoke.py 注册表）。

覆盖：pwngen（cyclic/build/落盘）、pwnfind（文本/hex/字节序/错误路径）、
elfpeek view=lookup（符号/相近/字符串/libc 版本）、DOMAIN_SPECS 自动注册与派发。
夹具全部在测试内构造（最小 ELF64 含 SHT_SYMTAB），跨平台、不执行任何二进制。

跑法：
  $env:PYTHONIOENCODING="utf-8"; python tests\\smoke_pwn.py
"""
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctfy_agent.tools_domain_ext import (  # noqa: E402
    DOMAIN_NAMES, DomainToolError, call_domain, elfpeek, pwnfind, pwngen,
)
from ctfy_agent.tools_local import ToolError  # [toolbox-fix 2026-10-30] call_domain 边界转译后的异常类

# [pwn-toolbox] 夹具放工作区内（沙盒策略禁写系统临时目录）
TMP = Path(__file__).resolve().parents[1] / "runs" / "_tmp_pwn_smoke"
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------- 夹具：带符号表的最小 ELF64 ----------------

def build_elf_sym() -> Path:
    """.text（pop rdi;ret + syscall 等）+ /bin/sh + libc 版本行 + .symtab(system/main)。"""
    code = b"\x55\x48\x89\xe5\x5f\xc3\x0f\x05\xc3"
    data = code + b"/bin/sh\x00" + b"GNU C Library stable release 2.35\n\x00"

    shstr = b"\x00.shstrtab\x00.symtab\x00.strtab\x00"
    o_shstrtab = 1
    o_symtab = shstr.find(b".symtab")
    o_strtab = shstr.find(b".strtab")
    strtab = b"\x00system\x00main\x00"
    o_system = strtab.find(b"system")
    o_main = strtab.find(b"main")

    ehdr_size, phdr_size, shdr_size = 64, 56, 64
    text_off = ehdr_size + phdr_size
    shstr_off = text_off + len(data)
    strtab_off = shstr_off + len(shstr)
    symtab_off = strtab_off + len(strtab)
    shoff = symtab_off + 3 * 24
    total = shoff + 4 * shdr_size

    def va(off):
        return 0x400000 + off

    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8,
        2, 62, 1, va(text_off), ehdr_size, shoff, 0,
        ehdr_size, phdr_size, 1, shdr_size, 4, 1,
    )
    phdr = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x400000, 0x400000, total, total, 0x1000)

    symtab = struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0)
    symtab += struct.pack("<IBBHQQ", o_system, 0x12, 0, 1, va(text_off), 16)
    symtab += struct.pack("<IBBHQQ", o_main, 0x12, 0, 1, va(text_off + 4), 8)
    assert len(symtab) == 72

    def shdr(name, typ, flags, addr, off, size, link=0, info=0, align=1, entsize=0):
        return struct.pack("<IIQQQQIIQQ", name, typ, flags, addr, off, size, link, info, align, entsize)

    shdrs = (
        b"\x00" * shdr_size
        + shdr(o_shstrtab, 3, 0, 0, shstr_off, len(shstr))
        + shdr(o_symtab, 2, 0, 0, symtab_off, len(symtab), link=3, info=1, align=8, entsize=24)
        + shdr(o_strtab, 3, 0, 0, strtab_off, len(strtab))
    )
    blob = ehdr + phdr + data + shstr + strtab + symtab + shdrs
    assert len(blob) == total
    p = TMP / "sym.elf"
    p.write_bytes(blob)
    return p


def cyclic_payload(size: int, n: int = 4, alphabet: str = "") -> str:
    """从 pwngen 输出里取 ascii 模式串本体。"""
    out = pwngen(op="cyclic", size=size, n=n, alphabet=alphabet)
    return out.split("直接发文本）：", 1)[1].splitlines()[0].strip()


# ---------------- 用例 ----------------

@case
def case_cyclic_deterministic():
    seq = cyclic_payload(24)
    assert seq.startswith("aaaabaaacaaadaaae"), seq[:40]
    assert len(seq) == 24


@case
def case_pwnfind_roundtrip():
    seq = cyclic_payload(300)
    for off in (0, 4, 8, 17, 63, 128, 255):
        out = pwnfind(value=seq[off:off + 4])
        assert ("偏移 %d" % off) in out, "offset %d → %s" % (off, out[:120])


@case
def case_pwnfind_hex_input():
    seq = cyclic_payload(64)
    off = 20
    h = seq[off:off + 4].encode("latin-1").hex()
    out = pwnfind(hex=h)
    assert ("偏移 %d" % off) in out, out[:120]


@case
def case_pwnfind_long_needle():
    # 5 字节连续子串必然存在于序列中（>n 的子串并非全部存在，所以这是有用的正例）
    seq = cyclic_payload(64)
    off = 40
    out = pwnfind(value=seq[off:off + 5])
    assert ("偏移 %d" % off) in out, out[:120]


@case
def case_pwnfind_unmatched_hint():
    # 非字母表字节 → 查不到，报错必须带"倒序/换入口"的排查提示
    try:
        pwnfind(value="a1b2")
    except DomainToolError as exc:
        assert "倒序" in str(exc) and "换长度" in str(exc), str(exc)
        return
    raise AssertionError("非字母表字节应报错并给排查提示")


@case
def case_pwnfind_errors():
    for kwargs in ({"value": ""}, {"value": "ab"}, {"hex": "zz"}):
        try:
            pwnfind(**kwargs)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (kwargs,))


@case
def case_cyclic_n_guard():
    try:
        pwngen(op="cyclic", size=32, n=6)
    except DomainToolError as exc:
        assert "n=4" in str(exc) or "太大" in str(exc), str(exc)
        return
    raise AssertionError("n=6（26^6）应被规模守卫拒绝")


@case
def case_build_composition():
    out = pwngen(op="build", parts=[
        {"cyclic": 8}, {"pad": "B", "count": 4}, {"int": 0x401000, "width": 8}, {"hex": "deadbeef"},
    ])
    assert "共 24 字节" in out, out[:200]
    hexline = [ln for ln in out.splitlines() if ln.startswith("hex=")][0][4:]
    assert len(hexline) == 48
    assert hexline.endswith("deadbeef"), hexline
    assert "0010400000000000" in hexline, hexline  # p64(0x401000) 小端
    assert hexline[16:24] == "42424242", hexline  # pad 'B'×4（cyclic 8 字节后）


@case
def case_build_write_file():
    p = TMP / "payload.bin"
    out = pwngen(op="build", parts=[{"pad": "A", "count": 16}, {"int": 0xdead, "width": 2}], path=str(p))
    assert "已落盘" in out, out[:160]
    blob = p.read_bytes()
    assert len(blob) == 18 and blob[:16] == b"A" * 16 and blob[16:] == b"\xad\xde", blob


@case
def case_build_errors():
    for parts in ([{"nope": 1}], [{"pad": "A", "count": 2}, {"int": 5, "width": 3}], [{"hex": "xyz"}]):
        try:
            pwngen(op="build", parts=parts)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (parts,))
    try:
        pwngen(op="bogus")
    except DomainToolError:
        return
    raise AssertionError("未知 op 应报错")


@case
def case_lookup_symbol():
    p = build_elf_sym()
    out = elfpeek(str(p), view="lookup", symbol="system")
    assert "符号 system" in out and "0x400078" in out, out[:300]
    assert "文件偏移 0x78" in out, out[:300]


@case
def case_lookup_near_miss():
    p = build_elf_sym()
    out = elfpeek(str(p), view="lookup", symbol="sys")
    assert "未命中" in out and "system" in out, out[:300]


@case
def case_lookup_search_and_version():
    p = build_elf_sym()
    out = elfpeek(str(p), view="lookup", search="/bin/sh")
    assert "字符串 '/bin/sh'" in out and "文件0x" in out, out[:300]
    assert "2.35" in out, out[:300]  # libc 版本行自动识别


@case
def case_lookup_needs_param():
    out = elfpeek(str(build_elf_sym()), view="lookup")
    assert "需要 symbol=" in out, out[:200]


@case
def case_registry_auto_wiring():
    """契约：新工具经 DOMAIN_SPECS 自动进 DOMAIN_NAMES（LocalTools.call 的路由依据）。"""
    assert {"pwngen", "pwnfind"} <= DOMAIN_NAMES, sorted(DOMAIN_NAMES)
    from ctfy_agent.tools_misc_ext import MISC_EXT_SPECS, NAMES

    names = {s["function"]["name"] for s in MISC_EXT_SPECS}
    assert {"pwngen", "pwnfind"} <= names, "MISC_EXT_SPECS 未聚合到新工具"
    assert {"pwngen", "pwnfind"} <= NAMES


@case
def case_dispatch_pwngen_pwnfind():
    out = call_domain("pwngen", {"op": "cyclic", "size": 32})
    assert "aaaabaaa" in out, out[:200]
    seq = out.split("直接发文本）：", 1)[1].splitlines()[0].strip()
    hit = call_domain("pwnfind", {"value": seq[12:16]})
    assert "偏移 12" in hit, hit[:160]


def build_elf32_sym() -> Path:
    """[toolbox-fix 2026-10-30] 32 位最小 ELF + .symtab：审计补 i386 lookup 覆盖。"""
    code = b"\x55\x5d\xc3\x0f\x05\xc3"
    data = code + b"/bin/sh\x00"

    shstr = b"\x00.shstrtab\x00.symtab\x00.strtab\x00"
    strtab = b"\x00system\x00main\x00"
    o_shstrtab = 1
    o_symtab = shstr.find(b".symtab")
    o_strtab = shstr.find(b".strtab")
    o_system = strtab.find(b"system")
    o_main = strtab.find(b"main")

    ehdr_size, phdr_size, shdr_size, sym_size = 52, 32, 40, 16
    text_off = ehdr_size + phdr_size
    shstr_off = text_off + len(data)
    strtab_off = shstr_off + len(shstr)
    symtab_off = strtab_off + len(strtab)
    shoff = symtab_off + 3 * sym_size
    total = shoff + 4 * shdr_size

    def va(off):
        return 0x400000 + off

    ehdr = struct.pack(
        "<16sHHIIIIIHHHHHH",
        b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8,  # ELFCLASS32, LSB
        2, 3, 1, va(text_off), ehdr_size, shoff, 0,
        ehdr_size, phdr_size, 1, shdr_size, 4, 1,
    )
    phdr = struct.pack("<IIIIIIII", 1, 0, 0x400000, 0x400000, total, total, 5, 0x1000)

    symtab = struct.pack("<IIIBBH", 0, 0, 0, 0, 0, 0)
    symtab += struct.pack("<IIIBBH", o_system, va(text_off), 16, 0x12, 0, 1)
    symtab += struct.pack("<IIIBBH", o_main, va(text_off + 3), 8, 0x12, 0, 1)

    def shdr(name, typ, offset, size, link=0, entsize=0):
        return struct.pack("<IIIIIIIIII", name, typ, 0, 0, offset, size, link, 0, 1, entsize)

    shdrs = (
        b"\x00" * shdr_size
        + shdr(o_shstrtab, 3, shstr_off, len(shstr))
        + shdr(o_symtab, 2, symtab_off, len(symtab), link=3, entsize=sym_size)
        + shdr(o_strtab, 3, strtab_off, len(strtab))
    )
    blob = ehdr + phdr + data + shstr + strtab + symtab + shdrs
    assert len(blob) == total, (len(blob), total)
    p = TMP / "sym32.elf"
    p.write_bytes(blob)
    return p


# ---------------- [toolbox-fix 2026-10-30] 审计回归 ----------------

@case
def case_fix_lookup_elf32():
    """[toolbox-fix 2026-10-30] i386 目标的 lookup（_phdrs/_Elf.symbols 的 32 位分支）。"""
    p = build_elf32_sym()
    out = elfpeek(str(p), view="lookup", symbol="system")
    # 32 位布局：ehdr 52 + phdr 32 = 84 = 0x54
    assert "符号 system" in out and "0x400054" in out and "文件偏移 0x54" in out, out[:300]
    out = elfpeek(str(p), view="lookup", search="/bin/sh")
    assert "字符串 '/bin/sh'" in out and "文件0x" in out, out[:300]


@case
def case_fix_cyclic_alphabet_cache():
    """B1：_CYC_CACHE 键曾忽略 alphabet → 同长度不同字母表串号、pwnfind 查错偏移。"""
    a1 = cyclic_payload(64, n=4, alphabet="ABCD")
    a2 = cyclic_payload(64, n=4, alphabet="WXYZ")
    assert a1[:4] != a2[:4], (a1[:8], a2[:8])
    for seq, alph in ((a1, "ABCD"), (a2, "WXYZ")):
        out = pwnfind(value=seq[9:13], n=4, alphabet=alph)
        assert "偏移 9" in out, out[:120]


@case
def case_fix_pwnfind_n3_consistency():
    """B2：pwngen n=3 时 pwnfind 曾强制 max(n,4) → 永远查不到。"""
    seq = cyclic_payload(200, n=3)
    out = pwnfind(value=seq[33:36], n=3)
    assert "偏移 33" in out, out[:120]


@case
def case_fix_cyclic_n_guard():
    try:
        pwngen(op="cyclic", size=32, n=2)
    except DomainToolError as exc:
        assert "n 需 ≥3" in str(exc), str(exc)
        return
    raise AssertionError("n=2（针长无唯一性）应被拒绝")


@case
def case_fix_latin1_guard():
    """B3：多字节字符曾让 encode("latin-1") 裸崩；现在要么安全降级要么报 ToolError。"""
    # alphabet 全为多字节 → 过滤后回退默认字母表，不崩
    out = pwngen(op="cyclic", size=16, alphabet="甲乙丙丁")
    assert "aaaabaaa" in out, out[:120]
    # value 多字节 → 明确报错并提示用 hex
    try:
        pwnfind(value="中文字节")
    except DomainToolError as exc:
        assert "hex" in str(exc) or "多字节" in str(exc), str(exc)
        return
    raise AssertionError("多字节 value 应报 DomainToolError")


@case
def case_fix_parts_bad_values():
    """B5：parts 里的非整数 count/cyclic/width 曾裸崩。"""
    for parts in ([{"pad": "A", "count": "lots"}], [{"cyclic": "many"}], [{"int": 1, "width": "wide"}]):
        try:
            pwngen(op="build", parts=parts)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (parts,))


@case
def case_fix_dispatch_bad_int():
    """B6：派发层垃圾整数参数曾 ValueError 裸崩。"""
    for kwargs in ({"op": "cyclic", "size": "big"}, {"op": "cyclic", "n": "huh"}):
        try:
            call_domain("pwngen", kwargs)
        except (DomainToolError, ToolError):  # [toolbox-fix] call_domain 边界会把 DomainToolError 转 ToolError
            continue
        raise AssertionError("应报错：%r" % (kwargs,))
    # 顺带回归：hex 字符串整数现在可用（审计加成，_to_int 走 _int_of）
    from ctfy_agent.tools_domain_ext import _to_int

    assert _to_int("0x40", 0, "x") == 64
    assert _to_int(None, 7, "x") == 7 and _to_int("", 7, "x") == 7


@case
def case_fix_pwngen_localtools_sandbox():
    """安全边界：pwngen 的 path 写入必须经 _resolve_path 锚定沙盒，越界拒绝。"""
    from ctfy_agent.tools_local import LocalTools, ToolError

    lt = LocalTools(TMP, "SANDBOXTEST", traffic_enabled=False)
    out = lt.call("pwngen", {"op": "build", "parts": [{"hex": "4142"}], "path": "sub/x.bin"})
    # 约定：store 传 Path 时该目录即沙盒根（_sandbox_dir 的既有语义，生产环境走 run_dir/sandbox/<id>）
    f = TMP / "sub" / "x.bin"
    assert f.exists() and f.read_bytes() == b"\x41\x42", out[:200]
    try:
        lt.call("pwngen", {"op": "build", "parts": [{"hex": "4142"}], "path": "../esc.bin"})
    except ToolError:
        return
    raise AssertionError("路径越界（../esc.bin）应被拒绝")


# ---------------- runner ----------------

def main() -> int:
    failed = 0
    for fn in CASES:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as exc:
            failed += 1
            print("FAIL %s：%s" % (fn.__name__, exc))
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("FAIL %s：意外异常 %s: %s" % (fn.__name__, type(exc).__name__, exc))
    print("汇总：%d/%d 通过" % (len(CASES) - failed, len(CASES)))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())