#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_roles.py — 让「config/agent.json 的 roles」与 roles/<role>.json / <role>.system.txt 零漂移。

为什么需要
----------
本项目的三源约定：
  1. `config/agent.json` 的 `roles.<role>` —— 运行时真正加载的那份（唯一权威）；
  2. `roles/<role>.json` —— 同名角色的镜像，方便单独阅读/评审；
  3. `roles/<role>.system.txt` —— 该角色系统提示词的纯文本版，方便直接读改。
`tests/smoke.py`（case_misc_contract）与 `tests/smoke_ai_role.py`（case_ai_contract）都会断言
「活配置 == roles/<role>.json（system 取 .txt）」。历史上有人只改 config 不改 roles，
于是测试红了 6 次（2026-11-10 接手时就是红的：max_steps 40 vs 20 等）。

用法
----
    python tools/sync_roles.py --check   # 只报漂移，不改文件（退出码 1 表示有漂移）
    python tools/sync_roles.py --write   # 以 config 为准回写 roles/
    python tools/sync_roles.py --write --role misc
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "agent.json"
ROLES_DIR = ROOT / "roles"


def load_config_roles() -> dict[str, dict]:
    raw = json.loads(CONFIG.read_text(encoding="utf-8-sig"))
    roles = raw.get("roles") or {}
    return {str(name): dict(spec or {}) for name, spec in roles.items()}


def role_files(role: str) -> tuple[pathlib.Path, pathlib.Path]:
    return ROLES_DIR / f"{role}.json", ROLES_DIR / f"{role}.system.txt"


def _wrapper_shape(path: pathlib.Path, role: str) -> str:
    """沿用既有文件的包裹形状：{"roles": {<role>: …}} 还是 {<role>: …}。

    两种写法都被 tests 的读取端支持；保持原样只为少一点无意义的 git diff。
    """
    if not path.is_file():
        return "roles"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError):
        return "roles"
    if isinstance(data, dict) and "roles" in data and isinstance(data["roles"], dict):
        return "roles"
    return "bare"


def read_role_json(role: str) -> dict | None:
    """只读 roles/<role>.json 里那一份 role spec（不含 .system.txt 回落）。"""
    json_path, _ = role_files(role)
    if not json_path.is_file():
        return None
    data = json.loads(json_path.read_text(encoding="utf-8-sig"))
    inner = data.get("roles") if isinstance(data, dict) and "roles" in data else data
    return dict((inner or {}).get(role) or {}) or None


def read_role_spec(role: str) -> dict | None:
    """按 tests 的同一口径读取 roles/<role>.json（system 缺失时回落 .system.txt）。"""
    spec = read_role_json(role)
    if spec is None:
        return None
    _, txt_path = role_files(role)
    if not spec.get("system") and txt_path.is_file():
        spec["system"] = txt_path.read_text(encoding="utf-8").strip()
    return spec or None


def mirrored_roles() -> list[str]:
    """roles/ 下已有镜像文件的角色（历史只维护了 ai 与 misc）。"""
    if not ROLES_DIR.is_dir():
        return []
    return sorted(p.stem for p in ROLES_DIR.glob("*.json"))


def expected(role: str, live: dict) -> tuple[dict, str]:
    """config 的活配置 → (roles json 内容, system.txt 内容)。system 单独出成 .txt。"""
    spec = {k: v for k, v in live.items() if k != "system"}
    return spec, str(live.get("system") or "").strip() + "\n"


def diff_keys(live: dict, current: dict) -> list[str]:
    keys = set(live) | set(current)
    return sorted(k for k in keys if live.get(k) != current.get(k))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="config/agent.json 与 roles/ 三源同步")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只报漂移（默认）")
    mode.add_argument("--write", action="store_true", help="以 config 为准回写 roles/")
    ap.add_argument("--role", action="append", default=None, help="只处理指定角色（可重复）")
    args = ap.parse_args(argv)

    live_roles = load_config_roles()
    ROLES_DIR.mkdir(parents=True, exist_ok=True)
    # 默认只校验「已有镜像」的角色：coordinator/crypto/pwn/rev/web 只活在 config 里，
    # 没有 roles/<role>.json 是历史设计，不算漂移。
    targets = args.role or mirrored_roles()

    drifted: list[str] = []
    for role in targets:
        live = live_roles.get(role)
        json_path, txt_path = role_files(role)
        if live is None:
            print(f"[skip] {role}: config/agent.json 里没有该角色")
            continue
        spec, prompt = expected(role, live)
        raw = read_role_json(role)
        if raw is None:
            drifted.append(role)
            print(f"[miss] {role}: 缺 roles/{role}.json")
            if args.write:
                _write(role, json_path, txt_path, spec, prompt)
            continue
        keys = diff_keys(spec, raw)
        # system 单独出成 .system.txt：比对 config 与文本文件（strip 后，尾部换行不算漂移）
        txt_now = txt_path.read_text(encoding="utf-8").strip() if txt_path.is_file() else None
        if live.get("system") and txt_now != str(live.get("system") or "").strip():
            keys.append("system(.txt)")
        if not keys:
            print(f"[ok]   {role}: 无漂移（{len(spec)} 键 + 提示词 {len(prompt)} 字符）")
            continue
        drifted.append(role)
        print(f"[drift] {role}: {len(keys)} 处不一致 → " + "、".join(keys))
        for key in keys[:8]:
            if key == "system(.txt)":
                print(f"         system: roles/*.txt 与 config 的提示词正文不一致")
                continue
            print(f"         {key}: roles={str(raw.get(key))[:70]}  config={str(spec.get(key))[:70]}")
        if args.write:
            _write(role, json_path, txt_path, spec, prompt)

    if args.write:
        print("\n已按 config/agent.json 回写。" if not drifted else "\n已回写漂移角色。")
        return 0
    if drifted:
        print(f"\n{len(drifted)} 个角色漂移：`python tools/sync_roles.py --write` 可修复。")
        return 1
    print("\n三源一致。")
    return 0


def _write(role: str, json_path: pathlib.Path, txt_path: pathlib.Path,
           spec: dict, prompt: str) -> None:
    shape = _wrapper_shape(json_path, role)
    payload = {"roles": {role: spec}} if shape == "roles" else {role: spec}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt_path.write_text(prompt, encoding="utf-8")
    print(f"         → 写回 {json_path.relative_to(ROOT)} + {txt_path.relative_to(ROOT)}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))