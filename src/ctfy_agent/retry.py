#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""集群-B 编排器：同题多次独立尝试 + 极简共享黑板（零耦合编排库）。

设计目标：把「多次尝试编排 / 两段式归因 / 黑板继承 / 差异化 / 早停」立成独立模块，
**不 import 任何生产兄弟模块**——求解能力通过 `attempt_fn` 回调注入，预算切片通过 `caps_fn`
回调注入（真实切片由 `budget.Budget.compute_attempt_caps` 提供，本模块不持有账本）。

接口契约（solver 接入点见 solver.Solver.solve）：
  - `attempt_fn: (cid, index, caps, prompt_inject, meta) -> AttemptOutcome`
    · caps 为 None 表示本次尝试不切片（题级闸门仍生效）
    · meta = {"strategy": 打法卡, "facts": 黑板 facts list}（facts 供 FactsBoard.seed 继承）
  - `caps_fn: (remaining_attempts) -> dict|None`，由调用方按「题级剩余」算尝试切片
  - `precheck_fn: (cid) -> bool`，接 my_solved_ids / status 预检，成本≈0

归因语义（classify_failure）与 solver 的机制 reason 对齐：
  - flag_accepted/exploit_verified → solved
  - budget_exhausted（题级总预算真耗尽）→ stopped（不再尝试）
  - attempt_cap（本次尝试切片用尽、题级还有配额）→ near_miss（尾步近目标）/ retryable
  - env_bound/capability_gap tag → hopeless（环境/能力硬缺口，不白烧钱）

设计依据：[docs/plan-concurrency.md](../docs/plan-concurrency.md)；spec-by-example：runs/_clusterb_fixtures.py
纪律：本模块 0 LLM、0 平台调用、0 文件写（除 __main__ 自检的 stdout）。

安全红线（调用方必须遵守，本模块只负责约定）：
  - attempt 内失败**不得**污染 ledger 的「方向证伪」状态（[ledger.py:347] 多轮诱导必拒坑）——继承事实、不继承判决。
  - 有状态题尝试**永远串行**（同实例并行会互相污染致假失败）；并行只用于无状态题（集群-A 范畴，不在本模块）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------- 常量 ---------------------------------------------------------------------------

SOLVED_REASONS = {"flag_accepted", "exploit_verified"}   # 解出（含 proof-of-exploitation，platform 记 solve）
NEAR_ACTIONS = {"verify_exploit", "verify_answer", "submit_flag"}   # 尾步逼近目标 → near_miss
HOPELESS_TAGS = {"env_bound", "capability_gap"}          # 环境/能力硬缺口 → 不重试
STOP_REASONS = {"budget_exhausted"}                      # 题级总预算真耗尽 → 停（区别于 attempt_cap 切片）

# 三档差异化（策略 / 温度 / 首动作）。crew 接入后据此选打法卡；温度仅作用「决策步」，tool_calls 参数仍结构化。
STRATEGY_SPECS = {
    0: {"card_hint": "默认 playbook 首卡", "tool_surface": "默认", "first_action": "题面默认入口", "temperature": 0.2},
    1: {"card_hint": "次优卡 + 换工具面(http_request↔shell/curl)", "tool_surface": "换工具面", "first_action": "侦察面(/docs、robots、错误页)", "temperature": 0.5},
    2: {"card_hint": "反推视角(flag 位置/端点枚举/编码形态 ⑥⑦⑧)", "tool_surface": "反推", "first_action": "构造面(畸形输入/边界探测)", "temperature": 0.8},
}

# --------------------------------------------------------------------------- 归因 ---------------------------------------------------------------------------

def classify_failure(reason: str, tail_action: str = "", tags=()) -> str:
    """机制 reason + 尾步动作 + env tag → 语义分类。

    返回之一：solved / stopped / hopeless / near_miss / retryable。
    """
    if reason in SOLVED_REASONS:
        return "solved"
    if any(t in HOPELESS_TAGS for t in (tags or ())):
        return "hopeless"
    if reason in STOP_REASONS:
        return "stopped"
    if reason in ("attempt_cap", "step_limit") and tail_action in NEAR_ACTIONS:
        return "near_miss"
    return "retryable"


# --------------------------------------------------------------------------- 数据载体 ---------------------------------------------------------------------------

@dataclass
class AttemptOutcome:
    """一次尝试的返回契约（由 attempt_fn 生产）。"""
    reason: str
    tail_action: str = ""
    tags: tuple = ()
    tokens: int = 0
    calls: int = 0
    facts: list = field(default_factory=list)          # 已确认事实（可复用）
    dead_ends: list = field(default_factory=list)      # 已证伪路径（勿重复）
    candidates: list = field(default_factory=list)     # 待验候选值（复用于校验/提交）
    extra: dict = field(default_factory=dict)


@dataclass
class AttemptRecord:
    index: int
    strategy: dict
    caps: dict | None
    reason: str
    semantic: str
    prompt_has_blackboard: bool


@dataclass
class ChallengeReport:
    challenge_id: str
    solved: bool
    attempts: list = field(default_factory=list)
    blocked: str = ""                                   # hopeless / budget / ""
    tokens_spent: int = 0
    calls_spent: int = 0


# --------------------------------------------------------------------------- 黑板 ---------------------------------------------------------------------------

class Blackboard:
    """极简事实黑板：只共享「事实 + 死路 + 候选值」，不共享完整轨迹（防锚定）。"""

    def __init__(self):
        self.facts: list = []
        self.dead_ends: list = []
        self.candidates: list = []

    def merge(self, outcome: AttemptOutcome) -> None:
        for f in outcome.facts:
            if f not in self.facts:
                self.facts.append(f)
        for d in outcome.dead_ends:
            if d not in self.dead_ends:
                self.dead_ends.append(d)
        for c in outcome.candidates:
            if c not in self.candidates:
                self.candidates.append(c)

    def inject(self) -> str:
        # 三段各自限长（设计 §7：800/400/300 字符），防止跨尝试累积把首发 brief 撑爆
        return "\n".join([
            "[前次尝试已确认事实] " + _clip_join(self.facts, FACTS_MAX_CHARS),
            "[前次尝试的死路（勿重复）] " + _clip_join(self.dead_ends, DEAD_ENDS_MAX_CHARS),
            "[待验候选值] " + _clip_join(self.candidates, CANDIDATES_MAX_CHARS),
        ])


# --------------------------------------------------------------------------- 黑板三段工具 ---------------------------------------------------------------------------

# 黑板三段注入的字符上限（设计 §7：事实 ≤800 / 死路 ≤400 / 候选 ≤300）
FACTS_MAX_CHARS = 800
DEAD_ENDS_MAX_CHARS = 400
CANDIDATES_MAX_CHARS = 300

# 「像 flag」的花括号形态：flag{...} / CTF{...} / 任意标识符前缀{...}。
# 前缀至少 1 个标识符字符、内容排除 `"`/`\`（JSON 对象/字符串特征）——否则会把 {"status":"ok"} 误当候选值。
_CAND_RE = re.compile(r"[A-Za-z0-9_]{1,24}\{[^{}\"\\\s]{3,120}\}")


def _clip_join(items, limit: int) -> str:
    """把列表拼成一段并限长；空列表渲染为「（无）」。"""
    if not items:
        return "（无）"
    text = "；".join(str(x) for x in items)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def extract_candidates(texts, limit: int = 6) -> list:
    """从工具输出文本里挑「看起来像 flag」的候选值（花括号形态），供黑板第三段。

    只做形态筛选，不做判真——判真由下一次尝试的 verify/submit 决定。
    """
    out: list = []
    for t in texts or []:
        for m in _CAND_RE.findall(str(t)):
            if m not in out:
                out.append(m[:160])
    return out[-limit:]


# --------------------------------------------------------------------------- 动态 k ---------------------------------------------------------------------------

def decide_k(tokens_pool: int, calls_pool: int, *, min_calls: int = 4, reserve: float = 0.12,
             remaining_seconds: float = 0.0, challenges_left: int = 1,
             avg_seconds_per_attempt: float = 1400.0, attempts_max: int = 3) -> int:
    """k = clamp(1..3, 时间允许的尝试数, 扣收口 reserve 后的 calls 池允许的尝试数)。

    现实口径：单次 attempt ~1400s、calls 池 20 且 reserve 12% 时 → 有效 calls≈17 → k≈2（少数 3）。
    calls 通常比 token 更早成为瓶颈；tokens_pool 保留为 token 维度扩展位。
    """
    k = attempts_max
    if remaining_seconds > 0 and challenges_left > 0:
        k = min(k, max(1, int(remaining_seconds / challenges_left / avg_seconds_per_attempt)))
    effective_calls = int(calls_pool * (1 - reserve))
    k = min(k, max(1, effective_calls // max(1, min_calls)))
    return max(1, k)


def resolve_k(cfg: dict, *, tokens_pool: int, calls_pool: int) -> int:
    """解析集群-B 的 k。

    语义：
      - 不写 `clusterb_k`（或写 0/空）→ **k=1**（旧版行为，零回归）；
      - `clusterb_k = "auto"` / `true` → 按**题级剩余预算**动态算（`decide_k`，calls 是主约束）；
      - 显式数字 → 尊重配置，原样返回（失败兜底 1）。
    """
    cfg = cfg or {}
    raw = cfg.get("clusterb_k")
    if raw is None or raw == "" or raw == 0 or raw is False:
        return 1
    if raw is True or (isinstance(raw, str) and raw.strip().lower() == "auto"):
        return decide_k(
            int(tokens_pool or 0),
            int(calls_pool or 0),
            min_calls=int(cfg.get("clusterb_min_calls") or 4),
            reserve=float(cfg.get("clusterb_reserve") or 0.12),
            attempts_max=int(cfg.get("clusterb_k_max") or 3),
        )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 1


# --------------------------------------------------------------------------- 编排器 ---------------------------------------------------------------------------

class Orchestrator:
    """逐题串行编排 k 次尝试。

    并发（无状态题并行、有状态题串行）属集群-A / 调度层职责，不在本模块——本模块对单题**始终串行**，
    天然符合「有状态题尝试间不并行」的安全约束。
    """

    def __init__(self, attempt_fn, *, attempts_max: int = 3, caps_fn=None, precheck_fn=None):
        """
        attempt_fn: (cid, index, caps, prompt_inject, meta) -> AttemptOutcome
        caps_fn: (remaining_attempts) -> dict|None，切片由调用方注入（如 spending.compute_attempt_caps）；
                 返回 None = 本次尝试不切片（题级闸门仍生效）
        precheck_fn: (cid) -> bool，返回 True 表示已解出/应跳过（接 my_solved_ids 预检，成本≈0）
        """
        self.attempt_fn = attempt_fn
        self.attempts_max = attempts_max
        self.caps_fn = caps_fn
        self.precheck_fn = precheck_fn

    def run_challenge(self, cid: str) -> ChallengeReport:
        report = ChallengeReport(challenge_id=cid, solved=False)
        if self.precheck_fn and self.precheck_fn(cid):
            report.solved = True
            report.attempts = [AttemptRecord(0, STRATEGY_SPECS[0], None, "already_solved", "solved", False)]
            return report

        bb = Blackboard()
        k = self.attempts_max

        for idx in range(k):
            caps = self.caps_fn(k - idx) if self.caps_fn else None   # 切片由外部按「题级剩余」算
            meta = {"strategy": STRATEGY_SPECS[idx % len(STRATEGY_SPECS)],
                    "facts": list(bb.facts),                          # 黑板事实随 meta 下传（供 FactsBoard.seed）
                    "dead_ends": list(bb.dead_ends),                  # 黑板死路随 meta 下传（供 _solve_once 注入勿重复）
                    "candidates": list(bb.candidates)}                # 黑板候选值随 meta 下传（供优先验证）
            prompt = bb.inject() if idx > 0 else ""                   # 纯文本注入（供其他渲染方；首尝试只看题面）
            out = self.attempt_fn(cid, idx, caps, prompt, meta)

            semantic = classify_failure(out.reason, out.tail_action, out.tags)
            report.attempts.append(AttemptRecord(idx, meta["strategy"], caps, out.reason,
                                                 semantic, prompt_has_blackboard=bool(prompt)))
            report.tokens_spent += out.tokens
            report.calls_spent += out.calls

            if semantic == "solved":
                report.solved = True
                bb.merge(out)                                 # 沉淀事实供复盘（不留判决）
                break

            bb.merge(out)

            if semantic == "hopeless":
                report.blocked = "hopeless"                   # 环境/能力硬缺口，不白烧钱
                break
            if semantic == "stopped":
                report.blocked = "budget"                     # 题级预算耗尽，不再尝试
                break

        return report


# --------------------------------------------------------------------------- 自检（__main__） ---------------------------------------------------------------------------

def _self_test() -> int:
    def fake_attempt(cid, idx, caps, prompt, meta):
        if cid == "easy":
            return AttemptOutcome(reason="flag_accepted", tokens=120_000, calls=8,
                                  facts=["flag 在 /flag.txt"], tail_action="submit_flag")
        if cid == "near_miss":
            if idx == 0:
                return AttemptOutcome(reason="attempt_cap", tokens=90_000, calls=7,
                                      facts=["存在 /kb、/docs；8 条路径全 200"],
                                      dead_ends=["默认 playbook 盲打 /debug 无效"],
                                      tail_action="verify_exploit", tags=("web",))
            return AttemptOutcome(reason="flag_accepted", tokens=40_000, calls=3,
                                  facts=["flag 在 /admin/export"],
                                  tail_action="verify_exploit",
                                  extra={"dead_end_seen": "盲打" in prompt})
        if cid == "budget":
            if idx == 0:
                return AttemptOutcome(reason="attempt_cap", tokens=80_000, calls=6, tail_action="http_request")
            return AttemptOutcome(reason="budget_exhausted", tokens=20_000, calls=2, tail_action="http_request")
        return AttemptOutcome(reason="no_more_actions", tokens=70_000, calls=6,
                              dead_ends=["探测被平台网络策略拦（需 VPN）"],
                              tail_action="tcp(vpn超时)", tags=("env_bound",))

    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  " + str(detail)) if detail and not cond else ""))
        ok = ok and cond

    # caps_fn 注入：递减切片被如实传进 attempt_fn
    seen_caps = []

    def spy_attempt(cid, idx, caps, prompt, meta):
        seen_caps.append(caps)
        if idx == 0:
            return AttemptOutcome(reason="no_more_actions", tokens=1, calls=1)  # 首次不解决 → 要第 2 次
        return AttemptOutcome(reason="flag_accepted", tokens=1, calls=1)

    orch = Orchestrator(spy_attempt, attempts_max=2, caps_fn=lambda r: {"tokens": 100_000 // max(1, r), "calls": 20 // max(1, r)})
    orch.run_challenge("spy")
    check("caps_fn 注入且按剩余尝试递减", seen_caps == [{"tokens": 50_000, "calls": 10}, {"tokens": 100_000, "calls": 20}], seen_caps)

    orch = Orchestrator(fake_attempt, attempts_max=3)
    r = orch.run_challenge("easy")
    check("easy 一次解出", r.solved and len(r.attempts) == 1, r)

    # near_miss：attempt_cap + verify → near_miss → 换策略再试 → 解出；第 2 次收到黑板（事实 + 死路）
    spy2 = []

    def spy_nm(cid, idx, caps, prompt, meta):
        spy2.append((idx, prompt, list(meta["facts"]), list(meta.get("dead_ends") or [])))
        return fake_attempt(cid, idx, caps, prompt, meta)

    r = Orchestrator(spy_nm, attempts_max=3).run_challenge("near_miss")
    check("near_miss 经重试解出", r.solved, r)
    check("near_miss 早停（2 次）", len(r.attempts) == 2, r.attempts)
    check("attempt#2 换策略", r.attempts[0].strategy["card_hint"] != r.attempts[1].strategy["card_hint"])
    check("attempt#1 无黑板注入(prompt 空)", spy2[0][1] == "")
    check("attempt#2 prompt 含死路", "盲打" in spy2[1][1], spy2[1][1])
    check("attempt#2 meta.facts 含第1次事实", any("存在 /kb" in f for f in spy2[1][2]), spy2[1][2])
    check("attempt#2 meta.dead_ends 含第1次死路", any("盲打" in d for d in spy2[1][3]), spy2[1][3])

    r = orch.run_challenge("hopeless")
    check("hopeless 拦下（1 次）", len(r.attempts) == 1 and r.blocked == "hopeless" and not r.solved, r)

    r = orch.run_challenge("budget")
    check("attempt_cap 重试后题级 budget 停", r.blocked == "budget" and len(r.attempts) == 2, r)

    check("classify solved", classify_failure("flag_accepted") == "solved")
    check("classify stopped(budget_exhausted)", classify_failure("budget_exhausted") == "stopped")
    check("classify near_miss(attempt_cap+verify)", classify_failure("attempt_cap", "verify_exploit") == "near_miss")
    check("classify retryable(attempt_cap+http)", classify_failure("attempt_cap", "http_request") == "retryable")
    check("classify hopeless(env_bound)", classify_failure("no_more_actions", "tcp", ("env_bound",)) == "hopeless")
    check("decide_k 受 calls 池约束 k≤2", decide_k(300_000, 20, min_calls=6, attempts_max=3) == 2,
          decide_k(300_000, 20, min_calls=6, attempts_max=3))

    # ---- resolve_k：不写=1（零回归）/ auto=动态 / 显式=尊重 / 非法=兜底 ----
    check("resolve_k 未配置 → 1（零回归）", resolve_k({}, tokens_pool=800_000, calls_pool=80) == 1)
    check("resolve_k 显式 3 → 3", resolve_k({"clusterb_k": 3}, tokens_pool=800_000, calls_pool=80) == 3)
    check("resolve_k 非法值 → 1", resolve_k({"clusterb_k": "abc"}, tokens_pool=800_000, calls_pool=80) == 1)
    auto_k = resolve_k({"clusterb_k": "auto", "clusterb_min_calls": 6}, tokens_pool=800_000, calls_pool=20)
    check("resolve_k auto 走 decide_k（calls 池 20/min6 → 2）", auto_k == 2, auto_k)
    check("resolve_k auto 与 decide_k 同口径",
          resolve_k({"clusterb_k": "auto"}, tokens_pool=300_000, calls_pool=20)
          == decide_k(300_000, 20, min_calls=4, reserve=0.12, attempts_max=3))

    # ---- 候选值提取（花括号形态，去重） ----
    got = extract_candidates(["返回 flag{abc123} 和 flag{abc123}", "另一个 CTF{x_y}"])
    check("extract_candidates 提花括号 flag", "flag{abc123}" in got and "CTF{x_y}" in got, got)
    check("extract_candidates 去重", len(got) == len(set(got)), got)
    check("extract_candidates 不收裸长串", extract_candidates(["token deadbeefdeadbeef"]) == [])

    # ---- 黑板三段注入：候选值段可见 + 三段各自限额 ----
    bb = Blackboard()
    bb.merge(AttemptOutcome(reason="attempt_cap", facts=["f1"], dead_ends=["d1"], candidates=["flag{a}"]))
    inj = bb.inject()
    check("黑板注入含候选值段", "[待验候选值] flag{a}" in inj, inj)

    bb = Blackboard()
    bb.facts = ["F" * 200] * 10
    bb.dead_ends = ["D" * 200] * 10
    bb.candidates = ["C" * 200] * 10
    inj = bb.inject()
    check("黑板三段限额生效（800/400/300）",
          len(inj) <= FACTS_MAX_CHARS + DEAD_ENDS_MAX_CHARS + CANDIDATES_MAX_CHARS + 64, len(inj))
    check("超限段以省略号收尾", "…" in inj)

    # ---- meta 契约：四键齐 ----
    seen_meta = {}

    def spy_meta(cid, idx, caps, prompt, meta):
        seen_meta[idx] = sorted(meta.keys())
        return AttemptOutcome(reason="flag_accepted")

    Orchestrator(spy_meta, attempts_max=1).run_challenge("meta")
    check("meta 含 strategy/facts/dead_ends/candidates 四键",
          seen_meta.get(0) == ["candidates", "dead_ends", "facts", "strategy"], seen_meta)

    print("\n%s" % ("ALL PASS" if ok else "SOME FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_self_test())