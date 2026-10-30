# -*- coding: utf-8 -*-
"""[crypto-toolbox 2026-10-30] crypto 方向工具箱 v1 回归（独立可跑，未挂入 tests/smoke.py 注册表）。

覆盖：aesblock（FIPS-197 AES-128 / NIST SP800-38A CBC+CTR 向量、PKCS7、xor、bitflip）、
rsatool（small_e / common_modulus / wiener / fermat / pollard_pm1 / pollard_rho / hastad / crt / factor）、
prng（MT19937 标准向量 + 状态恢复、LCG 参数恢复）、DOMAIN_SPECS 自动注册与派发。
全部纯 stdlib、离线、无网络。

跑法：
  $env:PYTHONIOENCODING="utf-8"; python tests\\smoke_crypto.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ctfy_agent.tools_domain_ext import (  # noqa: E402
    DOMAIN_NAMES, DomainToolError, _MT19937, call_domain, prng, rsatool, aesblock,
)
from ctfy_agent.tools_local import ToolError  # [toolbox-fix 2026-10-30] call_domain 边界转译后的异常类

CASES = []


def case(fn):
    CASES.append(fn)
    return fn


def is_prime(x: int) -> bool:
    if x < 2:
        return False
    for p in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if x % p == 0:
            return x == p
    d, s = x - 1, 0
    while d % 2 == 0:
        d //= 2
        s += 1
    for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37):
        if a >= x:
            continue
        t = pow(a, d, x)
        if t in (1, x - 1):
            continue
        for _ in range(s - 1):
            t = t * t % x
            if t == x - 1:
                break
        else:
            return False
    return True


# ---------------- AES ----------------

# FIPS-197 C.1
AES_KEY = "000102030405060708090a0b0c0d0e0f"
AES_PT = "00112233445566778899aabbccddeeff"
AES_CT = "69c4e0d86a7b0430d8cdb78070b4c55a"

# NIST SP800-38A F.2.1/F.2.2 CBC-AES128
NIST_KEY = "2b7e151628aed2a6abf7158809cf4f3c"
NIST_IV = "000102030405060708090a0b0c0d0e0f"
NIST_PT1 = "6bc1bee22e409f96e93d7e117393172a"
NIST_PT2 = "ae2d8a571e03ac9c9eb76fac45af8e51"
NIST_CBC_CT = "7649abac8119b246cee98e9b12e9197d5086cb9b507219ee95db113a917678b2"
# NIST SP800-38A F.5.1 CTR-AES128
NIST_CTR_CT = "874d6191b620e3261bef6864990db6ce9806f66b7970fdff8617187bb9fffdff"


def _hex_of(out: str) -> str:
    return [ln for ln in out.splitlines() if ln.startswith("hex=")][0][4:].replace(" ", "").lower()


@case
def case_aes_ecb_kat():
    out = aesblock(op="ecb_encrypt", key=AES_KEY, data=AES_PT)
    assert _hex_of(out) == AES_CT, _hex_of(out)[:80]
    back = aesblock(op="ecb_decrypt", key=AES_KEY, data=AES_CT)
    assert _hex_of(back) == AES_PT, _hex_of(back)[:80]


@case
def case_aes_cbc_kat():
    out = aesblock(op="cbc_encrypt", key=NIST_KEY, iv=NIST_IV, data=NIST_PT1 + NIST_PT2)
    assert _hex_of(out) == NIST_CBC_CT, _hex_of(out)[:80]
    back = aesblock(op="cbc_decrypt", key=NIST_KEY, iv=NIST_IV, data=NIST_CBC_CT)
    assert _hex_of(back) == NIST_PT1 + NIST_PT2


@case
def case_aes_ctr_kat():
    out = aesblock(op="ctr", key=NIST_KEY, nonce="f0f1f2f3f4f5f6f7f8f9fafbfcfdfeff",
                   data=NIST_PT1 + NIST_PT2)
    assert _hex_of(out) == NIST_CTR_CT, _hex_of(out)[:80]


@case
def case_aes_pkcs7():
    padded = aesblock(op="pkcs7_pad", data="414243")
    assert _hex_of(padded) == "414243" + "0d" * 13, _hex_of(padded)
    unpadded = aesblock(op="pkcs7_unpad", data="414243" + "0d" * 13)
    assert "414243" in unpadded and "ascii=ABC" in unpadded, unpadded[:200]
    # 满块时补整块
    full = aesblock(op="pkcs7_pad", data="00" * 16)
    assert len(_hex_of(full)) == 64, _hex_of(full)


@case
def case_aes_xor_and_bitflip():
    out = aesblock(op="xor", data="0011223344556677", iv="0102030405060708")
    assert _hex_of(out) == "011321374153617f", _hex_of(out)
    # CBC bit-flip：已知明文 "user=guest" 改成 "user=admin " 的第 5 字节
    known, target = "user=guest", "user=admin"
    flipped = aesblock(op="bitflip", iv="00" * 16, known=known, target=target)
    raw = bytes.fromhex(_hex_of(flipped))
    decoded = bytes(raw[i] ^ ord(known[i]) for i in range(len(known)))
    assert decoded.decode("latin-1") == target, decoded
    assert len(raw) == 16 and raw[len(known):] == b"\x00" * (16 - len(known)), raw


@case
def case_aes_errors():
    for kwargs in (
        {"op": "ecb_encrypt", "key": "00" * 16, "data": "00" * 15},   # 非整块
        {"op": "ecb_encrypt", "key": "00" * 8, "data": "00" * 16},    # key 长度非法
        {"op": "cbc_encrypt", "key": NIST_KEY, "iv": "00" * 8, "data": "00" * 16},
        {"op": "pkcs7_unpad", "data": "00" * 15 + "03"},          # 填充非法（末3字节非 03 03 03）
        {"op": "nope"},
    ):
        try:
            aesblock(**kwargs)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (kwargs,))


# ---------------- RSA ----------------

def _rsa_keys(bits=64):
    """生成 (n, e, d, p, q)：bits 位素数，e=65537。"""
    import random

    rnd = random.Random(20261030)
    while True:
        p = rnd.getrandbits(bits) | (1 << (bits - 1)) | 1
        if is_prime(p):
            break
    while True:
        q = rnd.getrandbits(bits) | (1 << (bits - 1)) | 1
        if q != p and is_prime(q):
            break
    n = p * q
    phi = (p - 1) * (q - 1)
    e = 65537
    return n, e, pow(e, -1, phi), p, q


@case
def case_rsa_small_e():
    m = int.from_bytes(b"hi!!", "big")
    n = 1 << 256
    out = rsatool(op="small_e", e="3", c=str(m ** 3), n=str(n))
    assert out.splitlines()[0].strip() == "m = %d" % m, out[:120]
    assert "ascii=hi!!" in out, out[:200]


@case
def case_rsa_common_modulus():
    n, _e, _d, _p, _q = _rsa_keys()
    e1, e2, m = 7, 11, 987654321
    out = rsatool(op="common_modulus", n=str(n), e1=str(e1), e2=str(e2),
                  c1=str(pow(m, e1, n)), c2=str(pow(m, e2, n)))
    assert out.splitlines()[0].strip() == "m = %d" % m, out[:160]


@case
def case_rsa_wiener():
    # 构造 d 很小：取 p,q，phi，人为选 d 后反求 e
    p, q = 1000003, 1000033
    n, phi = p * q, (p - 1) * (q - 1)
    d = 5
    while math.gcd(d, phi) != 1:
        d += 2
    e = pow(d, -1, phi)
    out = rsatool(op="wiener", n=str(n), e=str(e))
    assert ("d = %d" % d) in out and "p*q==n：True" in out, out[:200]


@case
def case_rsa_fermat():
    p = 1000000007
    q = p + 2
    while not is_prime(q):
        q += 2
    out = rsatool(op="fermat", n=str(p * q))
    assert ("p = %d" % p) in out and ("q = %d" % q) in out, out[:200]  # [toolbox-fix] 修掉 and/or 优先级混乱


@case
def case_rsa_pollard_pm1():
    # 65537-1 = 2^16；65539-1 = 2*3^2*11*331 → B=400 足够
    out = rsatool(op="pollard_pm1", n=str(65537 * 65539), B=400)
    assert "65537" in out and "65539" in out, out[:160]


@case
def case_rsa_pollard_rho():
    p, q = 1000003, 1000033
    out = rsatool(op="pollard_rho", n=str(p * q), limit=20000)
    assert set([str(p), str(q)]) & set(out.replace("p = ", "").replace("q = ", "").split()), out[:160]


@case
def case_rsa_hastad():
    m = int.from_bytes(b"OK!", "big")
    pairs = [(1000003, 1000033), (1000037, 1000039), (1000081, 1000099)]
    out = rsatool(op="hastad", e="3",
                  ns=[str(a * b) for a, b in pairs],
                  cs=[str(pow(m, 3, a * b)) for a, b in pairs])
    assert out.splitlines()[0].strip() == "m = %d" % m, out[:160]
    assert "ascii=OK!" in out, out[:200]


@case
def case_rsa_crt_and_factor():
    assert "x = 23" in rsatool(op="crt", ns=["3", "5", "7"], cs=["2", "3", "2"])
    n, _e, _d, p, q = _rsa_keys(20)
    out = rsatool(op="factor", n=str(n))
    assert str(p) in out or str(q) in out, out[:200]


@case
def case_rsa_errors():
    for kwargs in (
        {"op": "small_e", "e": "3", "c": "25"},          # 25 不是三次幂
        {"op": "wiener", "n": "77", "e": "17"},          # 参数太小
        {"op": "hastad", "e": "3", "ns": ["77"], "cs": ["1"]},
        {"op": "crt", "ns": ["3", "5"], "cs": ["1"]},    # 长度不等
        {"op": "crt", "ns": ["6", "9"], "cs": ["1", "2"]},  # 不互素
    ):
        try:
            rsatool(**kwargs)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (kwargs,))


# ---------------- PRNG ----------------

@case
def case_mt_vector_and_recover():
    out = prng(op="mt_seed", seed=5489, count=5)
    assert "3499211612, 581869302, 3890346734, 3586334585, 545404204" in out, out[-200:]
    # 恢复：624 个输出 → 后续 3 个预测必须与参考实现一致
    g = _MT19937(20261030)
    outs = [g.next() for _ in range(624)]
    expect = ", ".join(str(g.next()) for _ in range(3))
    pred = prng(op="mt_predict", outputs=outs, count=3).splitlines()[-1]
    assert pred == expect, "%r != %r" % (pred, expect)


@case
def case_mt_untemper_roundtrip():
    g = _MT19937(424242)
    outs = [g.next() for _ in range(624)]
    from ctfy_agent.tools_domain_ext import _mt_untemper

    assert all(_mt_untemper(outs[i]) == g.mt[i] for i in range(624))


@case
def case_lcg_recover():
    a, c, mod = 1103515245, 12345, 2 ** 31
    xs = [42]
    for _ in range(8):
        xs.append((a * xs[-1] + c) % mod)
    out = prng(op="lcg_recover", outputs=xs, m=mod, count=3)
    assert "a = %d" % a in out and "c = %d" % c in out and "校验通过" in out, out[:300]


@case
def case_prng_errors():
    for kwargs in (
        {"op": "mt_predict", "outputs": [1, 2, 3]},   # 输出不足 624
        {"op": "lcg_recover", "outputs": [1, 2, 3]},  # 输出不足
        {"op": "nope"},
    ):
        try:
            prng(**kwargs)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (kwargs,))


# ---------------- 注册与派发 ----------------

@case
def case_registry_auto_wiring():
    assert {"rsatool", "aesblock", "prng"} <= DOMAIN_NAMES, sorted(DOMAIN_NAMES)
    from ctfy_agent.tools_misc_ext import MISC_EXT_SPECS, NAMES

    names = {s["function"]["name"] for s in MISC_EXT_SPECS}
    assert {"rsatool", "aesblock", "prng"} <= names & NAMES


@case
def case_dispatch():
    out = call_domain("aesblock", {"op": "ecb_encrypt", "key": AES_KEY, "data": AES_PT})
    assert AES_CT in out.replace(" ", "").lower(), out[:200]
    out = call_domain("rsatool", {"op": "crt", "ns": ["3", "5", "7"], "cs": ["2", "3", "2"]})
    assert "x = 23" in out, out[:160]
    out = call_domain("prng", {"op": "mt_seed", "seed": 5489, "count": 3})
    assert "3499211612" in out, out[:200]


# ---------------- [toolbox-fix 2026-10-30] 审计回归 ----------------

@case
def case_fix_aes_192_256_kat():
    """FIPS-197 C.2 / C.3：AES-192 与 AES-256 此前没有 KAT 覆盖。"""
    k192 = "".join("%02x" % i for i in range(24))
    out = aesblock(op="ecb_encrypt", key=k192, data=AES_PT)
    # 期望值 = FIPS-197 C.2 密文，已用 .NET Aes（ECB/NoPadding）本地仲裁核对
    assert _hex_of(out) == "dda97ca4864cdfe06eaf70a0ec0d7191", _hex_of(out)
    k256 = "".join("%02x" % i for i in range(32))
    out = aesblock(op="ecb_encrypt", key=k256, data=AES_PT)
    assert _hex_of(out) == "8ea2b7ca516745bfeafc49904b496089", _hex_of(out)


@case
def case_fix_aes_bad_hex():
    """B4：iv/nonce/bitflip 的坏 hex 曾 ValueError 裸崩。"""
    for kwargs in (
        {"op": "cbc_encrypt", "key": NIST_KEY, "iv": "zz", "data": "00" * 16},
        {"op": "cbc_decrypt", "key": NIST_KEY, "iv": "zz", "data": "00" * 16},
        {"op": "ctr", "key": NIST_KEY, "nonce": "zz", "data": "00" * 16},
        {"op": "bitflip", "iv": "zz", "known": "abc", "target": "xyz"},
    ):
        try:
            aesblock(**kwargs)
        except DomainToolError:
            continue
        raise AssertionError("应报错：%r" % (kwargs,))


@case
def case_fix_bitflip_multibyte_guard():
    try:
        aesblock(op="bitflip", iv="00" * 16, known="中文", target="英文")
    except DomainToolError as exc:
        assert "单字节" in str(exc), str(exc)
        return
    raise AssertionError("多字节 known/target 应报错（bytearray 赋值会裸崩）")


@case
def case_fix_ctr_overflow():
    """B8：计数器越过 2^128 曾 OverflowError 裸崩。"""
    out = aesblock(op="ctr", key=NIST_KEY, nonce="ff" * 16,
                   data="00" * 32, counter=(1 << 128) - 1)
    assert "ctr 输出" in out and len(_hex_of(out)) == 64, out[:160]


@case
def case_fix_prng_clip():
    """B7：mt_seed 大 count 曾生成 20KB 文本直灌上下文（绕过 6000 字符契约）。"""
    out = prng(op="mt_seed", seed=1, count=2000)
    assert "输出已截断" in out and len(out) <= 6100, (len(out), out[-80:])


@case
def case_fix_dispatch_bad_int():
    """B6：派发层垃圾整数参数（count/limit/B）曾 ValueError 裸崩。"""
    for name, kwargs in (
        ("prng", {"op": "mt_seed", "seed": 1, "count": "lots"}),
        ("rsatool", {"op": "fermat", "n": "77", "limit": "huge"}),
        ("rsatool", {"op": "pollard_pm1", "n": "77", "B": "zzz"}),
    ):
        try:
            call_domain(name, kwargs)
        except (DomainToolError, ToolError):  # [toolbox-fix] 边界转译
            continue
        raise AssertionError("应报错：%s %r" % (name, kwargs))


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
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())