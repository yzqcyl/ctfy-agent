#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""格攻击数学（纯 stdlib）：LLL 归约 + 一元 Coppersmith 小根。

为什么单独立一个模块
--------------------
2026-11-12 的能力评估（docs/capability-report-2026-11-12.md §7#4）把「格攻击（LLL/Coppersmith）」
列为 CRYPTO 天花板的头号空白：RSA 低指数/已知高位/小根、部分明文、coppersmith 短填充这类题
在真实赛题里占比很高，而 `rsatool` 的 factor 分支当时只能回一句「考虑格攻击/Coppersmith（本工具不做）」。

为什么不装 fpylll/sage：镜像是 python:3.12-slim，fpylll 要现编 libfplll（C++ 工具链 + 构建时间），
sage 解包 500MB+ 直接撞 400MB 门禁。而 LLL 本身用 `fractions.Fraction` 做精确 Gram-Schmidt
就够了——CTF 里的格维数一般 ≤ 20，维数上限见下；慢一点（秒级）远好过装不上。

算法口径（教科书实现，无魔法）
------------------------------
* `lll_reduce`：δ-LLL（默认 3/4），精确有理 Gram-Schmidt，整数基入整数基出；
  哨兵：维数 > MAX_DIM、单元素位长 > MAX_BITS 直接拒绝（防止模型塞一个 200 维大矩阵把这一步拖死）。
* `coppersmith_univariate`：Howgrave-Graham 一元小根构造。
  - 输入 monic 多项式 f（升幂系数）与模数 N（或它的因子 b，用 beta=log_N(b) 表达）；
  - 已知 f(x0) ≡ 0 (mod b) 且 |x0| < X，X 默认按 N^(beta/deg) 量级给；
  - 造多项式 g_{i,j}(x) = x^j · N^{m-i} · f(x)^i（i<m, j<deg）与 g_{m+k}(x) = x^k · f(x)^m（k<t），
    代入 x → X·x 后取系数向量做 LLL；短向量还原成多项式后在整数上求公共根；
  - 求根不依赖任何第三方：先多项式 gcd（两条短向量都精确过 x0，gcd 通常就是线性因子），
    再退化为有理根检验（小素数试除常数项）。
* 所有返回值都是可读文本的原料（dict），格式化放在 tools_domain_ext.rsatool。

已知边界（写清楚免得误判）
--------------------------
* 只做**一元** Coppersmith。二元（boneh-durfee、部分密钥泄漏）不在范围内，别硬套。
* `beta < 1`（根模 N 的未知因子 b）时无法在工具内独立验证，只给候选根 + 「用 gcd(f(root), N) 复核」提示。
* m/t 越大越强也越慢；上限由 MAX_DIM 卡住（默认 8~12 维，秒级）。
"""

from __future__ import annotations

import decimal
import math
from decimal import ROUND_FLOOR, Decimal
from fractions import Fraction

MAX_DIM = 24          # 基向量个数上限（LLL 是 O(n^4) 精确有理运算，再大就不是"秒级"了）
MAX_BITS = 1 << 17    # 单元素 |value|.bit_length() 上限（约 13 万位）；防 DoS
DEFAULT_DELTA = Fraction(3, 4)


class LatticeError(ValueError):
    """参数/规模不合法（由调用方转成 DomainToolError 之类的用户可读错误）。"""


# ---------------------------------------------------------------- 多项式工具（升幂）
# 内部统一用 Fraction，对外可以是 int。零多项式表示为 []（而不是 [0]）。

def _trim(poly):
    out = [Fraction(c) for c in poly]
    while out and out[-1] == 0:
        out.pop()
    return out


def poly_mul(a, b):
    a, b = _trim(a), _trim(b)
    if not a or not b:
        return []
    out = [Fraction(0)] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x == 0:
            continue
        for j, y in enumerate(b):
            out[i + j] += x * y
    return _trim(out)


def poly_pow(a, e: int):
    if e < 0:
        raise LatticeError("多项式幂次不能为负")
    result = [Fraction(1)]
    base = _trim(a)
    while e:
        if e & 1:
            result = poly_mul(result, base)
        e >>= 1
        if e:
            base = poly_mul(base, base)
    return result


def poly_shift(a, k: int):
    """乘 x^k。"""
    return _trim([Fraction(0)] * int(k) + list(a))


def poly_eval(a, x) -> Fraction:
    """Horner 求值（Fraction 精确）。"""
    x = Fraction(x)
    acc = Fraction(0)
    for c in reversed(_trim(a)):
        acc = acc * x + c
    return acc


def poly_mod(a, b):
    """多项式取余（域上长除法；b 非零）。"""
    a, b = _trim(a), _trim(b)
    if not b:
        raise LatticeError("除数为零多项式")
    rem = list(a)
    db = len(b) - 1
    while rem and len(rem) - 1 >= db:
        coef = rem[-1] / b[-1]
        shift = len(rem) - 1 - db
        if coef:
            for i, c in enumerate(b):
                rem[shift + i] -= coef * c
        rem = _trim(rem)
    return rem


def poly_gcd(a, b):
    """域上 gcd，返回首一多项式（[] 表示零）。"""
    a, b = _trim(a), _trim(b)
    while b:
        a, b = b, poly_mod(a, b)
    if not a:
        return []
    lead = a[-1]
    return _trim([c / lead for c in a])


def poly_div_exact(a, b):
    """整除（假设可整除），返回商（Fraction）。"""
    a, b = _trim(a), _trim(b)
    if not b:
        raise LatticeError("除数为零多项式")
    rem = list(a)
    db = len(b) - 1
    quotient = [Fraction(0)] * max(0, len(a) - len(b) + 1)
    while rem and len(rem) - 1 >= db:
        coef = rem[-1] / b[-1]
        shift = len(rem) - 1 - db
        quotient[shift] = coef
        for i, c in enumerate(b):
            rem[shift + i] -= coef * c
        rem = _trim(rem)
    if rem:
        raise LatticeError("多项式不能整除（余式非零）")
    return _trim(quotient)


def _fraction_root(g):
    """首一/线性 gcd 多项式里的整数根候选；不是线性就返回 None。"""
    g = _trim(g)
    if len(g) != 2:
        return None
    num, den = g[0], g[1]
    if den == 0:
        return None
    root = -num / den
    if root.denominator != 1:
        return None
    return int(root)


def candidate_roots_from_polys(polys):
    """从若干「在 x0 处精确为 0」的多项式里榨出整数根候选（纯 stdlib）。

    思路：这些多项式都精确过 x0，所以任意两条的 gcd 一定含 (x - x0)。短向量少、参数强时
    gcd 恰好就是线性的，直接得根；参数更强时 gcd 会带出别的公共因子（次数 3~10），
    这时纯 stdlib 拿不到根——把多项式原样交给可选的 root_finder（镜像里有 sympy 时由调用方注入）。
    """
    polys = [p for p in polys if p and len(p) >= 2]
    found = []
    seen = set()
    if len(polys) >= 2:
        g = poly_gcd(polys[0], polys[1])
        for poly in polys[2:]:
            if len(g) <= 2:
                break
            g = poly_gcd(g, poly)
        root = _fraction_root(g)
        if root is not None:
            found.append(root)
            seen.add(root)
        for i in range(len(polys)):
            for j in range(i + 1, len(polys)):
                cand = _fraction_root(poly_gcd(polys[i], polys[j]))
                if cand is not None and cand not in seen:
                    found.append(cand)
                    seen.add(cand)
    return found


# ---------------------------------------------------------------- LLL
#
# 为什么用 Decimal 而不是 Fraction 做 Gram-Schmidt：
#   第一版用 Fraction 精确算 b* 与 mu，结果在 11 维 / 2000 位元素上跑了 5 分钟还没出来——
#   精确有理数的分母是「范数连乘积」，位长指数级膨胀，每次乘法都在做几千位大数除法。
#   Decimal 的指数范围足够（2^2000 只是 exponent≈600），80 位有效数字对 LLL 的取整判定
#   绰绰有余（教科书里浮点 LLL 就是常规做法）；基向量本身永远用整数精确更新，不会漂。
# 另：size reduction 只改 mu 的一行、不动 b*（b_k - q·b_j 不改变 b_k 的正交分量），
#   只在 swap 时重算一次 Gram-Schmidt——第一版每做一次消减就整体重算，是另一个数量级的浪费。

_LLL_PREC = 80


def _dec(value) -> Decimal:
    return Decimal(value)


def _norm2(vec) -> Decimal:
    return sum((v * v for v in vec), Decimal(0))


def _gram_schmidt_dec(basis, width):
    n = len(basis)
    bstar = []
    mu = [[Decimal(0)] * n for _ in range(n)]
    for i in range(n):
        v = [Decimal(x) for x in basis[i]]
        for j in range(i):
            denom = _norm2(bstar[j])
            if denom == 0:
                mu[i][j] = Decimal(0)
                continue
            numer = sum((Decimal(basis[i][k]) * bstar[j][k] for k in range(width)), Decimal(0))
            mu[i][j] = numer / denom
            v = [v[k] - mu[i][j] * bstar[j][k] for k in range(width)]
        bstar.append(v)
    return bstar, mu


def _dec_round_half_up(value: Decimal) -> int:
    return int((value + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


def lll_reduce(basis, delta=DEFAULT_DELTA):
    """整数基 → δ-LLL 归约后的整数基（行向量）。"""
    rows = [[int(x) for x in row] for row in basis]
    n = len(rows)
    if n == 0:
        return []
    width = len(rows[0])
    if width == 0:
        raise LatticeError("基向量不能为空")
    if any(len(r) != width for r in rows):
        raise LatticeError("基向量长度不一致（每行必须等长）")
    if n > MAX_DIM:
        raise LatticeError("维数 %d 超过上限 %d（LLL 代价太高）" % (n, MAX_DIM))
    for row in rows:
        for v in row:
            if abs(v).bit_length() > MAX_BITS:
                raise LatticeError("基元素过大（位长 > %d），拒绝处理" % MAX_BITS)
    if n == 1:
        return rows
    delta = Fraction(delta)
    if not (Fraction(1, 4) < delta < 1):
        raise LatticeError("delta 必须在 (0.25, 1) 内")
    delta_dec = Decimal(delta.numerator) / Decimal(delta.denominator)

    ctx_prec = decimal.getcontext().prec
    decimal.getcontext().prec = _LLL_PREC
    try:
        b = [list(row) for row in rows]
        bstar, mu = _gram_schmidt_dec(b, width)
        k = 1
        while k < n:
            # --- size reduction：b[k] 依次对 b[k-1..0] 取整消减
            for j in range(k - 1, -1, -1):
                q = _dec_round_half_up(mu[k][j])
                if not q:
                    continue
                b[k] = [b[k][t] - q * b[j][t] for t in range(width)]
                # b[k] 的正交分量不变；只更新 mu 行（i<j 用 mu[j][i]，j 自身减 q）
                for i in range(j):
                    mu[k][i] -= q * mu[j][i]
                mu[k][j] -= q
            # --- Lovász 条件
            lhs = _norm2(bstar[k])
            rhs = (delta_dec - mu[k][k - 1] * mu[k][k - 1]) * _norm2(bstar[k - 1])
            if lhs >= rhs:
                k += 1
            else:
                b[k], b[k - 1] = b[k - 1], b[k]
                bstar, mu = _gram_schmidt_dec(b, width)
                k = max(k - 1, 1)
        return b
    finally:
        decimal.getcontext().prec = ctx_prec


# ---------------------------------------------------------------- Coppersmith（一元）
def _mod_center(value, modulus):
    """把 value 规约到 (-modulus/2, modulus/2]。"""
    value %= modulus
    if value > modulus // 2:
        value -= modulus
    return value


def _int_nth_root_upper(n: int, k: int) -> int:
    if n <= 0:
        return 0
    x = 1 << ((n.bit_length() + k - 1) // k)
    while True:
        y = ((k - 1) * x + n // max(1, x ** (k - 1))) // k
        if y >= x:
            break
        x = y
    while (x + 1) ** k <= n:
        x += 1
    while x ** k > n:
        x -= 1
    return x


def build_coppersmith_lattice(coeffs, N: int, X: int, m: int, t: int):
    """返回 (整数基, 维数)；基矩阵每行是一个缩放后的多项式系数向量。"""
    f = _trim([Fraction(c) for c in coeffs])
    deg = len(f) - 1
    if deg < 1:
        raise LatticeError("多项式次数至少为 1")
    if not f or f[-1] != 1:
        raise LatticeError("多项式必须首一（最高次系数=1）")
    m = int(m)
    t = int(t)
    if m < 1:
        raise LatticeError("m 至少为 1")
    if t < 0:
        raise LatticeError("t 不能为负")
    dim = deg * m + t
    if dim > MAX_DIM:
        raise LatticeError("格维数 %d 超过上限 %d（调小 m/t）" % (dim, MAX_DIM))
    f_int = [int(c) for c in f]          # 首一且系数为整数（调用方保证）

    rows = []
    # (1) N^{m-i} · x^j · f(x)^i ，i ∈ [0, m)，j ∈ [0, deg)
    for i in range(m):
        poly_i = poly_pow([Fraction(c) for c in f_int], i)
        scale = N ** (m - i)
        for j in range(deg):
            poly = poly_shift(poly_i, j)
            rows.append([int(c * scale) for c in poly])
    # (2) x^k · f(x)^m ，k ∈ [0, t)
    poly_m = poly_pow([Fraction(c) for c in f_int], m)
    for k in range(t):
        poly = poly_shift(poly_m, k)
        rows.append([int(c) for c in poly])

    # 统一补齐到 dim 列，并代入 x → X·x（第 k 列乘 X^k）
    padded = []
    for row in rows:
        row = row + [0] * (dim - len(row))
        padded.append([row[k] * (X ** k) for k in range(dim)])
    return padded, dim


def _poly_from_vector(vec, X: int):
    """短向量 → 多项式系数（第 k 项除以 X^k）；除不尽返回 None。"""
    coeffs = []
    for k, value in enumerate(vec):
        xk = X ** k
        if value % xk != 0:
            return None
        coeffs.append(Fraction(value // xk))
    return _trim(coeffs)


def coppersmith_auto(coeffs, N: int, X: int | None = None, root_finder=None,
                     budget_s: float = 12.0):
    """稳健版一元 Coppersmith：在「(Decimal 精度, m, t) 小阶梯」上自动试，命中即返回。

    为什么要阶梯：LLL 用有限精度 Decimal 做 Gram-Schmidt，同一道题在 (m,t) 变大时
    还原出的短向量集合会变（更多向量满足 Howgrave-Graham 界），有时 gcd 出线性因子、
    有时出高次公因子；实测 m=3/t=2 与 m=4/t=2 各有各的命中案例。与其赌一组参数，
    不如按「小维数优先（快）→ 大维数（慢）」的顺序试，命中就停，总耗时有 budget_s 上限。

    只支持 beta=1（根模 N 本身）。beta<1（根模 N 的未知因子）需要 m≈25+ 维才达到理论界，
    在「秒级工具调用」里不现实，直接报错让模型换打法——**不要假装支持**。
    """
    import time as _time

    beta = 1.0
    N = int(N)
    if X is None:
        deg = len([c for c in coeffs]) - 1
        deg = max(1, deg)
        X = 1 << max(8, int(N.bit_length() / (2 * deg)))
    ladder = ((80, 3, 2), (40, 3, 2), (80, 4, 2), (40, 4, 3), (80, 4, 3))
    t_start = _time.time()
    tried = []
    for prec, m, t in ladder:
        left = budget_s - (_time.time() - t_start)
        if left <= 0.5:
            break
        _set_lll_precision(prec)
        try:
            res = coppersmith_univariate(coeffs, N, X=X, beta=beta, m=m, t=t,
                                         root_finder=root_finder)
        except LatticeError:
            continue
        tried.append((prec, m, t, res["found"]))
        if res["found"]:
            res["ladder"] = tried
            res["elapsed_s"] = round(_time.time() - t_start, 2)
            res["precision"] = prec
            return res
    return {
        "found": False, "root": None, "roots": [], "X": X, "m": None, "t": None,
        "dim": None, "deg": len([c for c in coeffs]) - 1, "beta": 1.0, "verified": False,
        "ladder": tried, "elapsed_s": round(_time.time() - t_start, 2), "precision": None,
        "note": ("阶梯全试完没找到（实测可靠区间：根位长 ≲ N^(1/(2δ))，δ=2 的 512 bit N 约 2^120）；"
                 "根更大时换 z3/linearmod 或人工分析，别在这里死磕"),
    }


def _set_lll_precision(prec: int) -> None:
    """改 LLL 的 Decimal 精度（只影响后续调用）。"""
    global _LLL_PREC
    _LLL_PREC = max(20, int(prec))


def coppersmith_univariate(coeffs, N: int, X: int | None = None, beta: float = 1.0,
                           m: int = 3, t: int | None = None, root_finder=None):
    """一元 Coppersmith：找 |x0| < X 且 f(x0) ≡ 0 (mod b)。

    参数
    ----
    coeffs : 升幂系数（常数项在前），必须首一；非整数系数不接受。
    N      : 模数（根模 N 或模 N 的因子 b，后者用 beta 表达）。
    X      : 根上界；不给则按 N^(beta/deg) 保守取（向下留 4 bit 余量）。
    beta   : log_N(b)，缺省 1.0（根模 N 本身）。
    m, t   : 格参数；t 缺省按 deg·m 的一半取（至少 1）。
    root_finder : 可选回调，签名 root_finder(coeffs)->list[int]（Fraction 系数，升幂）。
                  镜像里 sympy 可用时由调用方注入 ground_roots；纯 stdlib 下走 gcd 路线。

    返回 dict：found / root / X / m / t / dim / verified / note。
    """
    N = int(N)
    if N < 2:
        raise LatticeError("N 必须 ≥ 2")
    f = [Fraction(c) for c in coeffs]
    f = _trim(f)
    deg = len(f) - 1
    if deg < 1:
        raise LatticeError("多项式次数至少为 1")
    if any(c.denominator != 1 for c in f):
        raise LatticeError("多项式系数必须是整数")
    if f[-1] != 1:
        lead = int(f[-1])
        try:
            lead_inv = pow(lead % N, -1, N)
        except ValueError:
            raise LatticeError("首项系数与 N 不互素，无法首一化（先换多项式或先分解 N）") from None
        f = _trim([Fraction(int(c % N) * lead_inv % N) for c in f])
        # 首一化后模 N 等价，但常数项可能被压进 [0,N)；不影响小根判定
    f_int = [int(c) for c in f]

    beta = float(beta or 1.0)
    if not (0.0 < beta <= 1.0):
        raise LatticeError("beta 必须在 (0, 1] 内")
    if abs(beta - 1.0) > 1e-9:
        raise LatticeError(
            "beta<1（根模 N 的未知因子，如「已知 p 高位」）当前不支持："
            "该场景要 m≈25+ 维才到理论界，秒级工具调用跑不动。"
            "已知 p 高位请改用「低位枚举 + gcd(N, p_high*2^k + x)」或 z3 解约束。")
    m = int(m or 3)
    if t is None or int(t) <= 0:
        t = max(1, int(deg * m // 2))
    t = int(t)
    if X is None or int(X) <= 0:
        bits = max(1, int(N.bit_length() * beta / deg) - 4)
        X = 1 << bits
    X = int(X)
    if X < 2:
        raise LatticeError("X 太小（至少 2）")

    basis, dim = build_coppersmith_lattice(f_int, N, X, m, t)
    reduced = lll_reduce(basis)

    # 从最短的若干行还原多项式，先两两求 gcd，再全体 gcd；gcd 线性即得整数根
    polys = []
    for vec in reduced[: min(len(reduced), 8)]:
        poly = _poly_from_vector(vec, X)
        if poly and len(poly) >= 2:
            polys.append(poly)

    candidates = candidate_roots_from_polys(polys)
    note = ""
    if not candidates and root_finder is not None and polys:
        # 纯 stdlib 的 gcd 路线失效时（参数更强、gcd 带出高次公因子），交给可选第三方求根器
        try:
            for poly in polys:
                for cand in root_finder(poly) or []:
                    cand = int(cand)
                    if cand not in candidates:
                        candidates.append(cand)
            if candidates:
                note = "根由外部 root_finder 恢复（gcd 非线性）"
        except Exception:  # noqa: BLE001 - 求根器是尽力而为，失败就继续走 stdlib 结论
            candidates = []

    verified_roots = []
    for candidate in candidates:
        if X is not None and abs(candidate) > X:
            continue  # 超出 X 的候选不是「小根」，丢弃（否则会把 N 里另一个大根误当结果）
        fx = int(poly_eval(f_int, candidate))
        if beta >= 1.0:
            if fx % N == 0:
                verified_roots.append(candidate)
        else:
            g2 = math.gcd(abs(fx), N)
            if 1 < g2 < N or fx % N == 0:
                verified_roots.append(candidate)

    if verified_roots:
        root = verified_roots[0]
        if beta >= 1.0:
            note = note or "f(root) mod N = 0"
        else:
            fx = int(poly_eval(f_int, root))
            g2 = math.gcd(abs(fx), N)
            note = (note + "；" if note else "") + "gcd(f(root), N) = %d（用它继续分解 N）" % g2
        return {
            "found": True, "root": root, "roots": verified_roots, "X": X, "m": m, "t": t,
            "dim": dim, "deg": deg, "beta": beta, "verified": True, "note": note,
        }

    if not note:
        note = "短向量里没恢复出满足 f(root)≡0 的整数根（增大 m/t、放宽 X，或换 beta/多项式）"
    return {
        "found": False, "root": None, "roots": [], "X": X, "m": m, "t": t,
        "dim": dim, "deg": deg, "beta": beta, "verified": False, "note": note,
    }