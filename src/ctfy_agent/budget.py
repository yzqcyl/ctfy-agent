#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""费用/用量上限：防止模型跑飞烧钱。

三层刹车，任何一层触发都在**下一次调用模型之前**抛 BudgetExceeded：
  - token 累计上限（按题落盘，重跑同一题会接着算，不能靠重跑绕过）
  - LLM 调用次数上限
  - 金额上限（需先配单价；不配单价就只按 token 卡）

为什么把闸门放在「调用之前」：一次 chat 花多少由模型决定，只有调用后才知道。
所以在请求发出前检查已有用量，代价是最多多花一次调用的量（有界），
换来的是无论模型多能刷，都不可能无限烧钱。

集群-B（同题多次尝试）：在题级三层刹车之上，新增**尝试级切片**（attempt_tokens / attempt_calls）。
一次尝试耗尽切片即抛 BudgetExceeded(attempt=True) → 由编排器结束「本次尝试」并决定是否再试；
题级上限耗尽仍抛 attempt=False → 整个题不再尝试。默认切片为 0（=不限），行为与旧版完全一致。
"""

from __future__ import annotations


class BudgetExceeded(RuntimeError):
    """预算耗尽。带上一份快照，方便落盘和汇报。"""

    def __init__(self, reason: str, snapshot: dict, *, attempt: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.snapshot = snapshot
        self.attempt = attempt  # True=本次尝试切片超限（题内可再试）；False=题级硬闸门


class Budget:
    def __init__(
        self,
        *,
        max_tokens: int = 0,
        max_calls: int = 0,
        max_cost: float = 0.0,
        price_input: float = 0.0,
        price_output: float = 0.0,
        label: str = "",
        attempt_tokens: int = 0,
        attempt_calls: int = 0,
    ):
        self.max_tokens = max(0, int(max_tokens or 0))       # 0 = 不限
        self.max_calls = max(0, int(max_calls or 0))         # 0 = 不限
        self.max_cost = max(0.0, float(max_cost or 0.0))     # 0 = 不限
        self.price_input = max(0.0, float(price_input or 0.0))    # 每百万 token 单价
        self.price_output = max(0.0, float(price_output or 0.0))
        self.label = label
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        # 集群-B：尝试级切片（0=不限）。_attempt_used_* 是本次尝试已用，reset_attempt 时清零。
        self.attempt_tokens = max(0, int(attempt_tokens or 0))
        self.attempt_calls = max(0, int(attempt_calls or 0))
        self._attempt_used_tokens = 0
        self._attempt_used_calls = 0

    # ---------- 读数 ----------

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * self.price_input
            + self.completion_tokens / 1_000_000 * self.price_output
        )

    @property
    def tokens_left(self) -> int | None:
        return None if not self.max_tokens else max(0, self.max_tokens - self.total_tokens)

    @property
    def cost_left(self) -> float | None:
        return None if self.max_cost <= 0 else max(0.0, self.max_cost - self.cost)

    # ---------- 续算（跨进程继承历史用量） ----------

    def load(self, snapshot: dict | None) -> "Budget":
        snapshot = snapshot or {}
        self.prompt_tokens = max(self.prompt_tokens, int(snapshot.get("prompt_tokens") or 0))
        self.completion_tokens = max(self.completion_tokens, int(snapshot.get("completion_tokens") or 0))
        self.calls = max(self.calls, int(snapshot.get("calls") or 0))
        return self

    def snapshot(self) -> dict:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "calls": self.calls,
            "cost": round(self.cost, 6),
            "max_tokens": self.max_tokens,
            "max_calls": self.max_calls,
            "max_cost": self.max_cost,
            "attempt_tokens": self.attempt_tokens,
            "attempt_calls": self.attempt_calls,
            "attempt_used_tokens": self._attempt_used_tokens,
            "attempt_used_calls": self._attempt_used_calls,
        }

    # ---------- 集群-B：尝试级切片 ----------

    def compute_attempt_caps(self, remaining_attempts: int, *, min_calls: int = 4, reserve: float = 0.12) -> dict:
        """按「题级剩余 × (1-reserve) ÷ 剩余尝试数」算本次尝试的切片。0 表示该维度不设切片。"""
        k = max(1, int(remaining_attempts))
        t = 0
        if self.max_tokens:
            t = int((self.max_tokens - self.total_tokens) * (1 - reserve) / k)
        c = 0
        if self.max_calls:
            c = max(min_calls, int((self.max_calls - self.calls) * (1 - reserve) / k))
        return {"tokens": max(0, t), "calls": max(0, c)}

    def set_attempt_caps(self, caps: dict | None) -> None:
        caps = caps or {}
        self.attempt_tokens = max(0, int(caps.get("tokens") or 0))
        self.attempt_calls = max(0, int(caps.get("calls") or 0))
        self._attempt_used_tokens = 0
        self._attempt_used_calls = 0

    def reset_attempt(self, caps: dict | None = None) -> None:
        """开始一次新尝试：可选设置新切片，并清零本次尝试已用。"""
        if caps is not None:
            self.set_attempt_caps(caps)
        else:
            self._attempt_used_tokens = 0
            self._attempt_used_calls = 0

    def attempt_exceeded(self) -> str | None:
        """本次尝试切片超限原因；未超返回 None。"""
        if self.attempt_calls and self._attempt_used_calls >= self.attempt_calls:
            return f"本次尝试调用次数达切片上限 {self.attempt_calls} 次"
        if self.attempt_tokens and self._attempt_used_tokens >= self.attempt_tokens:
            return f"本次尝试 token 达切片上限 {self.attempt_tokens}（当前 {self._attempt_used_tokens}）"
        return None

    # ---------- 闸门 ----------

    def exceeded(self) -> str | None:
        """题级超限原因；未超返回 None。"""
        if self.max_calls and self.calls >= self.max_calls:
            return f"模型调用次数已达上限 {self.max_calls} 次"
        if self.max_tokens and self.total_tokens >= self.max_tokens:
            return f"token 用量已达上限 {self.max_tokens}（当前 {self.total_tokens}）"
        if self.max_cost > 0 and self.cost >= self.max_cost:
            return f"费用已达上限 {self.max_cost:.4f}（当前 {self.cost:.4f}）"
        return None

    def check(self) -> None:
        # 尝试级切片优先：命中表示「本次尝试该停了」，但题还有后续尝试机会
        a = self.attempt_exceeded()
        if a:
            raise BudgetExceeded(f"{self.label + '：' if self.label else ''}{a}", self.snapshot(), attempt=True)
        reason = self.exceeded()
        if reason:
            raise BudgetExceeded(f"{self.label + '：' if self.label else ''}{reason}", self.snapshot())

    def record(self, usage: dict | None) -> None:
        usage = usage or {}
        pt = int(usage.get("prompt_tokens") or 0)
        ct = int(usage.get("completion_tokens") or 0)
        self.prompt_tokens += pt
        self.completion_tokens += ct
        self.calls += 1
        self._attempt_used_tokens += pt + ct
        self._attempt_used_calls += 1

    # ---------- 展示 ----------

    def describe(self) -> str:
        def cap(limit, unit=""):
            return f"{limit}{unit}" if limit else "不限"

        money = f"{self.cost:.4f} / {cap(self.max_cost)}" if (self.max_cost or self.price_input or self.price_output) else "未计价"
        return (
            f"token {self.total_tokens} / {cap(self.max_tokens)}  "
            f"调用 {self.calls} / {cap(self.max_calls)}  "
            f"费用 {money}"
        )