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
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("OK submit_ledger 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())