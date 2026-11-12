# -*- coding: utf-8 -*-
"""平台档案：支持多个 MCP 平台（不止 CTFY），可新建/切换/删除。

存储：
  config/platforms.json  —— {"active": 名称, "profiles": {名称: {mcp_url, token_env, tool_map}}}
  .env                   —— 每个档案的 token 存在自己的 token_env 键下（默认档案用 CTFY_TOKEN，
                            与 CLI 完全兼容）。密钥永不写入 platforms.json / agent.json。

换平台时只需：填 MCP 地址 + Token；若对方工具名与约定不同，用「工具名映射」写 JSON 覆盖即可。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from ..config import CONFIG_DIR, DEFAULT_CONFIG_PATH, ENV_FILE
from .settings import _update_env

PROFILES_PATH = CONFIG_DIR / "platforms.json"
TRASH_PATH = CONFIG_DIR / "platforms.deleted.json"
DEFAULT_NAME = "CTFY"
DEFAULT_TOKEN_ENV = "CTFY_TOKEN"  # secret-scan: allow —— 这是环境变量「名字」，不是凭据值


def _read_agent_json() -> dict:
    try:
        return json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(name or "")).strip("_").upper()
    return s or "PLATFORM"


def _unique_name(profiles: dict, base: str) -> str:
    if base not in profiles:
        return base
    i = 2
    while f"{base}-{i}" in profiles:
        i += 1
    return f"{base}-{i}"


def load() -> dict:
    """读档案表；首次使用则由 agent.json 现状生成一个默认档案（不落盘）。"""
    if PROFILES_PATH.is_file():
        try:
            data = json.loads(PROFILES_PATH.read_text(encoding="utf-8-sig"))
            if isinstance(data.get("profiles"), dict) and data["profiles"]:
                return data
        except Exception:  # noqa: BLE001
            pass
    raw = _read_agent_json()
    default = {
        "mcp_url": str(raw.get("mcp_url") or ""),
        "token_env": DEFAULT_TOKEN_ENV,
        "tool_map": dict(raw.get("tool_map") or {}),
    }
    return {"active": DEFAULT_NAME, "profiles": {DEFAULT_NAME: default}}


def _atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace 的原子写（审计 S7）。

    对比 state.py 的同类实现：不共享 pid/tid 唯一名也没关系，mkstemp 已保证唯一。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def save(data: dict) -> None:
    """原子写档案表（审计 S7）：先写同目录临时文件再 os.replace，写一半崩溃不会毁档。"""
    _atomic_write_text(PROFILES_PATH, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _read_env() -> dict:
    out: dict[str, str] = {}
    if ENV_FILE.is_file():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def token_of(profile: dict) -> str:
    key = str(profile.get("token_env") or DEFAULT_TOKEN_ENV)
    return _read_env().get(key, "")


def set_token(profile: dict, token: str) -> None:
    token = (token or "").strip()
    if not token:
        return
    key = str(profile.get("token_env") or DEFAULT_TOKEN_ENV)
    _update_env(ENV_FILE, {key: token})


def new_profile(data: dict, name: str = "新平台", mcp_url: str = "") -> str:
    name = _unique_name(data["profiles"], (name or "新平台").strip())
    data["profiles"][name] = {
        "mcp_url": (mcp_url or "").strip(),
        "token_env": "PLATFORM_TOKEN_" + _slug(name),
        "tool_map": {},
    }
    return name


def _remove_env_keys(keys: set[str]) -> list[str]:
    """从 .env 里删掉指定键，保留其它行与注释顺序。返回实际删掉的键。"""
    if not keys or not ENV_FILE.is_file():
        return []
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines()
    kept: list[str] = []
    removed: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
        if key in keys:
            removed.append(key)
            continue
        kept.append(line)
    if removed:
        text = "\n".join(kept).strip("\n")
        ENV_FILE.write_text((text + "\n") if text else "", encoding="utf-8")
    return removed


def delete_token(profile: dict) -> bool:
    """从 .env 删掉这个档案的 token。返回是否真的删到了。"""
    key = str(profile.get("token_env") or DEFAULT_TOKEN_ENV)
    return bool(_remove_env_keys({key}))


def delete_profile(data: dict, name: str) -> None:
    """删除档案：配置（含 tool_map）进回收站可恢复，**token 一并从 .env 删除**。

    token 是凭据，不写进回收站；恢复档案后需要重新填一次 token。
    若还有别的档案共用同一个 token_env，则保留该 token 不删。
    """
    if name not in data["profiles"] or len(data["profiles"]) <= 1:
        return
    profile = dict(data["profiles"][name])
    token_env = str(profile.get("token_env") or DEFAULT_TOKEN_ENV)
    _archive(name, profile)
    del data["profiles"][name]
    if data.get("active") == name:
        data["active"] = next(iter(data["profiles"]))
    still_used = any(
        str(p.get("token_env") or DEFAULT_TOKEN_ENV) == token_env
        for p in data["profiles"].values()
    )
    if not still_used:
        _remove_env_keys({token_env})


# ---------- 回收站（防止误删丢失映射） ----------

def _load_trash() -> list[dict]:
    try:
        data = json.loads(TRASH_PATH.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def _save_trash(items: list[dict]) -> None:
    _atomic_write_text(
        TRASH_PATH, json.dumps(items[-50:], ensure_ascii=False, indent=2) + "\n")


def _archive(name: str, profile: dict) -> None:
    items = _load_trash()
    items.append({
        "name": name,
        "deleted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "profile": profile,
    })
    _save_trash(items)


def deleted_profiles() -> list[dict]:
    """回收站内容，最新的在最后。"""
    return _load_trash()


def restore_profile(data: dict, index: int) -> str:
    """把回收站第 index 条恢复回来；重名自动加后缀。返回恢复后的档案名。"""
    items = _load_trash()
    if not (0 <= index < len(items)):
        raise ValueError("回收站里没有这一条")
    entry = items[index]
    base = str(entry.get("name") or "已删除平台")
    name = base if base not in data["profiles"] else _unique_name(data["profiles"], base)
    data["profiles"][name] = dict(entry.get("profile") or {})
    items.pop(index)
    _save_trash(items)
    return name


def rename_profile(data: dict, old: str, new: str) -> str:
    """重命名档案：保持顺序、同步 active。

    token_env 刻意不变，所以已保存的 token 改名后依然有效（不用重填）。
    名称非法或重复时抛 ValueError。
    """
    old = (old or "").strip()
    new = (new or "").strip()
    profiles = data.get("profiles") or {}
    if old not in profiles:
        raise ValueError(f"档案「{old}」不存在")
    if not new:
        raise ValueError("新名称不能为空")
    if new == old:
        return old
    if new in profiles:
        raise ValueError(f"档案「{new}」已存在，换个名字")
    data["profiles"] = {(new if k == old else k): v for k, v in profiles.items()}
    if data.get("active") == old:
        data["active"] = new
    return new


def apply(config, profile: dict) -> None:
    """把所选档案应用到运行配置（内存）。"""
    url = (profile.get("mcp_url") or "").strip()
    if url:
        config.mcp_url = url
    config.token = token_of(profile)
    config.extra["tool_map"] = dict(profile.get("tool_map") or {})


def sync_active_to_agent_json(data: dict) -> None:
    """把当前档案同步进 agent.json（mcp_url / tool_map），让 CLI 命令也用同一平台。"""
    name = data.get("active")
    prof = data["profiles"].get(name) or {}
    raw = _read_agent_json()
    if prof.get("mcp_url"):
        raw["mcp_url"] = prof["mcp_url"]
    if "tool_map" in prof:
        raw["tool_map"] = dict(prof.get("tool_map") or {})
    _atomic_write_text(
        DEFAULT_CONFIG_PATH, json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    )