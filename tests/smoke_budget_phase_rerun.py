#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_budget_phase_rerun.py — 重跑时预算阶段按「本轮额度」折算的回归（离线）。

背景（2026-11-11 本地 plat 第 5 轮实测）：预算按题跨轮继承（费用刹车，有意），但阶段提示
也跟着累计走 —— SAMPLE-003 开局累计 756,249/800,000（95%），协调者第一步就收到
「收口期：禁止开辟新攻击面」，只重测一下就 done；实例配额已修好也没用。

修法：`BudgetPhase.line(budget, base_total, base_calls)`，重跑时用**本轮可用额度**
（cap - base_total）做分母；累计值只显示、不压死阶段；硬拦仍是 spending.check()。
本文件钉住：
  - base=0 时输出与旧版一致（单轮首跑零行为变化）；
  - 重跑时按本轮额度分档（10% → 探索期，95% → 收口期）；
  - 累计值仍显示，已烧穿（base ≥ cap）判收口；
  - crew 协调者接线真的传了基线（防回退）。

运行: python tests/smoke_budget_phase_rerun.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.progress import BudgetPhase  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


class _B:
    """最小 Budget 替身：line() 只读 total_tokens / calls / max_tokens / seconds_left。"""

    def __init__(self, used: int, calls: int, cap: int) -> None:
        self.total_tokens = used
        self.calls = calls
        self.max_tokens = cap
        self.seconds_left = None


def main() -> int:
    cap = 500_000

    # 1) 零行为变化：base=0 与旧口径逐字一致（现有 smoke_progress 的断言也依赖这三行）
    line = BudgetPhase.line(_B(100_000, 8, cap))
    check("首跑 20% → 探索期（旧文案）",
          "探索期" in line and "20%" in line and "已用 100,000/500,000" in line and "本轮" not in line, line)
    line = BudgetPhase.line(_B(275_000, 22, cap))
    check("首跑 55% → 收敛期", "收敛期" in line and "55%" in line, line)
    line = BudgetPhase.line(_B(400_000, 32, cap))
    check("首跑 80% → 收口期", "收口期" in line and "80%" in line and "禁止开辟新攻击面" in line, line)

    # 2) 重跑、本轮才用 10%：不再被历史 95% 压成收口期
    line = BudgetPhase.line(_B(410_000, 32, cap), base_total=400_000, base_calls=30)
    check("重跑本轮 10% → 探索期（关键修复）", "探索期" in line, line)
    check("重跑显示本轮额度", "本轮 10,000/100,000" in line, line)
    check("重跑仍显示累计", "累计 410,000/500,000" in line, line)
    check("重跑文案带「本轮约剩」", "本轮约剩" in line, line)

    # 3) 重跑把本轮额度也烧到 95% → 收口期（不是无限放宽）
    line = BudgetPhase.line(_B(495_000, 60, cap), base_total=400_000, base_calls=30)
    check("重跑本轮 95% → 收口期", "收口期" in line, line)

    # 4) 已烧穿（base ≥ cap）：额度 0 → 收口期，不崩、不出负号
    line = BudgetPhase.line(_B(520_000, 70, cap), base_total=520_000, base_calls=60)
    check("已烧穿 → 收口期", line is not None and "收口期" in line, line)
    check("已烧穿无负数", "-" not in line.split("收口期")[0], line)

    # 5) 无上限/无墙钟仍是 None；base 参数不改变这条
    check("无上限 → None", BudgetPhase.line(_B(0, 0, 0)) is None)
    check("无上限 + base → None", BudgetPhase.line(_B(10, 1, 0), base_total=5, base_calls=1) is None)

    # 6) 接线防回退：crew 协调者必须传本次 solve 的基线
    crew_src = (ROOT / "src" / "ctfy_agent" / "crew.py").read_text(encoding="utf-8")
    check("crew 传基线", "BudgetPhase.line(\n" in crew_src and "_base_total, _base_calls" in crew_src)
    solver_src = (ROOT / "src" / "ctfy_agent" / "solver.py").read_text(encoding="utf-8")
    check("solver 保持累计口径（不传基线）",
          solver_src.count("BudgetPhase.line(") >= 2 and "_base_total" not in solver_src)

    print()
    if FAILS:
        print(f"smoke_budget_phase_rerun 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("smoke_budget_phase_rerun 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())