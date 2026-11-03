#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实例守卫：平台每队只有 **1** 个实例槽位，泄漏一个就等于堵死后面所有题。

为什么需要这个模块（实测根因，非假想）：
  - 11-03 批次 21% 的题没测到，原因是反复 `start_instance` 触到 **每题 5 次硬上限**；
  - 而"反复 start"的起点多半是**上一次进程被 kill 时 finally 未执行**（job_kill/看门狗超时），
    残留实例占住唯一槽位 → 后续 start 全报 "Rate limited by upstream API" → agent 误判为时间限流
    → 继续 start → 5 次烧光，当天该题作废。

三道防线（由弱到强，全部不依赖模型自觉）：
  1. `reap()`     —— 开跑前/每 N 轮把「不属于本题」的实例清掉（带保护名单与宽限期）；
  2. `start_remaining()` —— 起实例前先看剩余配额，剩 1 次时不再做投机性重试；
  3. `looks_rate_limited()` —— start 失败文本判「槽位被占/上游限流」，供 `solver._recover_instance_slot`
     （先清扫再重试一次，而不是原地重试）判定是否值得清扫。判定/清扫逻辑在 solver 里，本模块只出判据。

安全约束（会被误停别人的实例，代价比泄漏更大）：
  - `keep`：本题 challenge_id，永不触碰；
  - `protect`：调用方显式声明的保护名单（并发批跑时把兄弟题塞进来）；
  - `grace_seconds`：**拿不到创建时间的实例一律不动**（宁可漏杀，不可误杀）。
"""

from __future__ import annotations

import time

# 实例对象里可能出现的字段名（平台 3.4.6 实测为 challenge_id / id / created_at，
# 但同族工具命名不统一，这里全部宽容接受）
_CID_KEYS = ("challenge_id", "challengeId", "challenge")
_ID_KEYS = ("id", "instance_id", "instanceId")
_TIME_KEYS = ("created_at", "createdAt", "started_at", "startedAt", "created")


def _items(listed) -> list[dict]:
    """把 list_instances 的返回（list / {instances|items|data: [...]} / 其它）归一成列表。"""
    if isinstance(listed, list):
        return [x for x in listed if isinstance(x, dict)]
    if isinstance(listed, dict):
        for key in ("instances", "items", "data", "results"):
            value = listed.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def instance_challenge_id(item: dict) -> str:
    """实例归属的 challenge_id；challenge 字段可能是 dict，也可能是纯字符串。"""
    for key in _CID_KEYS:
        value = item.get(key)
        if isinstance(value, dict):
            inner = value.get("id") or value.get("challenge_id")
            if inner:
                return str(inner)
        elif value:
            return str(value)
    return ""


def instance_id(item: dict) -> str:
    for key in _ID_KEYS:
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _age_seconds(item: dict, now: float | None = None) -> float | None:
    """实例已存在多久（秒）。拿不到时间戳或有歧义时返回 None —— 调用方按「不动」处理。"""
    now = time.time() if now is None else now
    for key in _TIME_KEYS:
        value = item.get(key)
        if isinstance(value, (int, float)):
            # 兼容毫秒时间戳（>1e11 视为 ms）
            stamp = float(value) / 1000.0 if float(value) > 1e11 else float(value)
            if stamp > 0:
                return max(0.0, now - stamp)
        elif isinstance(value, str) and value.strip():
            text = value.strip().replace("Z", "+00:00")
            try:
                from datetime import datetime  # noqa: PLC0415 - 只在真用到时导入

                return max(0.0, now - datetime.fromisoformat(text).timestamp())
            except ValueError:
                return None
    return None


def list_all(platform) -> list[dict] | None:
    """读全量实例列表；平台不支持或调用失败返回 None（调用方据此跳过清扫，不报错）。"""
    name = platform.try_resolve("list_instances") if hasattr(platform, "try_resolve") else None
    if not name:
        return None
    try:
        return _items(platform.list_instances())
    except Exception:  # noqa: BLE001 - 清扫是尽力而为，绝不因为读列表失败而毁掉 run
        return None


def reap(
    platform,
    *,
    keep: str = "",
    protect: tuple[str, ...] | set[str] = (),
    grace_seconds: float = 120.0,
    dry_run: bool = False,
) -> dict:
    """停掉「既不是本题、也不在保护名单、且已存活超过宽限期」的实例。

    返回 {"ok", "listed", "stopped", "kept", "skipped_unknown_age", "failed", "error"}。
    `ok=False` 只表示「列表读不到」，不代表清扫失败——调用方据此决定是否提示。
    """
    result = {
        "ok": False, "listed": 0, "stopped": [], "kept": [],
        "skipped_unknown_age": [], "failed": [], "error": "",
    }
    name = platform.try_resolve("list_instances") if hasattr(platform, "try_resolve") else None
    if not name:
        result["error"] = "平台没有 list_instances，跳过清扫"
        return result
    try:
        items = _items(platform.list_instances())
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"读实例列表失败：{exc}"
        return result

    result["ok"] = True
    result["listed"] = len(items)
    keep_set = {keep} | {str(x) for x in protect if x}
    now = time.time()
    for item in items:
        cid = instance_challenge_id(item)
        iid = instance_id(item)
        if not iid:
            result["failed"].append({"reason": "实例没有 id 字段", "item": item})
            continue
        if cid and cid in keep_set:
            result["kept"].append({"id": iid, "challenge_id": cid, "why": "本题/保护名单"})
            continue
        age = _age_seconds(item, now)
        if age is None:
            # 拿不到年龄 = 可能是兄弟进程刚起的那一个：不杀（宁可漏杀）
            result["skipped_unknown_age"].append({"id": iid, "challenge_id": cid})
            continue
        if age < grace_seconds:
            result["skipped_unknown_age"].append({"id": iid, "challenge_id": cid})
            continue
        if dry_run:
            result["stopped"].append({"id": iid, "challenge_id": cid, "dry_run": True})
            continue
        try:
            platform.stop_instance(iid)
            result["stopped"].append({"id": iid, "challenge_id": cid})
        except Exception as exc:  # noqa: BLE001
            result["failed"].append({"id": iid, "challenge_id": cid, "error": str(exc)[:200]})
    return result


def start_remaining(store, challenge_id: str, limit: int) -> int:
    """本题还剩几次 start_instance 配额（平台每题硬上限 5 次，用尽当天作废）。"""
    try:
        item = store.require(challenge_id)
    except Exception:  # noqa: BLE001 - 题不在库里时按"满额"处理，由平台自己报错
        return int(limit)
    used = int((item.tool_counts or {}).get("start_instance", 0))
    return max(0, int(limit) - used)


def looks_rate_limited(text: str) -> bool:
    """start 失败文本是否属于「槽位被占 / 上游限流」这一类——那才有清扫后重试的意义。"""
    lower = (text or "").lower()
    return any(k in lower for k in (
        "rate limit", "rate-limit", "rate_limit", "429", "quota",
        "too many", "already have", "conflict", "409",
    ))


def summarize(result: dict) -> str:
    """一行可打印摘要（CLI 与事件回调共用）。"""
    if not result.get("ok"):
        return f"[实例守卫] {result.get('error') or '列表不可读，跳过'}"
    parts = [f"列出 {result['listed']}"]
    if result["stopped"]:
        parts.append(f"清扫 {len(result['stopped'])}")
    if result["kept"]:
        parts.append(f"保留 {len(result['kept'])}")
    if result["skipped_unknown_age"]:
        parts.append(f"跳过(未知年龄) {len(result['skipped_unknown_age'])}")
    if result["failed"]:
        parts.append(f"失败 {len(result['failed'])}")
    return "[实例守卫] " + "，".join(parts)
