#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归：跨 run 平台提交账本（P17）。

背景（实证）：
  * cuteRE：本地每轮都以为还剩 5 次，实际平台已累计 attempt=6（跨 run 不重置）；
  * 菜刀流量：`NPF_{...}` 被平台判错后，下一个 run 又原样交了一次；
  * vigenere / Code_Interpreter：attempt=5/6，额度已打空仍在批次里继续烧预算。
本用例锁：账本路径、attempt 单调记录、判错值拦截、跨 run 原子读写、提交前拦截文案。
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import submit_ledger  # noqa: E402


def case_paths(tmp: Path) -> None:
    import os

    old = os.environ.pop(submit_ledger.ENV_PATH, None)
    try:
        run_dir = tmp / "runs" / "big-w1"
        run_dir.mkdir(parents=True)
        path = submit_ledger.ledger_path(run_dir)
        assert path == tmp / "runs" / "submit_ledger.json", path
        os.environ[submit_ledger.ENV_PATH] = str(tmp / "global.json")
        assert submit_ledger.ledger_path(run_dir) == tmp / "global.json"
    finally:
        os.environ.pop(submit_ledger.ENV_PATH, None)
        if old is not None:
            os.environ[submit_ledger.ENV_PATH] = old


def case_attempts_and_rejected(tmp: Path) -> None:
    path = tmp / "ledger.json"
    cid = "ch-1"
    data = submit_ledger.load(path)
    assert submit_ledger.attempts(data, cid) == 0
    assert submit_ledger.blocked_reason(data, cid, "flag{a}", 5) == ""

    # 判错值跨 run 拦截（同值），换值放行 —— 在额度打空之前验证，才走「判错」分支
    submit_ledger.note_rejected(path, cid, "NPF_{DECOY}")
    data = submit_ledger.load(path)
    assert submit_ledger.is_rejected(data, cid, "NPF_{DECOY}")
    assert "已判错过" in submit_ledger.blocked_reason(data, cid, "NPF_{DECOY}", 5)
    assert submit_ledger.blocked_reason(data, cid, "NPF_{OTHER}", 5) == ""

    # attempt 单调取 max（并发/乱序回执不会把次数写小）
    assert submit_ledger.note_attempt(path, cid, 3) == 3
    assert submit_ledger.note_attempt(path, cid, 1) == 3
    assert submit_ledger.note_attempt(path, cid, 5) == 5

    # 打空后拦截，且文案指向「别再换值试探」
    data = submit_ledger.load(path)
    reason = submit_ledger.blocked_reason(data, cid, "flag{b}", 5)
    assert "已累计 5 次提交" in reason and "不要再换值试探" in reason, reason

    # 已答对的值单独记录，不与判错混在一起
    submit_ledger.note_accepted(path, cid, "flag{ok}")
    data = submit_ledger.load(path)
    entry = data["challenges"][cid]
    assert entry["accepted"] == ["flag{ok}"]
    assert entry["rejected"] == ["NPF_{DECOY}"]
    assert entry["attempts"] == 5

    # 落盘是合法 JSON 且可重复加载（原子替换路径）
    again = submit_ledger.load(path)
    assert again["challenges"][cid]["attempts"] == 5


def case_cap_disabled(tmp: Path) -> None:
    path = tmp / "ledger2.json"
    cid = "ch-2"
    submit_ledger.note_attempt(path, cid, 9)
    data = submit_ledger.load(path)
    # cap=0 表示关闭硬停（老行为），只查判错值
    assert submit_ledger.blocked_reason(data, cid, "flag{x}", 0) == ""


def case_repair_and_corrupt(tmp: Path) -> None:
    """P0-5：repair 只修值归属、不动 attempts；损坏账本改名留证。"""
    import json

    path = tmp / "ledger3.json"
    path.write_text(json.dumps({"schema": 1, "challenges": {
        "ch-r": {"attempts": 1, "accepted": ["flag{ok}"],
                 "rejected": ["flag{ok}", "flag{bad}", "flag{bad}"]},
        "ch-x": "坏了",
    }}, ensure_ascii=False), encoding="utf-8")
    first = submit_ledger.repair(path)
    data = submit_ledger.load(path)
    entry = data["challenges"]["ch-r"]
    assert first["changed"] is True, first
    assert entry["rejected"] == ["flag{bad}"], entry
    assert entry["attempts"] == 1, entry          # 不能按 len(accepted) 抬 attempts
    assert isinstance(data["challenges"]["ch-x"], dict)
    assert submit_ledger.repair(path)["changed"] is False    # 幂等
    assert len(list(tmp.glob("ledger3.json.bak.*"))) == 1    # 时间戳备份不覆盖证据

    broken = tmp / "ledger4.json"
    broken.write_text('{"schema": 1, "challenges": {', encoding="utf-8")
    assert submit_ledger.load(broken).get("challenges") == {}
    assert len(list(tmp.glob("ledger4.json.corrupt.*"))) == 1


def case_question_scope(tmp: Path) -> None:
    """P0-5：额度按 (题, 题号) 记，某题号烧完不锁死同题其它题号。"""
    import json

    path = tmp / "ledger5.json"
    submit_ledger.note_exhausted(path, "ch-q", 2, question_id="q1")
    data = submit_ledger.load(path)
    assert submit_ledger.blocked_reason(data, "ch-q", "flag{a}", 2, question_id="q1")
    assert submit_ledger.blocked_reason(data, "ch-q", "flag{a}", 2, question_id="q2") == ""
    submit_ledger.note_accepted(path, "ch-q", "flag{q}", question_id="q2")
    data = submit_ledger.load(path)
    assert submit_ledger.is_accepted(data, "ch-q", "flag{q}", question_id="q2")
    assert not submit_ledger.is_accepted(data, "ch-q", "flag{q}", question_id="q1")
    # cap<=0（平台不限/未知）不写账本，避免把不限额题写成 10/10 永久拦死
    path2 = tmp / "ledger6.json"
    submit_ledger.note_exhausted(path2, "ch-u", 0)
    assert submit_ledger.attempts(submit_ledger.load(path2), "ch-u") == 0
    # 老账本的题级 attempts 不能一上来就拦新题号（有题号条目时只认题号条目）
    path3 = tmp / "ledger7.json"
    path3.write_text(json.dumps({"schema": 1, "challenges": {
        "ch-legacy": {"attempts": 10, "rejected": [], "accepted": []}}}), encoding="utf-8")
    submit_ledger.note_attempt(path3, "ch-legacy", 1, question_id="q2")
    data = submit_ledger.load(path3)
    assert submit_ledger.attempts(data, "ch-legacy", "q2") == 1
    assert submit_ledger.attempts(data, "ch-legacy") == 10
    # 跨键清理：题级 accepted 的值也要从题号 rejected 里去掉
    path4 = tmp / "ledger8.json"
    path4.write_text(json.dumps({"schema": 1, "challenges": {
        "ch-k": {"attempts": 1, "accepted": ["flag{ok}"], "rejected": []},
        "ch-k::q1": {"attempts": 1, "accepted": [], "rejected": ["flag{ok}"]}}}),
        encoding="utf-8")
    assert submit_ledger.is_rejected(submit_ledger.load(path4), "ch-k", "flag{ok}", question_id="q1")
    assert submit_ledger.repair(path4)["changed"] is True
    assert not submit_ledger.is_rejected(submit_ledger.load(path4), "ch-k", "flag{ok}",
                                         question_id="q1")
    # merge 不能把 note_accepted 删掉的 rejected 并回来
    path5 = tmp / "ledger9.json"
    path5.write_text(json.dumps({"schema": 1, "challenges": {
        "ch-m": {"attempts": 1, "accepted": [], "rejected": ["flag{x}"]}}}), encoding="utf-8")
    submit_ledger.note_accepted(path5, "ch-m", "flag{x}")
    data = submit_ledger.load(path5)
    assert "flag{x}" not in data["challenges"]["ch-m"]["rejected"], data
    assert "flag{x}" in data["challenges"]["ch-m"]["accepted"], data


def case_receipt_classify(tmp: Path) -> None:
    """P0-5 审计回归：accepted 只认结构化字段；429/耗时里的 429 不能当额度用尽。"""
    import json

    from ctfy_agent import submit_guard

    def mcp(payload):
        return {"content": [{"type": "text",
                             "text": json.dumps(payload, ensure_ascii=False)}]}

    def text(payload):
        return {"content": [{"type": "text", "text": payload}]}

    assert submit_guard.classify(mcp({"correct": True, "attempts_remaining": 0,
                                      "attempts_cap": 1}))["status"] == "accepted"
    assert submit_guard.classify(text("incorrect"))["status"] == "rejected"
    assert submit_guard.classify(text("correct"))["status"] == "unknown"
    assert submit_guard.classify(text("HTTP 429 rate limited"))["status"] != "cap_exceeded"
    assert submit_guard.classify(mcp({"correct": True, "solve_time_s": 429.4}))["status"] \
        == "accepted"
    assert submit_guard.quota_from_questions(
        [{"id": "q1", "attempts_cap": 10, "attempts_remaining": 0}], "q2") == (0, -1)


def case_concurrent_writes(tmp: Path) -> None:
    """并发写不丢写（P0-5 四审 P1-3：20 线程各写 1..20，曾只剩 1..16）。"""
    import threading

    path = tmp / "concurrent" / "submit_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    def worker(start: int) -> None:
        for value in range(start, start + 20):
            submit_ledger.note_attempt(path, "C", value, question_id="flag")
            submit_ledger.note_rejected(path, "C", f"v{value}", question_id="flag")

    threads = [threading.Thread(target=worker, args=(1 + 20 * i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = submit_ledger.load(path)
    used = submit_ledger.attempts(data, "C", "flag")
    assert used == 120, f"并发丢写：attempts={used}（期望 120）"
    rejected = len((data["challenges"].get("C::flag") or {}).get("rejected") or [])
    assert rejected == 120, f"并发丢写：rejected={rejected}（期望 120）"


def case_legacy_scope(tmp: Path) -> None:
    """老账本题级 attempts 不能锁死新题号首交（P0-5 四审 P1-4）。"""
    path = tmp / "legacy" / "submit_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    submit_ledger.note_attempt(path, "OLD", 10)          # 老账本：只有题级条目
    data = submit_ledger.load(path)
    assert submit_ledger.attempts(data, "OLD", "flag") == 10
    assert submit_ledger.attempts(data, "OLD", "q2") == 0, "新题号被老题级 attempts 锁死"
    assert submit_ledger.blocked_reason(data, "OLD", "x", 10, question_id="q2") == ""
    # 默认题号（flag）仍按老账本硬停，兼容单问题题
    assert submit_ledger.blocked_reason(data, "OLD", "x", 10, question_id="flag") != ""


def case_verdict_no_ok_success(tmp: Path) -> None:
    """ok/success 不能单独判 accepted；isError 回执不判 accepted（P0-5 四审 P0-1）。"""
    from ctfy_agent import submit_guard

    def mcp(payload, is_error=False):
        row = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
        if is_error:
            row["isError"] = True
        return row

    assert submit_guard.classify(mcp({"ok": True}))["accepted"] is not True
    assert submit_guard.classify(mcp({"ok": True}))["status"] != "accepted"
    assert submit_guard.classify(mcp({"success": True}))["status"] != "accepted"
    assert submit_guard.classify(mcp({"ok": True}, is_error=True))["status"] != "accepted"
    assert submit_guard.classify(mcp({"accepted": True}))["status"] == "accepted"
    assert submit_guard.classify(mcp({"correct": True}))["status"] == "accepted"
    # cap 已知但只是剩余 0（无明确判定）→ cap_exceeded；有 correct:false → rejected
    assert submit_guard.classify(mcp({"attempts_remaining": 0, "attempts_cap": 10}))["status"] \
        == "cap_exceeded"
    assert submit_guard.classify(mcp({"correct": False, "attempts_remaining": 0,
                                      "attempts_cap": 10}))["status"] == "rejected"
    # isError + 泛泛失败不是答错；cap 报错也不是
    assert submit_guard.classify(mcp({"message": "internal server error"}, True))["status"] != "rejected"
    assert submit_guard.classify(
        {"isError": True, "content": [{"type": "text", "text": "tool unavailable"}]}
    )["status"] != "rejected"


def main() -> int:
    # 不用系统临时目录：DSH 沙盒下 tempfile 清理会对目录 chmod，触发 WinError 5（实测）。
    import shutil

    tmp = ROOT / "runs" / "_capcheck-tmp" / "submit_ledger"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        case_paths(tmp)
        case_attempts_and_rejected(tmp)
        case_cap_disabled(tmp)
        case_repair_and_corrupt(tmp)
        case_question_scope(tmp)
        case_receipt_classify(tmp)
        case_concurrent_writes(tmp)
        case_legacy_scope(tmp)
        case_verdict_no_ok_success(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK submit_ledger 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())