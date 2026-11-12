#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_libs_linux_probe.py — 在 Linux（WSL/镜像）里验证外挂库工具的**真功能**。

为什么单独一个文件：Windows Smart App Control 会拦截未签名 `.pyd/.dll`（WinError 4551），
本机的 pycryptodome AES-ECB/GCM/CTR/3DES、z3 的 libz3.dll 都会被拦，只能跳过。
而正式镜像/线上是 Linux，这些库是正常可用的——所以功能正确性在 Linux 侧验证：

    wsl -d Ubuntu-24.04 -u root -- env \
        PYTHONPATH=/mnt/e/ctfy_project/src \
        CTFY_PYLIBS=/root/ctfy-libs/lib/python3.12/site-packages \
        CTFY_PYLIBS_STRICT=1 \
        /root/ctfy-libs/bin/python /mnt/e/ctfy_project/tests/_libs_linux_probe.py

依赖：该 venv 里装 z3-solver / pycryptodome / pwntools（见 runs/_mon/wsl_setup_libs.sh），
以及 gcc（pwnrop 的 ELF 夹具用）。文件名故意不以 smoke 开头，不会被 run_all_tests.py 自动收集。
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import pylibs  # noqa: E402
from ctfy_agent.tools_domain_ext import DOMAIN_NAMES, DomainToolError, call_domain  # noqa: E402
from ctfy_agent.tools_local import ToolError  # noqa: E402

FAILS: list[str] = []
SKIPPED: list[str] = []
PASSED = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if cond:
        PASSED += 1
    else:
        FAILS.append(name)


def skip(name: str, why: str) -> None:
    print(f"[skip] {name}  {why}")
    SKIPPED.append(name)


def run_tool(name: str, **kwargs) -> str:
    return call_domain(name, kwargs)


def expect_error(fn, needles: tuple, name: str) -> str:
    try:
        fn()
    except (DomainToolError, ToolError) as exc:
        text = str(exc)
        check(name, any(n in text for n in needles), text[:200])
        return text
    check(name, False, "没有抛错")
    return ""


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ctfy-libs-probe-"))
    print("库摘要：", pylibs.summary())

    # ---------------- z3 ----------------
    if pylibs.usable("z3"):
        out = run_tool("z3solve", smt2="(declare-const x Int)(assert (= (+ x 3) 10))")
        check("z3solve sat + 解值", "sat" in out and "x = 7" in out, out[:160])
        out = run_tool("z3solve", smt2="(declare-const x Int)(assert (> x 10))(assert (< x 5))")
        check("z3solve unsat", "unsat" in out, out[:160])
        out = run_tool("z3solve", smt2="(declare-const x Int)(assert (> x 0))(assert (< x 3))",
                       count=3, vars="x")
        check("z3solve 多解枚举", out.count("解 #") >= 2 and "1" in out and "2" in out, out[:220])
        smt = ('(declare-const a (_ BitVec 8))(declare-const b (_ BitVec 8))'
               '(assert (= a #x46))(assert (= b #x4c))')
        out = run_tool("z3solve", smt2=smt, vars="a,b")
        check("z3solve BitVec", "sat" in out and "0x46" in out and "0x4c" in out, out[:200])
        expect_error(lambda: run_tool("z3solve", smt2="(declare-const x Int"),
                     ("解析失败",), "z3solve 坏 SMT-LIB2 有可读报错")
    else:
        skip("z3solve 功能组", "z3 不可用")

    # ---------------- pycryptodome ----------------
    key_hex = "00112233445566778899aabbccddeeff"
    iv_hex = "0f0e0d0c0b0a09080706050403020100"
    data_hex = "4142434445464748494a4b4c4d4e4f50"
    if pylibs.usable("Crypto.Cipher.AES"):
        enc = run_tool("pycrypt", op="aes_encrypt", mode="cbc", key=key_hex, data=data_hex, iv=iv_hex)
        ct = re.search(r"hex=([0-9a-f]+)", enc)
        check("pycrypt AES-CBC 加密", bool(ct), enc[:160])
        if ct:
            dec = run_tool("pycrypt", op="aes_decrypt", mode="cbc", key=key_hex,
                           data=ct.group(1), iv=iv_hex)
            check("pycrypt AES-CBC 往返", data_hex in dec.lower(), dec[:200])
            padded = run_tool("aesblock", op="pkcs7_pad", data=data_hex)
            padded_hex = re.search(r"hex=([0-9a-f]+)", padded)
            pure = run_tool("aesblock", op="cbc_encrypt", key=key_hex,
                            data=padded_hex.group(1) if padded_hex else data_hex, iv=iv_hex)
            pure_hex = re.search(r"hex=([0-9a-f]+)", pure)
            check("pycrypt 与纯 stdlib aesblock 逐字节一致",
                  bool(pure_hex) and pure_hex.group(1) == ct.group(1),
                  f"pure={pure_hex and pure_hex.group(1)} libs={ct.group(1)}")
        for stream_mode in ("cfb", "ofb", "ctr"):
            enc = run_tool("pycrypt", op="aes_encrypt", mode=stream_mode, key=key_hex,
                           data=data_hex, iv=iv_hex)
            sm = re.search(r"hex=([0-9a-f]+)", enc)
            if not sm:
                check(f"pycrypt AES-{stream_mode.upper()} 往返", False, enc[:160])
                continue
            dec = run_tool("pycrypt", op="aes_decrypt", mode=stream_mode, key=key_hex,
                           data=sm.group(1), iv=iv_hex)
            check(f"pycrypt AES-{stream_mode.upper()} 往返", data_hex in dec.lower(), dec[:160])
        gcm = run_tool("pycrypt", op="aes_encrypt", mode="gcm", key=key_hex, data=data_hex, aad="aabb")
        nonce = re.search(r"nonce=([0-9a-f]+)", gcm)
        tag = re.search(r"tag=([0-9a-f]+)", gcm)
        gct = re.search(r"hex=([0-9a-f]+)", gcm)
        check("pycrypt AES-GCM 加密（nonce+tag）", bool(nonce and tag and gct), gcm[:200])
        if nonce and tag and gct:
            gdec = run_tool("pycrypt", op="aes_decrypt", mode="gcm", key=key_hex,
                            data=gct.group(1), nonce=nonce.group(1), aad="aabb", tag=tag.group(1))
            check("pycrypt AES-GCM 认证解密", "认证通过" in gdec and data_hex in gdec.lower(), gdec[:200])
            expect_error(lambda: run_tool("pycrypt", op="aes_decrypt", mode="gcm", key=key_hex,
                                          data=gct.group(1), nonce=nonce.group(1), aad="aabb",
                                          tag="00" * 16),
                         ("认证失败",), "pycrypt GCM 错 tag 被拒")
        des3 = run_tool("pycrypt", op="des3_encrypt", mode="cbc",
                        key="0123456789abcdeffedcba9876543210", data=data_hex, iv="0011223344556677")
        check("pycrypt 3DES 加密", "3DES" in des3 and "hex=" in des3, des3[:160])
    else:
        skip("pycrypt 功能组", "pycryptodome 不可用")

    # ---------------- pwntools ----------------
    if pylibs.usable("pwn"):
        src = tmp / "probe.c"
        src.write_text('#include <stdlib.h>\nint main(void){ return system("/bin/sh") & 0; }\n')
        elf = tmp / "probe_nopie"
        proc = subprocess.run(["gcc", "-o", str(elf), "-fno-stack-protector", "-no-pie", "-O0", str(src)],
                              capture_output=True, text=True)
        check("gcc 编译 ELF 夹具", proc.returncode == 0 and elf.exists(),
              (proc.stderr or "")[-200:])
        if elf.exists():
            info = run_tool("pwnrop", path=str(elf), op="checksec")
            check("pwnrop checksec", "NX" in info and "PIE" in info, info[:200])
            syms = run_tool("pwnrop", path=str(elf), op="symbols")
            check("pwnrop symbols（plt/got）", "plt" in syms and "got" in syms, syms[:200])
            chain = run_tool("pwnrop", path=str(elf), op="rop", func="system", arg_strings="/bin/sh")
            heap = re.search(r"hex=([0-9a-f]+)", chain)
            check("pwnrop 自动拼 system('/bin/sh') 链",
                  bool(heap) and len(heap.group(1)) > 8, chain[:240])
            gadgets = run_tool("pwnrop", path=str(elf), op="gadgets", filt="pop rdi", limit=5)
            check("pwnrop gadget 搜索", "pop rdi" in gadgets, gadgets[:200])
    else:
        skip("pwnrop 功能组", "pwntools 不可用")

    print()
    if FAILS:
        print(f"通过 {PASSED}，跳过 {len(SKIPPED)}，失败 {len(FAILS)}：{FAILS}")
        return 1
    print(f"_libs_linux_probe 全部通过（{PASSED} 项，跳过 {len(SKIPPED)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())