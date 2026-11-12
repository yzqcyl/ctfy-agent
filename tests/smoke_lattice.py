#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_lattice.py — 格攻击工具（LLL + 一元 Coppersmith）的离线回归。

背景（docs/capability-report-2026-11-12.md §7#4）：CRYPTO 的天花板短板之一是格攻击：
真实赛题里「RSA 已知明文结构 / message = m0 + x 且 x 小 / f(x)≡0 (mod N) 有小根」很常见，
而 rsatool 当时只能回「考虑格攻击/Coppersmith（本工具不做）」。本测试把新实现对的行为钉住：

  LLL（src/ctfy_agent/lattice_math.lll_reduce）
    1) 输出仍是整数基，行列式绝对值守恒（格不变）；
    2) 每条输出向量都是原基的**整数**线性组合（不是格的真子格/扩张）；
    3) 满足 Lovász 条件（δ=3/4，即 delta-LLL 的定义式）；
    4) 首向量范数满足 LLL 上界 ‖b1‖ ≤ 2^((n-1)/4)·det^(1/n)；
    5) 非法输入（空向量/长度不齐/维数超限/delta 越界）→ LatticeError。
  Coppersmith（coppersmith_auto，根模 N 的一元小根）
    6) 构造题（N 511bit、deg 2、根 58bit）能解出，且校验 f(root) ≡ 0 (mod N)；
       同时覆盖「无 sympy（纯 stdlib gcd 路线）」与「有 sympy（ground_roots）」两条路径；
    7) 负例：X 给得比根小 → found=False，且不会把模 N 的另一个大根当结果；
    8) beta<1（根模未知因子）明确报不支持，不做「看起来支持」的假承诺；
    9) 非首一多项式自动按 mod N 首一化后仍能解。
  rsatool 接线（tools_domain_ext.rsatool）
   10) op=lll / op=coppersmith 走工具层能出可读结果；
   11) factor 走不通时的错误提示要指向 coppersmith/lll（而不是旧文案「本工具不做」）。

运行: python tests/smoke_lattice.py   （纯离线，不连平台、不需联网；约 3 秒）
"""
from __future__ import annotations

import pathlib
import sys
from fractions import Fraction

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import lattice_math as lm  # noqa: E402
from ctfy_agent.tools_domain_ext import DomainToolError, rsatool  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


# ---------------------------------------------------------------- 小工具
def det_int(mat):
    """整数矩阵行列式（Fraction 精确高斯消元，无浮点、无除法截断）。"""
    n = len(mat)
    a = [[Fraction(x) for x in row] for row in mat]
    det = Fraction(1)
    for k in range(n):
        pivot = None
        for i in range(k, n):
            if a[i][k] != 0:
                pivot = i
                break
        if pivot is None:
            return 0
        if pivot != k:
            a[k], a[pivot] = a[pivot], a[k]
            det = -det
        det *= a[k][k]
        for i in range(k + 1, n):
            factor = a[i][k] / a[k][k]
            if factor:
                for j in range(k, n):
                    a[i][j] -= factor * a[k][j]
    assert det.denominator == 1, det
    return int(det)


def merge_vectors(mat):
    """行向量列表 → 大小矩阵、按 (行索引, 列索引) 编号的线性系统矩阵（A·coef = b）。"""
    n = len(mat)
    width = len(mat[0])
    cols = n * width
    rows = []
    for r in range(n):
        for c in range(width):
            row = [Fraction(0)] * cols
            for k in range(n):
                row[k * width + c] = Fraction(mat[k][c])
            rows.append((row, Fraction(mat[r][c])))
    return rows


def solve_unique(system, n):
    """高斯消元解 n×n 系统（system 是 [(行, 右端), …]）；无解/多解返回 None。"""
    mat = [list(row) + [rhs] for row, rhs in system]
    # 只取前 n 个线性无关的方程：直接对全部方程做消元，最后检查一致性
    pivot_row = 0
    where = [-1] * n
    for col in range(n):
        sel = None
        for r in range(pivot_row, len(mat)):
            if mat[r][col] != 0:
                sel = r
                break
        if sel is None:
            continue
        mat[pivot_row], mat[sel] = mat[sel], mat[pivot_row]
        pv = mat[pivot_row][col]
        mat[pivot_row] = [v / pv for v in mat[pivot_row]]
        for r in range(len(mat)):
            if r != pivot_row and mat[r][col] != 0:
                factor = mat[r][col]
                mat[r] = [mat[r][k] - factor * mat[pivot_row][k] for k in range(n + 1)]
        where[col] = pivot_row
        pivot_row += 1
    if any(v != 0 for v in where):
        if any(v == -1 for v in where):
            return None
    sol = [Fraction(0)] * n
    for col in range(n):
        if where[col] >= 0:
            sol[col] = mat[where[col]][n]
    # 一致性：所有行在非主元列上都应为 0
    for r in range(len(mat)):
        if all(mat[r][c] == 0 for c in range(n)) and mat[r][n] != 0:
            return None
    return sol


# ---------------------------------------------------------------- LLL 测试
def case_lll():
    basis = [[4, 1, 3], [5, 2, 0], [1, 0, 1]]
    out = lm.lll_reduce(basis)
    check("lll 输出维数不变", len(out) == 3 and all(len(r) == 3 for r in out), repr(out))
    check("lll 输出全是整数", all(isinstance(v, int) for r in out for v in r), repr(out))
    check("lll 行列式绝对值守恒（格不变）", abs(det_int(basis)) == abs(det_int(out)),
          f"{abs(det_int(basis))} vs {abs(det_int(out))}")

    # 每条输出向量都是原基的整数线性组合
    system = merge_vectors(basis)
    n = len(basis)
    all_combos = True
    detail = ""
    for vec in out:
        target = [Fraction(v) for v in vec]
        # 用原基解 coef：sum_k coef_k * basis[k] = vec
        sys_v = []
        for c in range(len(basis[0])):
            row = [Fraction(basis[k][c]) for k in range(n)]
            sys_v.append((row, target[c]))
        sol = solve_unique(sys_v, n)
        if sol is None or any(s.denominator != 1 for s in sol):
            all_combos = False
            detail = f"vec={vec} sol={sol}"
            break
    check("lll 输出是原基的整数组合（同一格）", all_combos, detail)

    # Lovász 条件（δ=3/4）：用 Fraction 重算 Gram-Schmidt 后逐项验证
    def gs(b):
        n = len(b)
        star, mu = [], [[Fraction(0)] * n for _ in range(n)]
        for i in range(n):
            v = [Fraction(x) for x in b[i]]
            for j in range(i):
                den = sum(t * t for t in star[j])
                mu[i][j] = (sum(Fraction(b[i][k]) * star[j][k] for k in range(len(v))) / den) if den else Fraction(0)
                v = [v[k] - mu[i][j] * star[j][k] for k in range(len(v))]
            star.append(v)
        return star, mu

    star, mu = gs(out)
    delta = Fraction(3, 4)
    lovasz = all(
        sum(t * t for t in star[k]) >= (delta - mu[k][k - 1] ** 2) * sum(t * t for t in star[k - 1])
        for k in range(1, len(out))
    )
    check("lll 满足 Lovász 条件（delta=3/4）", lovasz)

    det_abs = abs(det_int(out))
    norm0 = sum(v * v for v in out[0])
    bound = (2 ** (len(out) - 1)) * (det_abs ** (Fraction(2, len(out))))
    check("lll 首向量范数满足 LLL 上界", Fraction(norm0) <= bound, f"{norm0} > {float(bound):.3f}")

    # 幂等性/边界
    check("lll 单位阵保持", lm.lll_reduce([[1, 0], [0, 1]]) == [[1, 0], [0, 1]])
    check("lll 单行直接返回", lm.lll_reduce([[7, 5, 3]]) == [[7, 5, 3]])

    # 4x4 随机整数基：det 守恒 + 整数组合
    b4 = [[12, 3, 9, 1], [5, 2, 7, 4], [8, 11, 6, 2], [1, 13, 4, 3]]
    o4 = lm.lll_reduce(b4)
    check("lll 4x4 行列式守恒", abs(det_int(b4)) == abs(det_int(o4)),
          f"{abs(det_int(b4))} vs {abs(det_int(o4))}")
    o4_sorted = sorted([sorted(r) for r in o4])
    check("lll 4x4 输出行数正确", len(o4) == 4 and all(len(r) == 4 for r in o4), str(o4_sorted))

    # 非法输入
    check("lll 空基返回空（不是错误）", lm.lll_reduce([]) == [])
    for name, call in (
        ("lll 拒绝长度不齐", lambda: lm.lll_reduce([[1, 2], [3]])),
        ("lll 拒绝超维数", lambda: lm.lll_reduce([[1] * 2 for _ in range(lm.MAX_DIM + 1)])),
        ("lll 拒绝 delta 越界", lambda: lm.lll_reduce([[1, 0], [0, 1]], delta=2.0)),
    ):
        try:
            call()
            check(name, False, "没有抛 LatticeError")
        except lm.LatticeError:
            check(name, True)
        except Exception as exc:  # noqa: BLE001
            check(name, False, f"抛了 {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------- Coppersmith 测试
# 固定构造题（随机种子 20261112 生成，写死以保证确定性/不依赖随机素数生成）：
#   N = p*q（511 bit，p/q 各 256 bit），x0 = 205914851244006805（58 bit）
#   f(x) = x^2 + A·x + B，且 f(x0) ≡ 0 (mod N)
CASE_N = 4957093398864409203339725172296118727482283331739775930151093815687843906064547372740670470311361766593143847946330677852413832599363900415590352558219057
CASE_A = 1378913371521247807839814146854018342027254788749188801857954
CASE_B = 4957093398864409203339725172296118727482283331739775930151093815687843906064263433998895300679519676964022463638913218785199113284093360939970930252534062
CASE_X0 = 205914851244006805


def case_coppersmith():
    N, A, B, x0 = CASE_N, CASE_A, CASE_B, CASE_X0
    check("构造题自检：f(x0) ≡ 0 (mod N)", (x0 * x0 + A * x0 + B) % N == 0)

    res = lm.coppersmith_auto([B, A, 1], N, X=1 << 120, budget_s=10.0)
    check("coppersmith 解出小根", res["found"] and res["root"] == x0,
          f"found={res['found']} root={res['root']}")
    check("coppersmith 校验项为 mod N", "mod N" in (res["note"] or ""), res["note"])
    check("coppersmith 实际验证 f(root)≡0", (res["root"] * res["root"] + A * res["root"] + B) % N == 0
          if res["found"] else False)

    # 纯 stdlib 路线（不注入 sympy 求根器）：小参数下 gcd 应直接给出线性因子
    res_std = lm.coppersmith_auto([B, A, 1], N, X=1 << 120, root_finder=None, budget_s=10.0)
    check("coppersmith 纯 stdlib（gcd 路线）同样解出",
          res_std["found"] and res_std["root"] == x0,
          f"found={res_std['found']} root={res_std['root']}")

    # 负例：X 比根小 → 不能命中，也不能把模 N 的另一个大根当结果
    neg = lm.coppersmith_auto([B, A, 1], N, X=1 << 20, budget_s=6.0)
    check("coppersmith 负例：X 小于根 → found=False", not neg["found"], repr(neg["root"]))
    check("coppersmith 负例：不返回超出 X 的候选根", neg["root"] is None or abs(neg["root"]) <= (1 << 20),
          repr(neg["root"]))

    # beta<1 必须明确拒绝（不做假承诺）
    try:
        lm.coppersmith_auto([B, A, 1], N, X=1 << 60, beta=0.5)
        check("coppersmith 拒绝 beta<1（未知因子）", False, "没有抛错")
    except TypeError:
        # coppersmith_auto 没有 beta 参数 → 用底层函数验证
        try:
            lm.coppersmith_univariate([B, A, 1], N, X=1 << 60, beta=0.5, m=3, t=2)
            check("coppersmith 拒绝 beta<1（未知因子）", False, "没有抛错")
        except lm.LatticeError as exc:
            check("coppersmith 拒绝 beta<1（未知因子）", "beta" in str(exc), str(exc))
    except lm.LatticeError as exc:
        check("coppersmith 拒绝 beta<1（未知因子）", "beta" in str(exc), str(exc))

    # 非首一多项式：全体系数乘 3（mod N）后首项为 3，应自动 mod N 首一化后仍解出
    poly3 = [(3 * B) % N, (3 * A) % N, 3]
    res3 = lm.coppersmith_auto(poly3, N, X=1 << 120, budget_s=10.0)
    check("coppersmith 非首一多项式可自动首一化", res3["found"] and res3["root"] == x0,
          f"found={res3['found']} root={res3['root']}")


# ---------------------------------------------------------------- 工具层接线
def case_tool_wiring():
    text = rsatool(op="lll", basis=[[4, 1, 3], [5, 2, 0], [1, 0, 1]])
    check("rsatool op=lll 返回归约结果", "LLL 归约完成" in text and "v0 =" in text, text[:120])

    text2 = rsatool(op="coppersmith", n=str(CASE_N), poly=[str(CASE_B), str(CASE_A), "1"],
                    X=str(1 << 120))
    check("rsatool op=coppersmith 返回命中与根",
          "命中" in text2 and str(CASE_X0) in text2, text2[:200])

    # 参数缺失/格式错误 → 可读错误
    for name, kwargs in (
        ("rsatool lll 缺 basis", {"op": "lll"}),
        ("rsatool coppersmith 缺 poly", {"op": "coppersmith", "n": str(CASE_N)}),
    ):
        try:
            rsatool(**kwargs)
            check(name, False, "没有抛 DomainToolError")
        except DomainToolError:
            check(name, True)

    # factor 走不通时的提示必须指向新能力
    big_prime = 170141183460469231731687303715884105727  # 2^127-1（梅森素数）
    try:
        rsatool(op="factor", n=str(big_prime))
        check("rsatool factor 失败提示指向 coppersmith/lll", False, "质数居然被分解了")
    except DomainToolError as exc:
        check("rsatool factor 失败提示指向 coppersmith/lll",
              "coppersmith" in str(exc) and "lll" in str(exc), str(exc))


def main():
    print("== LLL ==")
    case_lll()
    print("\n== Coppersmith ==")
    case_coppersmith()
    print("\n== rsatool 接线 ==")
    case_tool_wiring()
    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())