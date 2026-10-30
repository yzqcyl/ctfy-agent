# -*- coding: utf-8 -*-
"""P2-2 按域编排档位离线验收：只动编排参数（轮次/步数/单步工具数/集群策略），不动提示词。

覆盖：
  1. 档位选择：web→web、ai→ai、cybench→deep、ctfarena 的 rev/crypto→不覆盖（零回归）；
  2. 增量语义（只加不减）：任何档位都不会把现有参数调小，未识别题型系数为 0；
  3. 应急开关 domain_profiles=false → 一律不覆盖；热调参覆盖 + 白名单（提示词键被丢弃）；
  4. 行为：_delegate 用档位加成（web 有效步数 = 角色基线 + 4）；无档位则等于基线；
  5. 行为：deep 档在 solve_clusterb 里强制 k=1 且禁用集群-A；非 deep 题仍走集群-A（零回归）。
"""
from __future__ import annotations

import sys
from pathlib import Path


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


class _Ch:
    def __init__(self, category: str = ""):
        self.category = category


def _mk_crew(tmp: Path, **extra):
    from ctfy_agent.config import load as load_config
    from ctfy_agent.crew import Crew
    from ctfy_agent.state import Store

    config = load_config()
    config.mcp_url = "http://127.0.0.1:9/mcp"
    config.token = "pf_test_token"
    config.runs_dir = str(tmp / "p2")
    config.max_tokens_per_challenge = 511_000
    config.max_llm_calls = 80
    config.extra.update(extra)

    store = Store(tmp / "p2").load()
    store.challenges.clear()
    store.upsert({"id": "web-01", "name": "Login", "category": "web"})
    store.upsert({"id": "ai-01", "name": "Prompt", "category": "ai"})
    store.upsert({"id": "rev-01", "name": "Crackme", "category": "rev"})
    store.save()

    class StubPlatform:
        @staticmethod
        def my_solved_ids():
            return []

        @staticmethod
        def tool_names():
            return []

        @staticmethod
        def openai_tools_for(names):
            return []

    class _LLM:
        budget = None
        temperature = 0.5
        max_tokens = 1024

    return Crew(config, StubPlatform(), store, lambda model=None: _LLM()), store, config


def case_p2(tmp: Path) -> None:
    from ctfy_agent.crew import (
        DOMAIN_PROFILES, PROFILE_KEYS, domain_profile, profile_calls_cap, profile_rounds, profile_steps,
    )

    # ---- 1. 档位选择 ----
    web = domain_profile({}, _Ch("web"), "any-comp")
    assert web.get("calls_per_step_add") == 4, web
    assert (web.get("role_steps_add") or {}).get("web") == 4, web
    ai = domain_profile({}, _Ch("ai"), "any-comp")
    assert ai.get("rounds_add") == 4 and (ai.get("role_steps_add") or {}).get("ai") == 2, ai
    deep = domain_profile({}, _Ch("rev"), "CyBench-2026")
    assert deep.get("force_k1") is True and deep.get("no_cluster_a") is True, deep
    assert (deep.get("role_steps_add") or {}).get("rev") == 6, deep
    _ok("p2:档位选择 web/ai/deep 命中预期")

    assert domain_profile({}, _Ch("rev"), "ctfarena-comp") == {}, "ctfarena 非 web/ai 不应被覆盖"
    assert domain_profile({}, _Ch("crypto"), "ctfarena-comp") == {}, "crypto 不应被覆盖"
    assert domain_profile({}, _Ch(""), "unknown-comp") == {}, "空分类不应被覆盖"
    _ok("p2:未识别题型/赛题 → 空档位（零回归）")

    # ---- 2b. 显式赛题映射（cybench 实测被平台标成 category=web，只能靠映射） ----
    cyb = "d35cc057773c44f79eec192b54551327"
    mapped = domain_profile({"domain_profile_competitions": {cyb: "deep"}}, _Ch("web"), cyb)
    assert mapped.get("force_k1") is True and mapped.get("no_cluster_a") is True, mapped
    assert domain_profile({"domain_profile_competitions": {cyb: "deep"}}, _Ch("web"), "other") == {} \
        or True  # 其它赛题仍按 category 判定
    assert domain_profile({"domain_profile_competitions": {cyb: "deep"}}, _Ch("web"), "other").get("force_k1") is None
    assert domain_profile({"domain_profile_competitions": {cyb: "bogus"}}, _Ch("rev"), cyb) == {}, \
        "映射值非法时应回退到启发式（rev 无档位 → 空）"
    _ok("p2:显式赛题映射优先于 category（cybench 哈希 id → deep），非法值回退")

    # ---- 2. 增量语义：只加不减 ----
    for name, prof in DOMAIN_PROFILES.items():
        for key in ("rounds_add", "calls_per_step_add"):
            assert int(prof.get(key) or 0) >= 0, (name, key)
        for role, add in (prof.get("role_steps_add") or {}).items():
            assert int(add) >= 0, (name, role, add)
    assert profile_rounds({}, 14) == 14 and profile_rounds(web, 14) == 14
    assert profile_rounds(ai, 14) == 18, profile_rounds(ai, 14)
    assert profile_steps(web, "web", 14) == 18 and profile_steps(web, "rev", 12) == 12
    assert profile_steps(deep, "rev", 12) == 18 and profile_steps(deep, "web", 14) == 14
    assert profile_calls_cap({}, 8) == 8 and profile_calls_cap(web, 8) == 12
    _ok("p2:增量语义只加不减（未命中角色/题型一律等于基线）")

    # ---- 3. 应急开关 + 热调参白名单 ----
    assert domain_profile({"domain_profiles": False}, _Ch("web"), "x") == {}
    assert domain_profile({"domain_profiles": False}, _Ch("rev"), "cybench") == {}
    tuned = domain_profile({"domain_profile_overrides": {"web": {"rounds_add": 2, "system": "hack"}}},
                           _Ch("web"), "x")
    assert tuned.get("rounds_add") == 2, tuned
    assert "system" not in tuned, "白名单必须丢掉提示词类键"
    for key in tuned:
        assert key in PROFILE_KEYS, key
    for name, prof in DOMAIN_PROFILES.items():
        assert set(prof) <= PROFILE_KEYS, (name, set(prof) - PROFILE_KEYS)
    _ok("p2:应急开关生效；热调参保留、提示词键被白名单丢弃")

    # ---- 4. 行为：_delegate 采用档位加成 ----
    crew, store, _cfg = _mk_crew(tmp)
    base_web = int(crew.roles["web"].get("max_steps") or 0)
    seen: list[dict] = []

    class _StubLLM:
        budget = None

    class StubSolver:
        llm = _StubLLM()

        def run_role(self, cid, **kw):
            seen.append(kw)
            return {"report": "ok", "steps": 1, "reason": "report", "facts": []}

    crew._profile = domain_profile({}, _Ch("web"), "x")
    crew._delegate("web-01", "web", "抓 /api/*", StubSolver())
    assert seen[0]["max_steps"] == base_web + 4, (seen[0]["max_steps"], base_web)
    crew._profile = {}
    crew._delegate("web-01", "web", "抓 /api/*", StubSolver())
    assert seen[1]["max_steps"] == base_web, "无档位时必须等于角色基线（零回归）"
    _ok("p2:_delegate 采用档位加成；无档位回到角色基线")

    # ---- 5. 行为：solve_clusterb 的 deep 档（k=1 + 禁 A）与非 deep 零回归 ----
    crew2, _store2, _cfg2 = _mk_crew(tmp, cluster_a_concurrency=3, clusterb_k=5,
                                     cluster_a_categories="rev")
    lanes: list[str] = []
    crew2.solve = lambda cid, **kw: (lanes.append("solve"), {"submitted": False, "reason": "stub"})[1]
    crew2._solve_clustera = lambda cid, n, cfg: (lanes.append("cluster_a"), {"cluster_a": {"n": n}})[1]
    crew2._is_stateless = lambda cid, cfg: True

    crew2.solve_clusterb("rev-01")  # 非 cybench → 无 deep 档 → 应走集群-A
    assert lanes == ["cluster_a"], lanes

    crew2.store.meta["competition_id"] = "CyBench"
    lanes.clear()
    crew2.solve_clusterb("rev-01")  # cybench → deep → 强制 k=1 → 单次 solve，不并行
    assert lanes == ["solve"], lanes
    _ok("p2:deep 档强制 k=1 且禁用集群-A；非 deep 题仍走集群-A（零回归）")


def _run_standalone(tmp: Path) -> None:
    case_p2(tmp)
    print("P2-2 smoke OK")


if __name__ == "__main__":
    _run_standalone(Path(__file__).resolve().parents[1] / "runs" / "_tmp_p2_case")