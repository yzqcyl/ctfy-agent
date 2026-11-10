# -*- coding: utf-8 -*-
"""GUI 设置：读取/保存平台接口与模型配置。

落盘策略（保证安全、不破坏既有条目）：
  - 密钥（CTFY_TOKEN / DEEPSEEK_API_KEY）只写 .env，绝不写进 config/agent.json；
  - 非密钥（mcp_url / model / llm_base_url / 预算）写 config/agent.json，
    读改写、只覆盖目标键，其余键（尤其是 roles 里的大段提示词）原样保留；
  - 密钥字段留空 = 不修改（避免把界面上的掩码写回文件）。
纯新增文件，不 import 时无副作用。
"""
from __future__ import annotations

import json
from pathlib import Path

from ..config import DEFAULT_CONFIG_PATH, ENV_FILE

# 界面上「用 * 显示」的密钥字段 -> .env 键名
SECRET_FIELDS = {
    "token": "CTFY_TOKEN",
    "llm_api_key": "DEEPSEEK_API_KEY",
}
# 走 config/agent.json 的非密钥键
AGENT_KEYS = ("mcp_url", "model", "llm_base_url", "max_tokens_per_challenge", "max_llm_calls")
INT_KEYS = ("max_tokens_per_challenge", "max_llm_calls")


def current(config) -> dict:
    """当前生效值（供对话框预填；密钥不回显明文）。"""
    return {
        "mcp_url": config.mcp_url or "",
        "token_set": bool((config.token or "").strip()),
        "model": config.model or "",
        "llm_base_url": config.llm_base_url or "",
        "llm_api_key_set": bool((config.llm_api_key or "").strip()),
        "max_tokens_per_challenge": str(config.max_tokens_per_challenge or 0),
        "max_llm_calls": str(config.max_llm_calls or 0),
    }


def _update_env(path: Path, updates: dict) -> None:
    """只替换/追加指定键，保留文件其它行与注释顺序。"""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    found: set[str] = set()
    out: list[str] = []
    for line in lines:
        key = None
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            found.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in found:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def save(config, values: dict) -> list[str]:
    """把界面值落盘并同步进内存 config。返回给人看的提示列表。

    values 可含：mcp_url / token / model / llm_base_url / llm_api_key /
                max_tokens_per_challenge / max_llm_calls
    其中 token / llm_api_key 为空表示「不修改」。
    """
    msgs: list[str] = []

    # 1) 密钥 -> .env
    env_updates: dict[str, str] = {}
    for field, env_key in SECRET_FIELDS.items():
        raw = (values.get(field) or "").strip()
        if not raw or "****" in raw:  # 空 / 掩码 = 保留原值
            continue
        env_updates[env_key] = raw
        setattr(config, field, raw)
    if env_updates:
        _update_env(ENV_FILE, env_updates)
        msgs.append(".env 已更新：" + "、".join(env_updates))

    # 2) 非密钥 -> config/agent.json（读改写，保留其它键）
    data: dict = {}
    if DEFAULT_CONFIG_PATH.is_file():
        try:
            data = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001 - 坏文件不覆盖，另存提示
            data = {}
            msgs.append(f"[警告] {DEFAULT_CONFIG_PATH.name} 解析失败，已跳过写入")
    changed: dict = {}
    for key in AGENT_KEYS:
        if key not in values:
            continue
        val = values[key]
        if val is None or str(val).strip() == "":
            continue
        if key in INT_KEYS:
            try:
                num = int(float(str(val).strip()))
            except (TypeError, ValueError):
                msgs.append(f"[警告] {key} 不是合法数字，已忽略")
                continue
            data[key] = num
            setattr(config, key, num)
        else:
            data[key] = str(val).strip()
            setattr(config, key, data[key])
        changed[key] = data[key]
    if changed:
        DEFAULT_CONFIG_PATH.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        msgs.append(f"{DEFAULT_CONFIG_PATH.name} 已更新：" + "、".join(changed))
    return msgs