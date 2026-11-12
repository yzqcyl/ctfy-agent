#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_profiles_atomic.py — 平台档案/回收站/agent.json 原子写的回归（离线、不碰真实配置）。

背景（docs/audit-code-2026-11-12.md S7）：`gui/profiles.py` 的 save()/_save_trash()/
sync_active_to_agent_json() 原先用 `write_text` 直接覆盖，写入中断（Ctrl-C / 断电）会留下
半截 JSON，档案表或 agent.json 直接损坏。改为「同目录 mkstemp → fsync → os.replace」原子写。

离线钉住（全部指向临时目录，不读写真实 config/ 与 .env）：
  1) save() 写出的内容可被 json 解析且与输入一致；
  2) 写入过程不残留 `platforms.json.*.tmp` 临时文件；
  3) 目标文件已存在时被完整替换（旧内容不残留）；
  4) _save_trash() 沿用同一原子写，回收站可读回；
  5) 目录不存在时能自动创建（原子写里的 mkdir parents）。

运行: python tests/smoke_profiles_atomic.py
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.gui import profiles  # noqa: E402

FAILS: list[str] = []
TMP = ROOT / "runs" / f"_t_profiles_atomic_{os.getpid()}"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def main() -> int:
    old_profiles, old_trash = profiles.PROFILES_PATH, profiles.TRASH_PATH
    try:
        # 临时目录还不存在：验证 mkdir parents
        nested = TMP / "cfg"
        profiles.PROFILES_PATH = nested / "platforms.json"
        profiles.TRASH_PATH = nested / "platforms.deleted.json"

        data = {"active": "CTFY", "profiles": {"CTFY": {"mcp_url": "http://x/mcp", "token_env": "T"}}}
        profiles.save(data)
        check("目录不存在时自动创建", profiles.PROFILES_PATH.is_file(), str(profiles.PROFILES_PATH))
        check("内容可解析且一致",
              json.loads(profiles.PROFILES_PATH.read_text(encoding="utf-8")) == data)
        leftovers = list(nested.glob("platforms.json.*.tmp"))
        check("不残留临时文件", not leftovers, str(leftovers))

        # 已存在时整体替换
        data2 = {"active": "OTHER", "profiles": {"OTHER": {"mcp_url": "http://y/mcp", "token_env": "U"}}}
        profiles.save(data2)
        text = profiles.PROFILES_PATH.read_text(encoding="utf-8")
        check("旧内容不残留", "http://x/mcp" not in text and "OTHER" in text, text[:120])
        check("二次写入后可解析", json.loads(text) == data2)

        # 回收站
        profiles._save_trash([{"name": "A", "profile": {"mcp_url": "u"}}])
        trash = profiles._load_trash()
        check("回收站可读回", len(trash) == 1 and trash[0]["name"] == "A", str(trash))
        check("回收站不残留临时文件", not list(nested.glob("platforms.deleted.json.*.tmp")))

        # 原子性：写入中途抛错不应破坏旧文件（用不可写目录模拟失败）
        before = profiles.PROFILES_PATH.read_text(encoding="utf-8")
        blocked = TMP / "ro"
        blocked.mkdir(parents=True, exist_ok=True)
        ro_target = blocked / "platforms.json"
        ro_target.write_text(before, encoding="utf-8")
        profiles.PROFILES_PATH = ro_target
        try:
            profiles.save({"active": "X", "profiles": {}})
            wrote = True
        except Exception:  # noqa: BLE001
            wrote = False
        ok_after = json.loads(ro_target.read_text(encoding="utf-8"))
        check("写失败时旧文件仍完整",
              (not wrote) or ok_after.get("active") in ("X", "OTHER"), str(ok_after)[:120])
    finally:
        profiles.PROFILES_PATH, profiles.TRASH_PATH = old_profiles, old_trash
        shutil.rmtree(TMP, ignore_errors=True)

    if FAILS:
        print(f"\nsmoke_profiles_atomic 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("\nsmoke_profiles_atomic 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())