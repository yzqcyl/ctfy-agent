#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_domain_cards.py — rev/pwn/crypto/misc 方向方法卡回归。

背景：能力盘点里这 4 个方向方法卡为 0，模型只能靠提示词盲打；SAMPLE 10 道非 web 题的
真实 tag 词表已经拿到（runs/_nonweb_inventory.py），按真实 tag 补 8 张卡。

覆盖 8 条：
  1) 表规模：DOMAIN_CARDS 8 张
  2) reverse 真实 tags 命中 REV 卡
  3) pwn 真实 tags 命中 PWN 卡
  4) crypto 真实 tags 命中 CRYPTO 卡
  5) misc 真实 tags 命中 MISC 卡
  6) web tags 不命中方向卡（不污染 web 打法块）
  7) AI tags 不命中方向卡
  8) 输出上限受 MAX_CARDS 约束、坏输入不炸

运行: python tests/smoke_domain_cards.py
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.method_cards import (  # noqa: E402
    DOMAIN_CARDS, MAX_CARDS, domain_cards, method_cards,
)

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def main() -> int:
    check("方向卡共 8 张（4 方向 × 2）", len(DOMAIN_CARDS) == 8, str(len(DOMAIN_CARDS)))

    cases = {
        "REV": ["python", "xor", "per-team-artifact"],
        "PWN": ["elf", "stack", "bof", "tcp"],
        "CRYPTO": ["aes", "ecb", "oracle", "tcp"],
        "MISC": ["udp", "connectivity"],
    }
    for prefix, tags in cases.items():
        cards = domain_cards(tags)
        check(f"{prefix} 真实 tags 命中（{','.join(tags)}）",
              bool(cards) and cards[0].startswith(prefix), str(cards)[:80])

    check("web tags 不命中方向卡",
          domain_cards(["sqli", "file_upload", "jwt_none", "ssti", "ssrf"]) == [])
    check("AI tags 不命中方向卡",
          domain_cards(["owasp_2026_asi01", "agentic_ai", "prompt_injection"]) == [])

    heavy = ["reverse", "stripped", "xor", "bof", "stack", "elf", "aes", "ecb",
             "zip", "archive", "udp", "forensics", "rsa", "rop"]
    cards = domain_cards(heavy)
    check("上限受 MAX_CARDS 约束", 0 < len(cards) <= MAX_CARDS, str(len(cards)))
    check("坏输入不炸", domain_cards(None) == [] and domain_cards([]) == [] and domain_cards(["nope"]) == [])
    check("不影响 web 卡路径", "打法提示" not in "".join(method_cards(["sqli"])))

    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_domain_cards 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())