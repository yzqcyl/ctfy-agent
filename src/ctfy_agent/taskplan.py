# -*- coding: utf-8 -*-
"""taskplan.py — 赛程级「排序 + 按题预算」纯函数（正式赛主跑/兜底共用）。

背景（2026-11-16，真事故 + 真 bug）：
1. GUI 批量解题只按难度给**固定墙钟**（easy 10 / medium 25 / hard 40 / 未知 20 分钟），
   没有"钱怎么分"的概念：关闭场次里 5 题照样跑满 20 分钟、烧 338 万 token（题根本不可玩）。
2. 自主主控（supervisor）虽然算了每题的 `quota_tokens`/`deadline`，但 `default_solver`
   没把它们传给 `solve_many`（死参数），实际仍按全局 `max_tokens_per_challenge`（默认 800k）
   烧钱 —— 两条路都做不到"按题预算硬约束"。

本模块只做纯计算（无网络、无状态、可单测），供两处调用：
- `gui/platform_worker.solve_many()`：开跑前按 EV 排序 + 给每题算 quota，落成
  `config.max_tokens_per_challenge` 与单题 deadline；
- `runs/_tpctf/supervisor.py`：已有 `policy.rank/allocate`（本题仍是自主主跑），修复透传
  之后由本模块的难度 cap 作为单题上限兜底。

用户 2026-11-16 决定：**token 总池不限**（自己的 key）→ 默认 `pool=None`，quota 只由
"难度 cap × near-miss 加成"决定；仍要单题封顶，保证 10 小时里能覆盖到所有会做的题。
"""
from __future__ import annotations

from typing import Any, Iterable

# 单题 token 上限（难度 cap）。这是"最多给一题烧多少"，不是"预期用量"；
# 预计用量见 EST_TOKENS（只进 EV 分母，不影响真实上限）。
DIFFICULTY_CAPS: dict[str, int] = {"easy": 800_000, "medium": 800_000, "hard": 800_000}
FALLBACK_CAP = 800_000

# EV 分母用的"预计开销"（沿用 policy.py 的难度时长 × 每分钟 token 直觉）。
EST_TOKENS: dict[str, int] = {"easy": 800_000, "medium": 800_000, "hard": 800_000}
FALLBACK_EST = 300_000

# 已有候选/near-miss 的题加注 50%（不突破难度 cap × 1.5）
NEAR_MISS_BONUS = 1.5
# 赛程过半后仍全服 0 解的题强降权（不硬排除；用户 2026-11-16 决定暂不放弃清单）
ZERO_SOLVE_PENALTY_AFTER = 0.40

_DIFF_ALIASES = {
    "easy": "easy", "简单": "easy",
    "medium": "medium", "mid": "medium", "中等": "medium",
    "hard": "hard", "困难": "hard",
}


def normalize_difficulty(value: Any) -> str:
    """宽松归一到 easy/medium/hard；认不出返回空串（调用方走 fallback）。"""
    text = str(value or "").strip().lower()
    return _DIFF_ALIASES.get(text, "")


def share_fraction(row: dict) -> float:
    """这题**还没拿到**的分值占比（0~1），供 EV 用。

    - 有 `questions`（含 share_percent）→ 未解出的题号 share 之和 / 总 share；
    - 无 questions 数据 → 已整题解出返回 0，否则 1（粗算，不会把已解题重排到前面）。
    """
    questions = row.get("questions") or []
    solved = set()
    for key in ("my_solved_question_ids", "solved_question_ids", "verified_qids",
                "answers_by_qid"):
        value = row.get(key)
        if isinstance(value, dict):
            solved.update(str(k) for k in value)
        elif isinstance(value, (list, tuple, set)):
            solved.update(str(v) for v in value)
    total = 0.0
    remaining = 0.0
    for question in questions:
        if not isinstance(question, dict):
            continue
        try:
            share = float(question.get("share_percent", 100) or 0)
        except (TypeError, ValueError):
            share = 100.0
        share = max(share, 0.0)
        total += share
        qid = str(question.get("id") or "")
        if qid and qid in solved:
            continue
        remaining += share
    if total <= 0:
        return 0.0 if row.get("my_fully_solved") else 1.0
    return max(0.0, min(1.0, remaining / total))


def solve_probability(row: dict, *, elapsed_ratio: float = 0.0) -> float:
    """先验解出概率：难度基线 × 全服解出数曲线；赛程过半仍 0 解 → 强降权。"""
    base = {"easy": 0.85, "medium": 0.60, "hard": 0.35}.get(
        normalize_difficulty(row.get("difficulty")), 0.5)
    solves = row.get("solves_count")
    try:
        solves_value = float(solves) if solves is not None else None
    except (TypeError, ValueError):
        solves_value = None
    if solves_value is None:
        curve = 0.5                       # 字段缺失：按中间值，不猜
    else:
        curve = max(0.0, min(solves_value / 50.0, 1.0))
    prob = base * (0.35 + 0.65 * curve)
    if (solves_value is not None and solves_value <= 0
            and elapsed_ratio >= ZERO_SOLVE_PENALTY_AFTER):
        # 赛程过半仍全服 0 解：强降权到基线的 15%（不硬排除，用户 2026-11-16 决定）
        prob *= 0.15
    return max(0.01, min(1.0, prob))


def expected_value(row: dict, *, elapsed_ratio: float = 0.0) -> float:
    """期望分 / 预计 token（已解/无剩余分返回 -1，排序时自然沉底）。"""
    share = share_fraction(row)
    if share <= 0:
        return -1.0
    try:
        points = float(row.get("points") or 100)
    except (TypeError, ValueError):
        points = 100.0
    points = max(points, 1.0)
    est = EST_TOKENS.get(normalize_difficulty(row.get("difficulty")), FALLBACK_EST)
    # ×1e6 只是把数值放大到便于阅读/断言的量级
    return points * share * solve_probability(row, elapsed_ratio=elapsed_ratio) / est * 1e6


def rank(rows: Iterable[dict], *, elapsed_ratio: float = 0.0) -> list[dict]:
    """按 EV 降序；同 EV 时题号少的优先（收口成本低）。已解的题沉底。"""
    def key(row: dict) -> tuple[float, float]:
        return (expected_value(row, elapsed_ratio=elapsed_ratio),
                -float(len(row.get("questions") or [])))

    return sorted(rows, key=key, reverse=True)


def token_cap(row: dict, *, near_miss: bool = False) -> int:
    """单题 token 上限：难度 cap（near-miss 加注 50%，仍不突破 cap×1.5）。"""
    cap = DIFFICULTY_CAPS.get(normalize_difficulty(row.get("difficulty")), FALLBACK_CAP)
    if near_miss:
        # near-miss 加注 50%，但**任何情况下单题不超过 800k**（2026-11-18 用户定：预算全开 800k）
        cap = min(int(round(cap * NEAR_MISS_BONUS)), 800_000)
    return int(cap)


def plan(rows: Iterable[dict], *, pool: float | None = None, reserve_ratio: float = 0.1,
         elapsed_ratio: float = 0.0, slots: int | None = None,
         near_miss_ids: Iterable[str] | None = None) -> list[dict]:
    """给出执行顺序 + 每题 token 上限。

    返回 ``[{"id", "order", "quota", "ev"}, ...]``（EV 降序）。
    - ``pool`` 为 None/<=0（正式赛默认，用户用自己的 key 不限池）→ quota 只受难度 cap 约束；
    - ``pool`` > 0 → 在可用池内按 EV 分摊（前 2 题 60%、其余 40%，预留 reserve_ratio）；
    - ``slots`` 给了就只返回前 slots 题（其余题本轮不投）。

    注意：``pool`` 分摊只是"上限"分配，实际消耗仍由 Crew 的累计预算硬拦（不会超花）。
    """
    ordered = rank(rows, elapsed_ratio=elapsed_ratio)
    if slots is not None:
        ordered = ordered[:max(0, int(slots))]
    if not ordered:
        return []
    near = {str(x) for x in (near_miss_ids or ())}
    evs = [max(expected_value(row, elapsed_ratio=elapsed_ratio), 1e-9) for row in ordered]
    quotas: list[int] = []
    if pool is not None and float(pool) > 0:
        usable = float(pool) * (1.0 - max(0.0, min(1.0, float(reserve_ratio))))
        total_ev = sum(evs) or 1.0
        if len(ordered) <= 2:
            weights = [ev / total_ev for ev in evs]
        else:
            rest_total = sum(evs[2:]) or 1.0
            weights = [0.3, 0.3] + [0.4 * ev / rest_total for ev in evs[2:]]
        for index, row in enumerate(ordered):
            cap = token_cap(row, near_miss=str(row.get("id") or "") in near)
            quotas.append(max(0, min(cap, int(usable * weights[index]))))
    else:
        for row in ordered:
            quotas.append(token_cap(row, near_miss=str(row.get("id") or "") in near))
    return [
        {"id": str(row.get("id") or ""), "order": index, "quota": quotas[index],
         "ev": round(evs[index], 3)}
        for index, row in enumerate(ordered)
    ]
