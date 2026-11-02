#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量编排：无人值守地把一批题跑完。

比赛开始后不允许人工干预，所以必须有一个"跑一批、自己扛住失败、花超了就停"的驱动。
这里刻意做成纯编排：不碰平台、不碰模型，只依赖 store 和注入的 solve 回调，
因此可以完全离线测试。

三条兜底：
  1. 单题异常不中断整批（记失败、继续下一题）
  2. 全局 token 上限（跨题累计），剩余额度会动态收紧下一题的单题上限
  3. 连续失败超过阈值就停，避免在坏环境下空烧
"""

from __future__ import annotations

from typing import Callable, Iterable

DEFAULT_STATUSES = ("new", "parsed", "instance_up", "stalled")

# 难度兜底：平台没给 difficulty 时按这个排（越小越先打）
_DIFFICULTY_RANK = {"easy": 0, "1": 0, "medium": 1, "2": 1, "hard": 2, "3": 2}


def _score(challenge) -> tuple:
    """选题优先级（越小越先打）。依据：实战里「已解队伍数」是免费且最准的难度指标。

    排序口径（P2-1）：
      1. **solve 数降序** —— 200 队解出的题远比 3 队解出的题更可能在预算内拿下；
      2. 分值降序 —— 动态计分赛事里高分题越早交越值钱；
      3. 难度升序 —— 平台标了 easy 的先打；
      4. 题号升序 —— 兜底确定性（同分同难度时不要每跑一次换一个顺序）。
    缺 solve 数（None）按"中等"处理，不给它编排上的特权也不歧视。
    """
    solves = getattr(challenge, "solves_count", None)
    solves_key = -1 if solves is None else -int(solves)   # 有 solve 数的排前面，且多的更前
    points = int(getattr(challenge, "points", 0) or 0)
    rank = _DIFFICULTY_RANK.get(str(getattr(challenge, "difficulty", "") or "").lower(), 1)
    return (solves_key, -points, rank, str(getattr(challenge, "id", "")))


def _select(store, statuses: Iterable[str], limit: int, order: str = "score") -> list:
    wanted = set(statuses)
    rows = [c for c in store.challenges.values() if getattr(c, "status", None) in wanted]
    if order == "id":
        rows.sort(key=lambda c: getattr(c, "id", ""))
    else:
        rows.sort(key=_score)
    if limit and limit > 0:
        return rows[:limit]
    return rows


def run_auto(
    *,
    store,
    solve: Callable[[str], dict],
    statuses: Iterable[str] = DEFAULT_STATUSES,
    limit: int = 0,
    max_total_tokens: int = 0,
    max_failures: int = 3,
    order: str = "score",
    on_event: Callable[[str, dict], None] | None = None,
) -> dict:
    """依次解题。solve(challenge_id) 返回带 budget/usage 的摘要。"""
    emit = on_event or (lambda kind, payload=None: None)
    rows = _select(store, statuses, limit, order)
    emit("plan", {"candidates": [getattr(c, "id", "") for c in rows]})

    results: list[dict] = []
    spent = 0
    submitted = 0
    failed = 0
    consecutive_failures = 0
    stopped = "exhausted"

    for challenge in rows:
        cid = getattr(challenge, "id", "")
        if max_total_tokens and spent >= max_total_tokens:
            stopped = "total_budget"
            emit("stop", {"reason": stopped, "spent": spent})
            break

        try:
            summary = solve(cid) or {}
        except Exception as exc:  # noqa: BLE001 - 单题失败不能拖垮整批
            failed += 1
            consecutive_failures += 1
            results.append({"challenge_id": cid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
            emit("failed", {"challenge_id": cid, "error": str(exc)})
            if consecutive_failures >= max_failures:
                stopped = "too_many_failures"
                emit("stop", {"reason": stopped, "failures": failed})
                break
            continue

        consecutive_failures = 0
        # 用本轮实际消耗（tokens_spent delta），而不是跨 run 累计值，否则重试会重复计入（审计 B9）
        used = int(summary.get("tokens_spent") or (summary.get("budget") or {}).get("total_tokens") or 0)
        spent += used
        if summary.get("submitted"):
            submitted += 1
        results.append(
            {
                "challenge_id": cid,
                "ok": bool(summary.get("submitted")),
                "reason": summary.get("reason"),
                "tokens": used,
                "steps": summary.get("steps"),
            }
        )
        emit("done", {"challenge_id": cid, "submitted": bool(summary.get("submitted")), "tokens": used, "spent": spent})

    return {
        "planned": len(rows),
        "attempted": len(results),
        "submitted": submitted,
        "failed": failed,
        "spent": spent,
        "stopped": stopped,
        "results": results,
    }
