# -*- coding: utf-8 -*-
"""[rev-toolbox 2026-10-30] rev 方向工具箱 v2 回归（独立可跑，未挂入 tests/smoke.py 注册表）。

覆盖：elfpeek 新增 disasm / bytecode / gadgets v2 / strings utf16 视图 + 派发与退化路径。
夹具全部在测试内构造（最小 ELF64 / .pyc / 伪 PyInstaller 包），跨平台、不执行任何二进制。
capstone 存在时（镜像内/CI）走真实引擎断言；缺失时断言退化路径不报错。

跑法：
  $env:PYTHONIOENCODING="utf-8"; python tests\\smoke_rev.py
"""
import importlib.util
import marshal
import shutil
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctfy_agent.tools_domain_ext import (  # noqa: E402
    DomainToolError, _PYI_COOKIE, call_domain, elfpeek,
)

try:
    import capstone  # noqa: F401
    HAS_CAPSTONE = True
except Exception:
    HAS_CAPSTONE = False

# [rev-toolbox] 夹具放工作区内（沙盒策略禁写系统临时目录）
TMP = Path(__file__).resolve().parents[1] / "runs" / "_tmp_rev_smoke"
shutil.rmtree(TMP, ignore_errors=True)
TMP.mkdir(parents=True, exist_ok=True)
CASES = []


def case(fn):
    CASES.append(fn)
    return fn


# ---------------- 夹具构造 ----------------

def build_elf64(seg_off: int = 0) -> Path:
    """最小 ELF64：1 个可执行 PT_LOAD，text 在文件偏移 0x78。
    seg_off=0 时 PT_LOAD 覆盖全文件；seg_off=0x78 时只覆盖 text（用于测吸附）。"""
    text = (
        b"\x55\x48\x89\xe5"                     # push rbp; mov rbp,rsp
        b"\x5f\xc3"                             # pop rdi; ret
        b"\x0f\x05\xc3"                         # syscall; ret
        b"/bin/sh\x00"
        b"FLAG{ELF_FIXTURE}\x00"
        b"correct password!\x00"
    )
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\x00" * 8,
        2, 62, 1, 0x400078, 64, 0, 0,
        64, 56, 1, 0, 0, 0,
    )
    assert len(ehdr) == 64
    data = ehdr + b"\x00" * 56 + text
    base = 0x400000 + seg_off  # 保证 offset 0x78 ↔ vaddr 0x400078（= e_entry）
    phdr = struct.pack("<IIQQQQQQ", 1, 5, seg_off, base, base,
                       len(data) - seg_off, len(data) - seg_off, 0x1000)
    assert len(phdr) == 56
    data = ehdr + phdr + text
    p = TMP / ("tiny.elf" if seg_off == 0 else "tiny_snap.elf")
    p.write_bytes(data)
    return p


def build_utf16() -> Path:
    data = b"RT" + "flag{wide_8848}".encode("utf-16-le") + b"\x00\x00" + b"plain-ascii-not-wide"
    p = TMP / "wide.bin"
    p.write_bytes(data)
    return p


def build_pyc() -> Path:
    code = compile("FLAG_REV = 'REVTOOL_8848'\ndef chk(s):\n    return s == 'REVTOOL_8848'\n", "t", "exec")
    data = importlib.util.MAGIC_NUMBER + b"\x00" * 12 + marshal.dumps(code)
    p = TMP / "mod.pyc"
    p.write_bytes(data)
    return p


def build_pyz(mods) -> bytes:
    body = b"PYZ\x00" + importlib.util.MAGIC_NUMBER
    toc = {}
    off = len(body)
    for name, code in mods:
        zdata = zlib.compress(marshal.dumps(code))
        body += struct.pack("!I", len(zdata)) + zdata
        toc[name] = (0, off)
        off += 4 + len(zdata)
    toc_pos = len(body)
    body += marshal.dumps(toc)
    body += struct.pack("!I", toc_pos)
    return body


def build_pyi(path: Path, entries) -> Path:
    """entries: [(name, typ_bytes, payload, cmprs_bool)]；按 PyInstaller v2.1+ cookie 格式拼包。"""
    blobs, toc = b"", b""
    pos = 0
    for name, typ, payload, cmprs in entries:
        raw = zlib.compress(payload) if cmprs else payload
        nb = name.encode()
        toc += struct.pack("<IIIIBc", 18 + len(nb), pos, len(raw), len(payload), 1 if cmprs else 0, typ)
        toc += nb
        blobs += raw
        pos += len(raw)
    pkg = blobs + toc
    cookie = (_PYI_COOKIE + struct.pack("<IIII", len(pkg) + 88, len(blobs), len(toc), 312)
              + b"libpython3.12.so.1.0".ljust(64, b"\x00"))
    path.write_bytes(b"MZ\x90\x00" + b"\x00" * 8 + pkg + cookie)
    return path


# ---------------- 用例 ----------------

@case
def case_info():
    out = elfpeek(str(build_elf64()), view="info")
    for needle in ("machine=0x3e", "ELF 64 LE", "入口段", "可执行", "PIE=False"):
        assert needle in out, "info 缺 %r：\n%s" % (needle, out[:300])


@case
def case_strings_ascii():
    out = elfpeek(str(build_elf64()), view="strings")
    assert "/bin/sh" in out and "FLAG{ELF_FIXTURE}" in out, out[:300]


@case
def case_strings_utf16():
    out = elfpeek(str(build_utf16()), view="strings", utf16=True)
    assert "flag{wide_8848}" in out, out[:300]


@case
def case_symbols_empty_hint():
    out = elfpeek(str(build_elf64()), view="symbols")
    assert "符号表为空" in out or "strip" in out, out[:300]


@case
def case_gadgets():
    out = elfpeek(str(build_elf64()), view="gadgets")
    assert "pop rdi" in out.replace(" ; ", " "), out[:400]
    assert "0x" in out
    if HAS_CAPSTONE:
        assert "ret" in out  # 真实链条文本含 ret
    else:
        assert "退化" in out  # 退化路径必须有明示


@case
def case_disasm():
    out = elfpeek(str(build_elf64()), view="disasm", offset=0x78, length=32, count=16)
    if HAS_CAPSTONE:
        assert any(m in out for m in ("push", "mov", "pop", "syscall")), out[:300]
        assert "00400078" in out  # vaddr 首列
    else:
        assert "退化" in out and "0x78" in out.replace("0x078", "0x78") or "字节视图" in out, out[:300]


@case
def case_disasm_offset_snap():
    # 可执行段只覆盖 0x78..：offset=0 必须被吸附到 0x78，且两条引擎路径都带"吸附"说明
    out = elfpeek(str(build_elf64(seg_off=0x78)), view="disasm", offset=0, length=32)
    assert "吸附" in out and "0x78" in out, out[:300]


@case
def case_disasm_beyond_eof():
    try:
        elfpeek(str(build_elf64()), view="disasm", offset=10 ** 9)
    except DomainToolError:
        return
    raise AssertionError("超界 offset 应抛 DomainToolError")


@case
def case_pyc_consts_and_func():
    out = elfpeek(str(build_pyc()), view="bytecode")
    assert "REVTOOL_8848" in out and "chk" in out, out[:400]
    out2 = elfpeek(str(build_pyc()), view="bytecode", func="chk")
    assert "REVTOOL_8848" in out2 and "return" in out2 or "COMPARE" in out2, out2[:400]


@case
def case_pyi_list_and_extract():
    code = compile("SECRET = 'PYITEST_77'\n", "m", "exec")
    p = build_pyi(TMP / "app.exe", [("fakemod", b"m", marshal.dumps(code), True)])
    listing = elfpeek(str(p), view="bytecode")
    assert "fakemod" in listing and "py模块" in listing, listing[:400]
    got = elfpeek(str(p), view="bytecode", extract="fakemod")
    assert "PYITEST_77" in got, got[:400]


@case
def case_pyz_extract():
    code = compile("SECRET = 'PYZSEC_42'\n", "m", "exec")
    pyz = build_pyz([("mymod", code)])
    p = build_pyi(TMP / "app2.exe", [("out00-PYZ.pyz", b"z", pyz, False)])
    got = elfpeek(str(p), view="bytecode", extract="pyz:mymod")
    assert "PYZSEC_42" in got, got[:400]


@case
def case_pyi_no_cookie_error():
    p = TMP / "plain_pe.bin"
    p.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    try:
        elfpeek(str(p), view="bytecode")
    except DomainToolError:
        return
    raise AssertionError("无 cookie 的 PE 应报 DomainToolError")


@case
def case_dispatch_new_args():
    p = build_elf64()
    out = call_domain("elfpeek", {"path": str(p), "view": "disasm", "offset": "120", "length": "64", "count": "20"})
    assert out and "0x" in out, out[:200]
    out2 = call_domain("elfpeek", {"path": str(p), "view": "gadgets", "filter": "rdi|binsh"})
    assert "pop rdi" in out2.replace(" ; ", " ") and "/bin/sh" in out2, out2[:300]


@case
def case_dispatch_info_regression():
    p = build_elf64()
    direct = elfpeek(str(p), view="info")
    via = call_domain("elfpeek", {"path": str(p)})
    assert direct == via, "派发结果应与直调一致"


@case
def case_bad_view_error():
    try:
        call_domain("elfpeek", {"path": str(build_elf64()), "view": "nonsense"})
    except Exception:
        return
    raise AssertionError("未知 view 应抛错")


# ---------------- runner ----------------

def main() -> int:
    print("capstone 引擎：%s" % ("可用（真实引擎断言）" if HAS_CAPSTONE else "不可用（退化路径断言）"))
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
