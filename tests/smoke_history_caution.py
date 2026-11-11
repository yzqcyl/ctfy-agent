#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_history_caution.py — 重跑时「上一轮收口结论」不再把模型锚死的提示回归（离线）。

背景（2026-11-11 本地 plat 第 4 轮实测）：SAMPLE-003 开局 30 秒内就引用历史 note 里的
「接入层不可达 / 维持历史判定，不再派活」直接 done；而那一轮真正的问题是「实例配额被
上一轮旧账锁死」，代码侧已修。历史笔记本身要保留（审计/证据），但重跑时不能当成本轮事实。

修法： histórico 笔记命中「收口 / 不可解 / 不可达 / 配额 / 5 次」等字样时，在 brief 里追加
一句「先花 1-2 步重测关键卡点（start_instance / host 可达性）再决定收口」的提示。
本文件钉住：有收口字样才提示、普通笔记不提示、提示真的进了 initial prompt。

运行: python tests/smoke_history_caution.py
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.solver import Solver, history_caution  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402
from ctfy_agent.tools_local import LocalTools  # noqa: E402

FAILS: list[str] = []
CID = "chall-history"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def _store(root: pathlib.Path, notes: list[str]) -> Store:
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    store = Store(root).load()
    store.upsert({"id": CID, "name": "hist", "category": "rev", "description": "hello", "points": 100})
    item = store.require(CID)
    for note in notes:
        item.notes.append(note)
    store.save()
    return store


def main() -> int:
    # ---- 1) 纯函数：命中收口类字样才提示 ----
    check("收口字样 → 提示",
          "重测关键卡点" in history_caution(["SAMPLE-003 最终收口（维持历史判定，不再派活）"]))
    check("不可达字样 → 提示", bool(history_caution(["接入层不可达，本机无 VPN"])))
    check("配额字样 → 提示", bool(history_caution(["start_instance 已达本题上限 5 次"])))
    check("普通进展笔记 → 不提示", history_caution(["已下载附件 healthcheckd，strings 找到隐藏命令"]) == "")
    check("空笔记 → 不提示", history_caution([]) == "" and history_caution(None) == "")

    # ---- 2) 集成：提示真的进了 initial prompt（有旧结论时）----
    tmp = ROOT / "runs" / f"_t_hist_{os.getpid()}"
    store = _store(tmp / "a", ["SAMPLE-003 最终收口（维持历史判定，不再派活）：接入层不可达"])
    prompt = Solver._initial_prompt(store.require(CID), LocalTools(store, CID))
    check("prompt：含「历史笔记」原文", "历史笔记" in prompt)
    check("prompt：含「重测关键卡点」提示", "重测关键卡点" in prompt)
    check("prompt：要求不要开局直接 done", "不要开局就直接 done" in prompt)

    # ---- 3) 集成：普通笔记不加提示（不给正常流程添噪）----
    store2 = _store(tmp / "b", ["已拿到实例 host，正在跑 exploit"])
    prompt2 = Solver._initial_prompt(store2.require(CID), LocalTools(store2, CID))
    check("prompt：普通笔记不加提示", "重测关键卡点" not in prompt2)

    print()
    if FAILS:
        print(f"smoke_history_caution 失败 {len(FAILS)} 项：{FAILS}")
        return 1
    print("smoke_history_caution 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())