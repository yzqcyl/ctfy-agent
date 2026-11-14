# -*- coding: utf-8 -*-
"""提交回执判定（P0-5 / P0-5 审计修复）。

纯逻辑，放 src 里以便进仓、可被 smoke 覆盖、可被 CLI 复用；`runs/_tpctf/submit_guard.py` 只是
把本模块的 classify/quota 与「带平台调用的 SubmitGuard 编排」组合起来的赛中胶水。

判定口径（对齐 solver.looks_accepted 的安全约定）：
  * accepted 只认**结构化 JSON** 的明确通过字段（correct/accepted/success/ok/passed）；
  * 自由文本只用于判「未通过」（incorrect/wrong/…），绝不从自然语言推断通过
    （否则 "incorrect" 里的 "correct" 会把错值洗成已收 —— P0-5 审计 Q1）；
  * 额度用尽只认平台真实错误码（question_attempt_cap_exceeded 等），不用 429 文本猜测；
  * 结构化 accepted 优先于 remaining==0（最后一击答对时 remaining 必然为 0）。
"""
from __future__ import annotations

import json

CAP_MARKERS = (
    "question_attempt_cap_exceeded",
    "attempt_cap_exceeded",
    "attempts_exhausted",
    "attempt_limit_exceeded",
    "提交次数已用尽",
    "提交额度已用尽",
    "提交次数已达上限",
)
TRANSIENT_MARKERS = (
    "rate limit", "too many requests", "timed out", "timeout", "temporarily unavailable",
    "service unavailable", "bad gateway", "gateway timeout", "connection reset",
    "platform_busy", "platform busy", "did not process this request", "send it again in",
    "server busy", "try again", "限流", "超时", "稍后重试", "平台繁忙", "平台忙",
)


def receipt_text(result, text: str = "") -> str:
    """统一渲染回执：所有 content part + structuredContent 都要看（不能只看第一段）。"""
    if isinstance(result, dict):
        from .mcp_client import render_call_result  # noqa: PLC0415

        return render_call_result(result)
    return str(text or result or "")


def looks_accepted(text: str):
    """复用 solver 的既有加固实现（结构化才判通过；这里延迟导入避免循环依赖）。"""
    from .solver import looks_accepted as _impl  # noqa: PLC0415

    return _impl(text)


def _as_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _quota_from_obj(obj) -> tuple[int, int]:
    if not isinstance(obj, dict):
        return (0, -1)
    cap = _as_int(obj.get("attempts_cap"))
    remaining = _as_int(obj.get("attempts_remaining"))
    if remaining is not None and remaining >= 0:
        return (max(0, cap or 0), remaining)
    inner = obj.get("structuredContent")
    if isinstance(inner, dict):
        found = _quota_from_obj(inner)
        if found[1] >= 0:
            return found
    return (max(0, cap or 0), -1)


def quota_from_receipt(result, text: str = "") -> tuple[int, int]:
    """从回执里取 (attempts_cap, attempts_remaining)；取不到返回 (0, -1)（未知，不猜）。"""
    raw = receipt_text(result, text)
    found = _quota_from_obj(result)
    if found[1] >= 0:
        return found
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            parsed = json.loads(stripped)
        except (ValueError, TypeError):
            continue
        found = _quota_from_obj(parsed)
        if found[1] >= 0:
            return found
    if isinstance(result, dict):
        inner = result.get("structuredContent")
        if isinstance(inner, dict):
            found = _quota_from_obj(inner)
            if found[1] >= 0:
                return found
    start, end = raw.find("{"), raw.rfind("}")
    if 0 <= start < end:
        try:
            found = _quota_from_obj(json.loads(raw[start:end + 1]))
            if found[1] >= 0:
                return found
        except (ValueError, TypeError):
            pass
    return (0, -1)


def quota_from_questions(questions, question_id: str = "") -> tuple[int, int]:
    """从 challenge.questions[] 取 (cap, remaining)。

    只在**找到对应题号**时返回该题号的值；题号给错就直接 (0,-1)，绝不拿别的题号顶替
    （P0-5 审计 Q6：串题号会把 A 的额度当成 B 的）。
    """
    rows = [q for q in (questions or []) if isinstance(q, dict)]
    if not rows:
        return (0, -1)
    want = str(question_id or "").strip()
    if want:
        matches = [q for q in rows if str(q.get("id") or q.get("question_id") or "") == want]
        if not matches:
            return (0, -1)
        caps, remainings = [], []
        for q in matches:
            cap = _as_int(q.get("attempts_cap"))
            remaining = _as_int(q.get("attempts_remaining"))
            caps.append(max(0, cap or 0))
            remainings.append(remaining if remaining is not None and remaining >= 0 else -1)
        remaining = max(remainings)
        return (max(caps), remaining)
    # 没给题号：所有题号口径一致才敢用，否则未知
    pairs = set()
    for q in rows:
        cap = _as_int(q.get("attempts_cap"))
        remaining = _as_int(q.get("attempts_remaining"))
        pairs.add((max(0, cap or 0), remaining if remaining is not None and remaining >= 0 else -1))
    if len(pairs) == 1:
        return pairs.pop()
    return (0, -1)


def attempt_from_receipt(result, text: str = "") -> int:
    """平台回执里的 attempt（用户+题全局累计）；取不到返回 0。"""
    raw = receipt_text(result, text)

    def _pick(obj) -> int:
        if not isinstance(obj, dict):
            return 0
        value = _as_int(obj.get("attempt"))
        if value is not None and value >= 0:
            return value
        inner = obj.get("structuredContent")
        return _pick(inner) if isinstance(inner, dict) else 0

    found = _pick(result)
    if found:
        return found
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            try:
                found = _pick(json.loads(stripped))
            except (ValueError, TypeError):
                found = 0
            if found:
                return found
    return 0


def _json_verdict(obj):
    """从单个 JSON 对象里取明确的通过/未通过判定；取不到返回 None。"""
    if not isinstance(obj, dict):
        return None
    for key in ("accepted", "correct", "success", "ok", "passed"):
        if key in obj and isinstance(obj[key], bool):
            return obj[key]
    for key in ("status", "result"):
        value = str(obj.get(key, "")).lower()
        if value in ("correct", "accepted", "success", "ok", "true"):
            return True
        if value in ("wrong", "incorrect", "failed", "error", "false", "rejected"):
            return False
    return None


def _scan_json_verdict(text: str):
    """回执是多段（散文 + JSON / structuredContent）时，逐行找 JSON 对象里的明确判定。

    solver.looks_accepted 只解析开头第一个 JSON；多段回执会落到 None（判不出），
    这里补扫，避免把「判错」漏成 unknown（P0-5 审计 Q1-3）。
    """
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in lines:
        if not line.startswith("{"):
            continue
        try:
            verdict = _json_verdict(json.loads(line))
        except (ValueError, TypeError):
            continue
        if verdict is not None:
            return verdict
    start, end = str(text or "").find("{"), str(text or "").rfind("}")
    if 0 <= start < end:
        try:
            return _json_verdict(json.loads(str(text)[start:end + 1]))
        except (ValueError, TypeError):
            pass
    return None


def classify(result, text: str = "") -> dict:
    """把一次提交回执归一成 {status, accepted, attempt, cap, remaining, reason}。

    status ∈ accepted / rejected / cap_exceeded / transient / error / unknown
    """
    raw = receipt_text(result, text)
    lowered = raw.lower()
    is_error = bool(isinstance(result, dict) and result.get("isError"))
    accepted = looks_accepted(raw)
    if accepted is None:
        accepted = _scan_json_verdict(raw)
    if accepted is None:
        # 自由文本只用于判「未通过」（绝不正向推断通过），补上 solver 负面词表没有的中文说法
        negatives = ("未通过", "不正确", "不对", "答案错误", "错误", "not passed")
        if any(word in lowered for word in negatives):
            accepted = False
    cap, remaining = quota_from_receipt(result, text)
    attempt = attempt_from_receipt(result, text)
    out = {
        "status": "unknown",
        "accepted": accepted,
        "attempt": attempt,
        "cap": cap,
        "remaining": remaining,
        "reason": "",
        "is_error": is_error,
        "text": raw,
    }
    # 1) 结构化 accepted 最优先：最后一击答对时 remaining 必然 0，不能被判成额度用尽
    if accepted is True:
        out["status"] = "accepted"
        return out
    # 2) 平台明确错误码 → 额度用尽（不做 429 文本猜测）
    if any(mark.lower() in lowered for mark in CAP_MARKERS):
        out["status"] = "cap_exceeded"
        out["reason"] = "cap_marker"
        return out
    # 3) 传输/限流类错误：平台没判定这个值，不能记成「答错」
    if is_error and any(mark in lowered for mark in TRANSIENT_MARKERS):
        out["status"] = "transient"
        out["reason"] = "transient_error"
        return out
    # 4) 自由文本/结构化字段明确判错
    if accepted is False:
        out["status"] = "rejected"
        out["reason"] = "judged_wrong"
        return out
    # 5) 没判通过但额度确实归零（cap 已知）→ 视为额度用尽，防止继续白烧
    if remaining == 0 and cap > 0:
        out["status"] = "cap_exceeded"
        out["reason"] = "remaining_zero"
        return out
    if is_error:
        out["status"] = "error"
        out["reason"] = "tool_error"
    return out