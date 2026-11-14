# -*- coding: utf-8 -*-
"""统计 CTFY 平台侧「提交次数上限」的直接证据。

CTFY 的回执/题目对象里有两个字段：attempts_remaining / attempts_cap。
本脚本扫描全部转录，按 run 分组统计这两个字段出现过的取值，并统计 submit 回执里的
attempt/correct 分布——用来回答「CTFY 平台到底有没有提交次数上限、上限是多少」。

转录里的工具结果是 JSON-in-JSON（引号被转义），所以匹配前先把 \\" 还原成 "。
用法：python tools/ctfy_attempts_audit.py
"""
from __future__ import annotations

import collections
import json
import pathlib
import re

ROOT = pathlib.Path("E:/ctfy_project")
PATTERN = re.compile(r'"(attempts_remaining|attempts_cap)"\s*:\s*([^,}\s]+)')
ATTEMPT = re.compile(r'"attempt"\s*:\s*(\d+)')


def unescape(text: str) -> str:
    return text.replace('\\"', '"')


def scan() -> None:
    values = collections.Counter()
    cap_values = collections.Counter()
    runs = collections.defaultdict(lambda: {"files": 0, "remaining_seen": 0, "cap_seen": 0,
                                            "submits": 0, "attempts": collections.Counter(),
                                            "null_remaining": 0})
    for f in ROOT.glob("runs/**/transcripts/*.jsonl"):
        run_name = f.parents[1].name
        info = runs[run_name]
        info["files"] += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        flat = unescape(text)
        for key, val in PATTERN.findall(flat):
            if key == "attempts_remaining":
                info["remaining_seen"] += 1
                values[val] += 1
                if val == "null":
                    info["null_remaining"] += 1
            else:
                info["cap_seen"] += 1
                cap_values[val] += 1
        for line in flat.splitlines():
            if '"tool": "submit"' not in line and '"tool":"submit"' not in line:
                continue
            info["submits"] += 1
            m = ATTEMPT.search(line)
            if m:
                info["attempts"][m.group(1)] += 1
    print("== 全局字段取值（CTFY 回执/题目对象）==")
    print("attempts_remaining:", dict(values))
    print("attempts_cap    :", dict(cap_values))
    print()
    print("== 按 run 分组（只列有该字段的 run）==")
    for name, info in sorted(runs.items()):
        if not (info["remaining_seen"] or info["cap_seen"]):
            continue
        print(f"{name}: files={info['files']} remaining_seen={info['remaining_seen']}"
              f" (null={info['null_remaining']}) cap_seen={info['cap_seen']}"
              f" submits={info['submits']} attempt分布={dict(info['attempts'])}")


if __name__ == "__main__":
    scan()