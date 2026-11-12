#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_libs.py — 外挂库接入（tools/pylibs）与三个库工具回归。

背景（2026-11-12 缺口：CRYPTO/PWN 长期解不动）：镜像只带 capstone，pycryptodome /
sympy / gmpy2 / z3 / pwntools 由 `tools/fetch_pylibs.py` 装到 `tools/pylibs`（git 忽略、
不进镜像），此前只有 shell() 子进程能看到。本回归覆盖：
  A. 进程内接入：路径发现/幂等/env 覆盖/懒加载/缺库提示
  B. 工具接线：z3solve / pycrypt / pwnrop 进 DOMAIN_SPECS 与角色 local 名单
  C. z3solve：sat / unsat / 多解枚举 / vars 过滤（无 z3 → 断言「怎么装」提示）
  D. pycrypt：AES-CBC 往返且与纯 stdlib aesblock 逐字节一致、GCM tag 认证、3DES
  E. pwnrop：checksec / plt-got / gadgets / 自动拼 ROP 链（需要 pwntools + 一个真 ELF；
     没有 WSL/gcc 时这组跳过）

运行: python tests/smoke_libs.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import pylibs  # noqa: E402
from ctfy_agent.tools_domain_ext import DOMAIN_NAMES, DomainToolError, call_domain  # noqa: E402
from ctfy_agent.tools_local import ToolError  # noqa: E402

FAILS: list[str] = []
PASSED = 0
SKIPPED: list[str] = []


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


def _elf_fixture(tmp: pathlib.Path):
    """用 WSL gcc 编一个动了 system 的 -no-pie ELF；编不出来返回 None（测试跳过）。"""
    out = tmp / "fixture_nopie"
    src = tmp / "fixture.c"
    src.write_text(
        '#include <stdlib.h>\nint main(void){ return system("/bin/sh") & 0; }\n',
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "root", "--", "bash", "-lc",
             f"gcc -o '{_wsl_path(out)}' -fno-stack-protector -no-pie -O0 '{_wsl_path(src)}' 2>&1"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"      WSL/gcc 不可用：{exc}")
        return None
    if proc.returncode != 0 or not out.exists():
        print(f"      gcc 失败：{(proc.stdout or '')[-200:]}{(proc.stderr or '')[-200:]}")
        return None
    return out


def _expect_error(fn, needles, name: str) -> str:
    """断言调用抛「可回显的工具错误」且文案含可操作线索。"""
    try:
        fn()
    except (DomainToolError, ToolError) as exc:
        text = str(exc)
        check(name, any(n in text for n in needles), text[:200])
        return text
    check(name, False, "没有抛错")
    return ""


def _probe_z3() -> bool:
    """真跑一次求解：只看 import 不够（Windows 上 libz3.dll 可能被应用控制策略拦截）。"""
    try:
        import z3  # noqa: PLC0415 - 探测点必须在 pylibs.ensure_on_path 之后

        solver = z3.Solver()
        solver.add(z3.Int("x") > 0)
        return solver.check() == z3.sat
    except Exception as exc:  # noqa: BLE001
        print(f"      z3 原生库不可用：{type(exc).__name__}: {str(exc)[:160]}")
        return False


def _wsl_path(path: pathlib.Path) -> str:
    """Windows 路径 → WSL 里能看到的 /mnt/<drive>/... 路径（gcc 夹具要用）。"""
    text = str(path)
    if len(text) > 2 and text[1] == ":":
        return "/mnt/" + text[0].lower() + text[2:].replace("\\", "/")
    return text.replace("\\", "/")


def _crypto_probe(modes=("cbc",)) -> dict:
    """逐模式探测 pycryptodome 原生库在本机是否可用。

    Windows Smart App Control 可能只拦截部分 `.pyd`（实测 ECB/GCM 被拦、CBC/CFB/OFB 放行），
    所以不能用一个布尔值代表「pycryptodome 可用」；每种模式分别探测、分别跳过。
    """
    ok: dict = {}
    aes_mod = pylibs.import_or_none("Crypto.Cipher.AES")
    for mode in modes:
        try:
            if aes_mod is None:
                raise RuntimeError("Crypto.Cipher.AES 导入失败")
            if mode == "gcm":
                cipher = aes_mod.new(b"\x00" * 16, aes_mod.MODE_GCM, nonce=b"\x00" * 12)
                cipher.encrypt(b"\x00" * 16)
                cipher.digest()
            else:
                cipher = aes_mod.new(b"\x00" * 16, getattr(aes_mod, "MODE_" + mode.upper()),
                                     b"\x00" * 16)
                cipher.encrypt(b"\x00" * 16)
            ok[mode] = True
        except Exception as exc:  # noqa: BLE001
            ok[mode] = False
            print(f"      pycryptodome mode={mode} 不可用：{type(exc).__name__}: {str(exc)[:120]}")
    return ok


def _des3_available() -> bool:
    try:
        des3_mod = pylibs.import_or_none("Crypto.Cipher.DES3")
        if des3_mod is None:
            return False
        des3_mod.new(b"0123456789abcdef", des3_mod.MODE_ECB).encrypt(b"0011223344556677")
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"      pycryptodome 3DES 不可用：{type(exc).__name__}: {str(exc)[:120]}")
        return False


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_libs"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    # ---------------- A. 进程内接入 ----------------
    dirs = pylibs.candidate_dirs()
    check("默认候选含项目 tools/pylibs", (ROOT / "tools" / "pylibs") in dirs, str(dirs))
    check("候选目录去重", len(dirs) == len(set(map(str, dirs))), str(dirs))
    added = pylibs.ensure_on_path()
    check("ensure_on_path 返回已挂目录", bool(added) and str((ROOT / "tools" / "pylibs")) in added, str(added))
    before = list(sys.path)
    pylibs.ensure_on_path()
    check("ensure_on_path 幂等（不重复插入）", list(sys.path) == before)
    check("import_or_none 未知模块返回 None", pylibs.import_or_none("no_such_module_xyz") is None)

    summary = pylibs.summary()
    check("summary 可读（含路径或未启用）", isinstance(summary, str) and summary, summary)

    # ---------------- B. 工具接线 ----------------
    for tool in ("z3solve", "pycrypt", "pwnrop"):
        check(f"DOMAIN_NAMES 含 {tool}", tool in DOMAIN_NAMES)
    config = json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    roles = config.get("roles") or {}
    want = {
        "rev": {"z3solve"},
        "crypto": {"z3solve", "pycrypt"},
        "pwn": {"z3solve", "pwnrop"},
        "misc": {"z3solve"},
    }
    for role, tools in want.items():
        local = set((roles.get(role) or {}).get("local") or [])
        check(f"{role} 放行 {sorted(tools)}", tools <= local, f"缺 {sorted(tools - local)}")
    check("web 不泄漏 z3solve（避免无谓扩权）", "z3solve" not in ((roles.get("web") or {}).get("local") or []))

    # ---------------- C. z3solve ----------------
    if _probe_z3():
        out = run_tool("z3solve", smt2="(declare-const x Int)(assert (= (+ x 3) 10))")
        check("z3solve sat + 解值", "sat" in out and "x = 7" in out, out[:160])
        out = run_tool("z3solve", smt2="(declare-const x Int)(assert (> x 10))(assert (< x 5))")
        check("z3solve unsat", "unsat" in out, out[:160])
        out = run_tool("z3solve", smt2="(declare-const x Int)(assert (> x 0))(assert (< x 3))",
                       count=3, vars="x")
        check("z3solve 多解枚举", out.count("解 #") >= 2 and "1" in out and "2" in out, out[:220])
        # 字节约束（flag 校验器最常见）：两个字节分别是 F / L
        smt = ('(declare-const a (_ BitVec 8))(declare-const b (_ BitVec 8))'
               '(assert (= a #x46))(assert (= b #x4c))')
        out = run_tool("z3solve", smt2=smt, vars="a,b")
        check("z3solve BitVec（字节约束）", "sat" in out and "0x46" in out and "0x4c" in out, out[:200])
        _expect_error(lambda: run_tool("z3solve", smt2="(declare-const x Int"), ("解析失败",),
                      "z3solve 坏 SMT-LIB2 有可读报错")
    else:
        skip("z3solve 功能组", "z3 不可用（未安装 / 原生库被策略拦截）")
        _expect_error(
            lambda: run_tool("z3solve", smt2="(declare-const x Int)(assert (= x 1))"),
            ("加载失败", "fetch_pylibs"), "z3solve 不可用时提示可操作")

    # ---------------- D. pycrypt（逐模式探测：SAC 可能只拦部分原生模块） ----------------
    key_hex = "00112233445566778899aabbccddeeff"
    iv_hex = "0f0e0d0c0b0a09080706050403020100"
    data_hex = "4142434445464748494a4b4c4d4e4f50"
    modes_ok = _crypto_probe(("cbc", "cfb", "ofb", "ctr", "gcm"))
    if modes_ok.get("cbc"):
        enc = run_tool("pycrypt", op="aes_encrypt", mode="cbc", key=key_hex, data=data_hex, iv=iv_hex)
        m = re.search(r"hex=([0-9a-f]+)", enc)
        ct_hex = m.group(1) if m else ""
        check("pycrypt AES-CBC 加密出密文", bool(ct_hex) and len(ct_hex) % 32 == 0, enc[:160])
        dec = run_tool("pycrypt", op="aes_decrypt", mode="cbc", key=key_hex, data=ct_hex, iv=iv_hex)
        check("pycrypt AES-CBC 往返", data_hex in dec.lower(), dec[:200])
        # pycrypt 的 CBC 自动 PKCS7 填充，所以纯 stdlib 对照也要先补到同样长度再比
        padded = run_tool("aesblock", op="pkcs7_pad", data=data_hex)
        padded_hex = re.search(r"hex=([0-9a-f]+)", padded)
        pure = run_tool("aesblock", op="cbc_encrypt", key=key_hex,
                        data=padded_hex.group(1) if padded_hex else data_hex, iv=iv_hex)
        pure_hex = re.search(r"hex=([0-9a-f]+)", pure)
        check("pycrypt 与纯 stdlib aesblock 逐字节一致",
              bool(pure_hex) and pure_hex.group(1) == ct_hex,
              f"pure={pure_hex and pure_hex.group(1)} libs={ct_hex}")
        for stream_mode in ("cfb", "ofb", "ctr"):
            if not modes_ok.get(stream_mode):
                skip(f"pycrypt AES-{stream_mode.upper()} 往返", "该模式原生库被策略拦截")
                continue
            enc = run_tool("pycrypt", op="aes_encrypt", mode=stream_mode, key=key_hex,
                           data=data_hex, iv=iv_hex)
            sm = re.search(r"hex=([0-9a-f]+)", enc)
            if not sm:
                check(f"pycrypt AES-{stream_mode.upper()} 往返", False, enc[:160])
                continue
            dec = run_tool("pycrypt", op="aes_decrypt", mode=stream_mode, key=key_hex,
                           data=sm.group(1), iv=iv_hex)
            check(f"pycrypt AES-{stream_mode.upper()} 往返", data_hex in dec.lower(), dec[:200])
    else:
        skip("pycrypt AES-CBC 功能组", "CBC 原生库不可用（未安装 / 被策略拦截）")
        _expect_error(
            lambda: run_tool("pycrypt", op="aes_encrypt", mode="cbc", key=key_hex,
                             data=data_hex, iv=iv_hex),
            ("原生模块被系统策略拦截", "加载失败", "fetch_pylibs"), "pycrypt 不可用时提示可操作")

    if modes_ok.get("gcm"):
        gcm = run_tool("pycrypt", op="aes_encrypt", mode="gcm", key=key_hex, data=data_hex, aad="aabb")
        nonce = re.search(r"nonce=([0-9a-f]+)", gcm)
        tag = re.search(r"tag=([0-9a-f]+)", gcm)
        gct = re.search(r"hex=([0-9a-f]+)", gcm)
        check("pycrypt AES-GCM 加密（给 nonce+tag）", bool(nonce and tag and gct), gcm[:200])
        if nonce and tag and gct:
            gdec = run_tool("pycrypt", op="aes_decrypt", mode="gcm", key=key_hex,
                            data=gct.group(1), nonce=nonce.group(1), aad="aabb", tag=tag.group(1))
            check("pycrypt AES-GCM 认证解密", "认证通过" in gdec and data_hex in gdec.lower(), gdec[:200])
            _expect_error(
                lambda: run_tool("pycrypt", op="aes_decrypt", mode="gcm", key=key_hex,
                                 data=gct.group(1), nonce=nonce.group(1), aad="aabb",
                                 tag="00" * 16),
                ("认证失败",), "pycrypt GCM 错 tag 被拒")
    else:
        skip("pycrypt AES-GCM 组", "GCM 原生库不可用（Windows 上 GCM 依赖 _raw_ecb，常见被策略拦截）")
        _expect_error(
            lambda: run_tool("pycrypt", op="aes_encrypt", mode="gcm", key=key_hex, data=data_hex),
            ("原生模块被系统策略拦截", "加载失败", "fetch_pylibs"), "pycrypt GCM 不可用时提示可操作")

    if _des3_available():
        des3 = run_tool("pycrypt", op="des3_encrypt", mode="cbc",
                        key="0123456789abcdeffedcba9876543210", data=data_hex, iv="0011223344556677")
        check("pycrypt 3DES 加密", "3DES" in des3 and "hex=" in des3, des3[:160])
    else:
        skip("pycrypt 3DES", "3DES 原生库不可用")

    # ---------------- E. pwnrop ----------------
    if pylibs.usable("pwn"):
        _expect_error(lambda: run_tool("pwnrop", path=str(tmp / "missing.elf"), op="checksec"),
                      ("文件不存在",), "pwnrop 缺文件报错")
        not_elf = tmp / "not_elf.bin"
        not_elf.write_bytes(b"not an elf at all")
        _expect_error(lambda: run_tool("pwnrop", path=str(not_elf), op="checksec"),
                      ("ELF 解析失败",), "pwnrop 非 ELF 报错")
        fixture = _elf_fixture(tmp)
        if fixture is None:
            skip("pwnrop 正功能组", "没有可用的 ELF 编译环境（WSL gcc）")
        else:
            try:
                info = run_tool("pwnrop", path=str(fixture), op="checksec")
                check("pwnrop checksec", "NX" in info and "PIE" in info, info[:200])
                syms = run_tool("pwnrop", path=str(fixture), op="symbols")
                check("pwnrop symbols 给出 plt/got", "plt" in syms and "got" in syms, syms[:200])
            except (DomainToolError, ToolError) as exc:
                check("pwnrop ELF 解析（checksec/symbols）", False, f"库已 import 但解析失败：{str(exc)[:180]}")
            # ROP / gadget 枚举依赖 capstone 原生库：被系统策略拦截时算环境跳过，不算失败
            for name, call in (
                ("pwnrop 自动拼 system('/bin/sh') 链",
                 lambda: run_tool("pwnrop", path=str(fixture), op="rop", func="system",
                                  arg_strings="/bin/sh")),
                ("pwnrop gadget 搜索",
                 lambda: run_tool("pwnrop", path=str(fixture), op="gadgets", filt="pop rdi", limit=5)),
            ):
                try:
                    out = call()
                except (DomainToolError, ToolError) as exc:
                    text = str(exc)
                    if "原生模块被系统策略拦截" in text or "加载失败" in text:
                        skip(name, "capstone 原生库被系统策略拦截")
                    else:
                        check(name, False, text[:200])
                    continue
                if name.endswith("链"):
                    hexm = re.search(r"hex=([0-9a-f]+)", out)
                    check(name, bool(hexm) and len(hexm.group(1)) > 8, out[:240])
                else:
                    check(name, "pop rdi" in out, out[:200])
    else:
        skip("pwnrop 功能组", "pwntools 不可用")
        _expect_error(
            lambda: run_tool("pwnrop", path=str(tmp / "x.elf"), op="checksec"),
            ("加载失败", "fetch_pylibs"), "pwnrop 不可用时提示可操作")

    # ---------------- F. env 覆盖（放最后，避免污染前面的路径判断） ----------------
    fake = tmp / "fake_libs"
    pkg = fake / "fake_lib_xyz"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    old = os.environ.get("CTFY_PYLIBS")
    try:
        os.environ["CTFY_PYLIBS"] = str(fake)
        check("env CTFY_PYLIBS 覆盖候选首位", pylibs.candidate_dirs()[0] == fake, str(pylibs.candidate_dirs()[0]))
        pylibs.ensure_on_path(force=True)
        check("env 指定目录里的模块可导入", pylibs.available("fake_lib_xyz"))
        mod = pylibs.import_or_none("fake_lib_xyz")
        check("import_or_none 真能返回模块", mod is not None and getattr(mod, "VALUE", None) == 42)
    finally:
        if old is None:
            os.environ.pop("CTFY_PYLIBS", None)
        else:
            os.environ["CTFY_PYLIBS"] = old

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"通过 {PASSED}，跳过 {len(SKIPPED)}，失败 {len(FAILS)}：{FAILS}")
        return 1
    print(f"smoke_libs 全部通过（{PASSED} 项，跳过 {len(SKIPPED)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())