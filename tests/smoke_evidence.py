#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_evidence.py — M4 证据校验的「表示层归一」回归（agent 打磨，2026-10-30）。

背景：v0 批 14 次 report 被拒里有 10 次是误杀——模型逐字复制的是**响应体嵌套 JSON 的内层
内容**（如 `{"data":{...,"value":"FLAG{...}"}}`），而账本原文带转义（`\"body\": \"{...}\"`），
字面比对必然落空。修复后 verify_evidence 会解出内层字符串再比（仍要求连续逐字，不做语义匹配）。

运行: python tests/smoke_evidence.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.ledger import Ledger  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def _ledger() -> Ledger:
    return Ledger()


def main() -> int:
    # 1) 字面逐字子串：接受
    lg = _ledger()
    lg.record("http_request", {"url": "http://t/api/x"}, '{"status":200,"body":"hello secret"}')
    check("字面逐字子串接受", lg.verify_evidence("hello secret") is not None)

    # 2) 嵌套转义 JSON 的内层内容：接受（本次修复的核心场景）
    lg = _ledger()
    inner = '{"data":{"siteConfig":{"key":"ops.flag_stash","value":"FLAG{fa864848e0689752}"}}}'
    envelope = '{"status": 200, "body": ' + '"' + inner.replace('"', '\\"') + '"}'
    lg.record("http_request", {"url": "http://t/graphql"}, envelope)
    check("嵌套转义 JSON 内层证据接受", lg.verify_evidence(inner) is not None)
    check("嵌套内层片段接受", lg.verify_evidence('"value":"FLAG{fa864848e0689752}"') is not None)

    # 3) 双层嵌套：接受
    lg = _ledger()
    deep = '{"result":{"rows":[{"note":"FLAG{deepnest123}"}]}}'
    env2 = '{"body": ' + '"' + deep.replace('"', '\\"').replace('\\', '\\\\') + '"}'
    lg.record("http_request", {"url": "http://t/q"}, env2)
    check("双层嵌套证据接受", lg.verify_evidence("FLAG{deepnest123}") is not None)

    # 4) 改写/翻译：拒绝（契约不能被放宽成语义匹配）
    lg = _ledger()
    lg.record("http_request", {"url": "http://t/login"}, '{"status":401,"body":"invalid credentials"}')
    check("改写证据拒绝", lg.verify_evidence("password is golden") is None)
    check("翻译证据拒绝", lg.verify_evidence("无效的凭据") is None)

    # 5) 空白/换行差异：接受
    lg = _ledger()
    lg.record("shell", {"cmd": "ls"}, "total 2\nFLAG{ws1}   extra")
    check("空白折叠接受", lg.verify_evidence("FLAG{ws1} extra") is not None)

    # 6) 全角/NFKC：接受
    lg = _ledger()
    lg.record("shell", {"cmd": "cat"}, '{"k": "FLAG{nfkc9}", "v":"x"}')
    check("全角标点归一接受", lg.verify_evidence('"FLAG{nfkc9}"， "v":"x"}') is not None)

    # 7) 太短：拒绝（防噪声）
    lg = _ledger()
    lg.record("shell", {"cmd": "ls"}, "abc")
    check("过短证据拒绝", lg.verify_evidence("ab") is None)

    # 8) 非 JSON / 二进制样原文：不崩、不误接受
    lg = _ledger()
    lg.record("shell", {"cmd": "cat bin"}, "\x00\x01\x02 binary-ish \xffFLAG{bin1}")
    check("非 JSON 原文不崩", lg.verify_evidence("FLAG{bin1}") is not None)
    check("非 JSON 原文不误接受", lg.verify_evidence("FLAG{absent}") is None)

    # 9) 空账本 / 无 raw：返回 None
    lg = _ledger()
    check("空账本返回 None", lg.verify_evidence("anything here") is None)

    # 10) recent_sources 片段足够长（≥200 字符可复制），且仍是原样文本
    lg = _ledger()
    long_json = '{"status":200,"body":"' + "A" * 500 + '"}'
    lg.record("http_request", {"url": "http://t/long"}, long_json)
    rs = lg.recent_sources(k=1)
    check("recent_sources 含长片段", len(rs) > 200, f"len={len(rs)}")

    # 11) 语义改写但含真实 flag 的不明文片段：仍拒绝（守住逐字契约）
    lg = _ledger()
    lg.record("http_request", {"url": "http://t/x"}, '{"body":"FLAG{real1}"}')
    check("只凭记忆拼 flag 仍拒绝", lg.verify_evidence("FLAG{real2}") is None)

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_evidence 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())