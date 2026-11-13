#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""所有限制口径必须一致（统一取「并行存在的最高值」）。

守卫三件事：
  1) `config/agent.json`（运行时权威）== `config.py:DEFAULTS`（兜底）——同键必须同值；
  2) 角色步数/输出上限、提示词里的步数说法与运行时一致（不再出现「提示词写 20 步、跑 40 步」）；
  3) `agent_main.py:DEFAULT_*`（容器入口兜底）不低于统一值，且实例就绪默认值 180 已注入。

另外守住两条「不要回退」的语义：墙钟默认不限、oracle_limit 只是配置未接线。
运行：PYTHONPATH=src python tests/smoke_limits_consistency.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

PASSED = 0

# 统一后的期望值（改这里必须同步改 agent.json / DEFAULTS / agent_main / README / docs）
EXPECTED = {
    "max_tokens_per_challenge": 800_000,
    "max_llm_calls": 240,
    "max_total_tokens": 1_000_000_000,
    "max_tokens": 8_192,
    "max_steps": 40,
    "max_calls_per_step": 8,
    "max_context_tokens": 32_000,
    "instance_ops_limit": 5,
    "submit_limit": 5,
    "oracle_limit": 100,
    "connect_timeout": 40.0,
    "connect_deadline": 5_400.0,
    "timeout": 240.0,
    "max_cost": 0.0,
}


def ok(label: str) -> None:
    global PASSED
    PASSED += 1
    print(f"PASS {label}")


def load_agent_json() -> dict:
    return json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8-sig"))


# ---------------------------------------------------------------- 1) 配置三处一致
def case_config_consistency() -> None:
    from ctfy_agent.config import DEFAULTS, load

    agent = load_agent_json()
    for key, want in EXPECTED.items():
        assert key in agent, f"agent.json 缺 {key}"
        assert key in DEFAULTS, f"config.py DEFAULTS 缺 {key}"
        assert agent[key] == want, f"agent.json {key}={agent[key]!r}，统一口径应为 {want!r}"
        assert DEFAULTS[key] == want, f"DEFAULTS {key}={DEFAULTS[key]!r}，统一口径应为 {want!r}"
        assert agent[key] == DEFAULTS[key], f"{key} 在 agent.json 与 DEFAULTS 不一致"
    ok("agent.json 与 config.py:DEFAULTS 同键同值，且等于统一口径期望值")

    cfg = load(None)
    for key in ("max_llm_calls", "max_steps", "max_tokens", "max_context_tokens",
                "max_tokens_per_challenge", "submit_limit", "oracle_limit",
                "max_calls_per_step", "instance_ops_limit"):
        assert getattr(cfg, key) == EXPECTED[key], f"load() 生效值 {key}={getattr(cfg, key)}"
    assert float(cfg.timeout) == EXPECTED["timeout"]
    assert float(cfg.connect_deadline) == EXPECTED["connect_deadline"]
    ok("Config.load() 实际生效值与统一口径一致（不是只有文件里好看）")


# ---------------------------------------------------------------- 2) 角色 / 提示词
def case_roles_and_prompts() -> None:
    agent = load_agent_json()
    roles = agent["roles"]
    for name in ("coordinator", "web", "ai", "rev", "crypto", "pwn", "misc"):
        assert roles[name]["max_steps"] == EXPECTED["max_steps"], f"{name}.max_steps 不是 40"
    for name in ("web", "ai", "rev", "crypto", "pwn", "misc"):
        assert roles[name].get("max_tokens") == EXPECTED["max_tokens"], \
            f"{name}.max_tokens 不是 8192"
    ok("7 个角色步数=40、6 个专家输出上限=8192")

    for name, spec in roles.items():
        sys_text = str(spec.get("system") or "")
        assert "只有 20 步" not in sys_text, f"{name} 提示词还在写 20 步"
        assert "只有 16 步" not in sys_text, f"{name} 提示词还在写 16 步"
    for name in ("web", "rev", "crypto", "pwn", "misc"):
        assert "只有 40 步" in str(roles[name]["system"]), f"{name} 提示词没更新到 40 步"
    assert "上限 40 步" in str(roles["ai"]["system"]), "ai 提示词步数说法漂移"
    ok("提示词步数说法与运行时一致（40 步），不再出现 20/16 步")

    # roles/<role>.json 镜像：只守「我统一的字段」（max_steps / max_tokens / 提示词正文）。
    # 工具面（local/platform）可能在并行会话手里变动，这里只提示不判红——避免守卫挡住别人正在做的
    # 工具接线（对面优先）；真要全量一致性时跑 `python tools/sync_roles.py --check`。
    import importlib.util

    spec = importlib.util.spec_from_file_location("sync_roles", ROOT / "tools" / "sync_roles.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    live_roles = mod.load_config_roles()
    for role in mod.mirrored_roles():
        mirror = mod.read_role_spec(role)
        assert mirror is not None, f"roles/{role}.json 读不出来"
        live = live_roles.get(role) or {}
        for key in ("max_steps", "max_tokens"):
            if key in live:
                assert mirror.get(key) == live.get(key), \
                    f"roles/{role}.json 的 {key} 与 config 漂移：{mirror.get(key)} vs {live.get(key)}"
        assert (mirror.get("system") or "").strip() == (live.get("system") or "").strip(), \
            f"roles/{role}.system.txt 与 config 提示词正文漂移"
        extra = [k for k in (mod.diff_keys(live, mod.read_role_json(role) or {}) or [])
                 if k not in ("max_steps", "max_tokens", "system")]
        if extra:
            print(f"  [note] roles/{role}.json 还有工具面/其它字段漂移（并行会话可能正在改，不判红）：{extra}")
    ok("roles 镜像的关键字段（步数/输出上限/提示词）与活配置一致")


# ---------------------------------------------------------------- 3) 容器入口兜底
def case_agent_main_defaults() -> None:
    from ctfy_agent import agent_main as am

    assert am.DEFAULT_MAX_TURNS == EXPECTED["max_llm_calls"], am.DEFAULT_MAX_TURNS
    assert am.DEFAULT_MAX_TOKENS == EXPECTED["max_tokens"], am.DEFAULT_MAX_TOKENS
    assert am.DEFAULT_MAX_CONTEXT == EXPECTED["max_context_tokens"], am.DEFAULT_MAX_CONTEXT
    assert am.DEFAULT_MAX_TOKENS_PER_CHALLENGE == EXPECTED["max_tokens_per_challenge"]
    assert am.DEFAULT_INSTANCE_READY_TIMEOUT == 180, am.DEFAULT_INSTANCE_READY_TIMEOUT
    src = (ROOT / "src" / "ctfy_agent" / "agent_main.py").read_text(encoding="utf-8")
    assert 'os.environ.setdefault("AGENT_INSTANCE_READY_TIMEOUT"' in src, \
        "agent_main 没有注入实例就绪默认值 180s"
    ok("agent_main 兜底值全部对齐（240 轮 / 8192 / 32k / 800k / 就绪 180s 已注入）")


# ---------------------------------------------------------------- 4) 语义不回退
def case_semantics() -> None:
    from ctfy_agent.budget import Budget, apply_deadline
    from ctfy_agent.gui import limits as gui_limits

    assert apply_deadline(Budget(), {}).deadline_at is None, "墙钟默认必须仍是不限"
    assert apply_deadline(Budget(), {"deadline_minutes": 30}).seconds_left > 1700
    ok("墙钟默认仍为不限；显式配置才生效")

    assert gui_limits.PLATFORM_MAX_CONCURRENCY == 3, gui_limits.PLATFORM_MAX_CONCURRENCY
    ok("GUI 并发上限仍按平台规则 ≤3")

    src = (ROOT / "src" / "ctfy_agent" / "solver.py").read_text(encoding="utf-8")
    cfg_src = (ROOT / "src" / "ctfy_agent" / "config.py").read_text(encoding="utf-8")
    # oracle_limit 目前只有配置没有使用点。若并行会话以后把它接线了，这里不拦（对面优先），
    # 只提示去更新 docs/limits 的「未接线」说明，而不是让守卫变红。
    assert "oracle_limit" in cfg_src
    if "oracle_limit" in src:
        print("  [note] oracle_limit 已被 solver 使用：请更新 docs/limits-2026-11-13.md 的口径说明")
    ok("oracle_limit 口径已记录（未接线/已接线都不拦，避免影响并行会话的接线工作）")


def main() -> int:
    try:
        case_config_consistency()
        case_roles_and_prompts()
        case_agent_main_defaults()
        case_semantics()
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        raise
    print(f"\n{PASSED} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
