#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""观测级去重 / 同目标重复刹车 / 预算阶段线 / 环境失败断路器（P0+A1，bank-500k-v1 实测驱动）。

实机证据（docs/report-large-scale-test.md §10）：
- 未解出题 49 次单点 http_request：同一 JS 文件取 13 次、/api/auth/login 取 12 次、
  /api/auth/me 取 12 次；解出题仅 7 次。失败模式 = 重复探索 + 无预算感知；
- 预算上限实际等价于调用次数上限（500k ÷ ~12.6k/call ≈ 40 次），agent 对此无感知；
- 原 still_rounds 刹车（连续 N 步无新 digest）会被「重复旧目标 + 偶尔取新文件」的
  交替模式重置，在实测中从未触发。

三条对策，全部在消息送进模型之前生效（不依赖模型自觉）：
1. ObservationTracker：digest 相同的工具输出 → 注入上下文的正文替换为「重复观察」
   省略标记（audit transcript 仍存全文）。省上下文，更延缓 _compact_history 把有用
   证据挤出历史（重复获取问题的根源，对应论文 2608.16370 / TRACE 2608.06503）。
2. 同一 (tool, target) 第 4 次起注入换法提示（换端点/换方法/交回协调者）——计数
   独立累计，不被其它目标的新输出稀释。
3. BudgetPhase.line：把预算折成一行阶段提示（探索/收敛/收口 + 剩余调用估算），
   run_role/_solve_once 每步写进事实头部，协调者每轮注入一条。
"""

from __future__ import annotations

import hashlib
import json
import re
from json import JSONDecodeError

REPEAT_TARGET_NOTE_AT = 4    # 同一 (tool,target) 第几次调用时开始注入换法提示
REPEAT_NOTE_EVERY = 4        # 之后每再 N 次提示一次（第 4、8、12…次），避免刷屏
REPEAT_OMIT_HEAD = 160       # 重复观测保留的头部字符数

# ---- A1 环境失败断路器（实测依据 docs/audit-agent-2026-10-30.md §4）----
# v0 批 2134 次工具调用里 379 次（17.8%）是接入/环境类失败：SAMPLE-003 烧 30 次（53% 预算）、
# SAMPLE-004 28 次（54%）、最长连续 9 次；而题目攻击面其实早有结论（SAMPLE-012 主动收口那题
# 正是正确做法）。这里只做**提示**不做硬拦截：环境问题不是模型换法能解决的，但模型常会一直试。
ENV_NOTE_AT = 3          # 连续环境类失败达到 N 次 → 注入「先确认接入方式」提示
ENV_CLOSE_AT = 6         # 连续达到 N 次 → 注入「立即收口、换接入路线」建议
ENV_BUDGET_RATIO = 0.30  # 环境失败次数占模型调用次数 ≥ 该比例 → 追加预算侧提示

# 环境类失败的判据（只在工具返回文本是失败时命中；成功输出里的地址/正文不会误伤：
# 要求先出现失败信号词，再在同一段文本里出现环境线索）。
_ENV_HINT = re.compile(
    r"getaddrinfo|Name or service not known|DNS[^，。;,\n]{0,10}失败|无法解析|隧道|tailscale|wireguard"
    r"|ConnectionRefused|积极拒绝|连接失败|Connection reset|网络不可达|No route to host"
    r"|command not found|不是内部或外部命令",
    re.I,
)
_FAIL_SIGNAL = re.compile(r"失败|错误|拒绝|refused|not known|不可达|超时|报错|Traceback|未安装", re.I)
_DENIED = re.compile(r"\[工具被拒绝\]|合规拦截|工具未开启", re.I)
_TIMEOUT = re.compile(r"命令超时|请求超时|timed out|Timeout", re.I)


def failure_kind(name: str, text: str) -> str | None:
    """把一次工具调用结果分类成环境类失败；非环境类失败/正常输出返回 None。

    - dns：DNS/隧道/连接失败（*.internal、VPN 不通）
    - bin：本机缺工具（ffuf/sqlmap/解释器）
    - timeout：超时
    - blocked：被工具层拒绝（合规拦截/未开启/未登记实例）——接入/用法类浪费
    注意顺序：环境线索优先于「[工具被拒绝]」前缀。tcp/netcheck 对 *.internal 的拒绝
    文本既带「工具被拒绝」又带 DNS 失败，必须先归到 dns，否则统计失真。
    """
    raw = str(text)
    if not raw:
        return None
    head = raw[:600]
    if _FAIL_SIGNAL.search(head):
        if re.search(r"未安装|command not found|不是内部或外部命令", head):
            return "bin"
        if _ENV_HINT.search(head):
            return "dns"
        if _TIMEOUT.search(head):
            return "timeout"
    if _DENIED.search(head):
        return "blocked"
    return None


class EnvFailureTracker:
    """连续环境类失败 → 提示收口（advisory，不拦工具）。

    设计原则（与 P0 一致）：只在**消息送进模型之前**追加一条 user 提示，
    不改工具行为、不做硬拦截；提示只在阈值点出现一次，重复启动不刷屏。
    """

    def __init__(self, note_at: int = ENV_NOTE_AT, close_at: int = ENV_CLOSE_AT,
                 budget_ratio: float = ENV_BUDGET_RATIO):
        self._note_at = max(2, int(note_at))
        self._close_at = max(self._note_at + 1, int(close_at))
        self._budget_ratio = max(0.0, float(budget_ratio))
        self.streak = 0
        self.max_streak = 0
        self.env_failures = 0
        self.calls = 0
        self.kinds: dict[str, int] = {}
        self.notes_injected = 0
        self._ratio_noted = False
        self.isolated_note: str | None = None

    def observe(self, name: str, text: str, budget=None) -> str | None:
        """每次工具调用后调一次；返回要注入的提示（或 None）。"""
        self.calls += 1
        kind = failure_kind(name, str(text))
        if kind is None:
            self.streak = 0
            return None
        self.env_failures += 1
        self.kinds[kind] = self.kinds.get(kind, 0) + 1
        self.streak += 1
        self.max_streak = max(self.max_streak, self.streak)
        if self.streak == self._note_at:
            self.notes_injected += 1
            return (
                f"[框架·环境侧] 连续 {self.streak} 次工具调用失败在接入/环境层（{kind}）。"
                "这类失败换攻击载荷通常无效：先确认接入方式是否成立（实例地址/隧道/本机工具是否存在），"
                "别把预算继续烧在接入层；若确认不可达，就把已确认事实写进 report 交回协调者换路线。"
            )
        if self.streak == self._close_at:
            self.notes_injected += 1
            return (
                f"[框架·环境侧] 连续 {self.streak} 次接入层失败，仍未打通。"
                "本角色应立即收口：停止同类重试，把「已确认事实 + 卡在哪一层」写进 report 交回协调者，"
                "由协调者换接入路线（换角色/换工具/换协议）或判定本题环境不可达。"
            )
        # 预算侧：环境失败占比过高时补一条（只一次）
        if not self._ratio_noted and budget is not None:
            cap = int(getattr(budget, "max_tokens", 0) or 0)
            used = int(getattr(budget, "total_tokens", 0) or 0)
            if cap > 0 and used / cap >= self._budget_ratio and self.env_failures >= self._note_at:
                self._ratio_noted = True
                self.notes_injected += 1
                return (
                    f"[框架·环境侧] 已有 {self.env_failures}/{self.calls} 次工具调用失败在接入/环境层，"
                    f"预算已用 {used / cap:.0%}。优先转「接入排查」或直接收口，别再铺新攻击面。"
                )
        return None

    def stats(self) -> dict:
        return {
            "calls": self.calls,
            "env_failures": self.env_failures,
            "max_streak": self.max_streak,
            "kinds": dict(self.kinds),
            "notes_injected": self.notes_injected,
        }

# 时变字段：与 solver._stable_output 同口径（此处独立实现，避免循环导入）。
# 剥离后哈希，避免「同样的失败输出因 elapsed_ms 不同 → 永远算新输出」。
_TIMEVARY_KEYS = frozenset({
    "elapsed_ms", "ms", "duration", "ts", "timestamp",
    "started_at", "finished_at", "created_at", "updated_at",
})


def stable_text(text: str) -> str:
    """剥离 JSON 时变字段后的稳定内容（重复判定基准）。"""
    raw = str(text).strip()
    if raw.startswith("{"):
        try:
            data, _ = json.JSONDecoder().raw_decode(raw)
        except (JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if str(k).lower() not in _TIMEVARY_KEYS}
            return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return raw


def primary_target(name: str, args: dict) -> str:
    """从工具参数里取「主目标」键：URL/端点/查询/文件等；取不到则退化到参数摘要。"""
    args = args if isinstance(args, dict) else {}
    for key in ("url", "endpoint", "target", "challenge_id", "file", "path",
                "query", "q", "search", "cmd", "command", "script", "message", "text"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True)[:80]
    except (TypeError, ValueError):
        return str(args)[:80]


class ObservationTracker:
    """单次角色运行内的观测去重 + 同目标重复计数。

    observe() 返回 (display, is_new, note)：
      display  应注入上下文的文本（重复时替换为省略标记）；
      is_new   digest 是否首次出现（run_role 的 still_rounds 判定继续用它）；
      note     可选的换法提示（同目标第 4/8/12…次时给出），调用方追加为 user 消息。
    """

    def __init__(self, note_at: int = REPEAT_TARGET_NOTE_AT, note_every: int = REPEAT_NOTE_EVERY):
        self._digest_order: dict[str, int] = {}
        self._targets: dict[tuple[str, str], int] = {}
        self._note_at = max(2, int(note_at))
        self._note_every = max(1, int(note_every))
        self.repeats_shrunk = 0   # 统计：被省略的重复观测次数
        self.notes_injected = 0   # 统计：注入的换法提示次数

    def observe(self, name: str, args: dict, text: str) -> tuple[str, bool, str | None]:
        raw = str(text)
        digest = hashlib.sha1(stable_text(raw).encode("utf-8", "replace")).hexdigest()[:12]
        target = primary_target(name, args)
        key = (str(name), target)
        count = self._targets.get(key, 0) + 1
        self._targets[key] = count

        is_new = digest not in self._digest_order
        if is_new:
            self._digest_order[digest] = len(self._digest_order) + 1
            display = raw
        else:
            self.repeats_shrunk += 1
            head = " ".join(raw.strip()[:REPEAT_OMIT_HEAD].split())
            omitted = max(0, len(raw) - REPEAT_OMIT_HEAD)
            display = (f"«重复观察（与第 {self._digest_order[digest]} 次输出完全相同，"
                       f"其余 {omitted} 字符已省略）» {head}")

        note = None
        if count >= self._note_at and (count - self._note_at) % self._note_every == 0:
            note = (f"[框架·同目标重复] {name} 对「{target}」已是第 {count} 次调用。"
                    "同一目标反复打不会产生新信息：换端点/换方法（POST/编码/参数类型）/换载体，"
                    "或直接 report 交回协调者换人。")
            self.notes_injected += 1
        return display, is_new, note

    def stats(self) -> dict:
        return {
            "repeats_shrunk": self.repeats_shrunk,
            "notes_injected": self.notes_injected,
            "distinct_targets": len(self._targets),
            "distinct_outputs": len(self._digest_order),
        }


class BudgetPhase:
    """把 Budget 快照折成一行「阶段提示」。advisory（提示），不做硬拦截。"""

    CONVERGE_AT = 0.50   # 已用 ≥50%：转向收敛（对应 §10.7 方案的 50% 收敛候选）
    CLOSE_AT = 0.75      # 已用 ≥75%：收口（对应 75% 禁新探索）

    @staticmethod
    def line(budget) -> str | None:
        """无预算上限且无墙钟时返回 None（默认配置下行为与旧版逐字一致）。"""
        if budget is None:
            return None
        cap = int(getattr(budget, "max_tokens", 0) or 0)
        left_seconds = getattr(budget, "seconds_left", None)
        if cap <= 0 and left_seconds is None:
            return None
        bits: list[str] = []
        if left_seconds is not None:
            # 墙钟是独立一层：token 还剩很多也可能已经没有时间了，必须让模型看得见
            bits.append(f"剩余 {left_seconds / 60:.0f} 分钟（截止 {budget.deadline_clock}）")
        if cap > 0:
            used = int(budget.total_tokens or 0)
            calls = int(budget.calls or 0)
            ratio = used / cap
            left_calls = ""
            if calls > 0:
                avg = used / calls
                if avg > 0:
                    left_calls = f"，按当前消耗约剩 {max(0, int((cap - used) / avg))} 次模型调用"
            if ratio < BudgetPhase.CONVERGE_AT:
                phase = "探索期：可开辟新攻击面"
            elif ratio < BudgetPhase.CLOSE_AT:
                phase = "收敛期：优先把已有证据整理成候选并验证，少开新面"
            else:
                phase = "收口期：只验证/提交已有候选，禁止开辟新攻击面"
            bits.append(f"已用 {used:,}/{cap:,} tok（{ratio:.0%}{left_calls}）— {phase}")
        return "[预算] " + "；".join(bits)
