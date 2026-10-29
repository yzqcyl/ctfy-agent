# -*- coding: utf-8 -*-
"""集群-B × Crew（容器/生产路径）离线验收。

要点：**不联网、不花 token**——把 `Crew.solve` 换成桩，只验编排层行为：
  1. k 缺失 / k=1 → 只调 1 次 solve、summary 无 clusterb 段（= 旧版零回归）；
  2. k=3 → 调 3 次，切片按「(剩余)×(1-reserve)÷剩余次数」递减；
  3. 温度按 STRATEGY_SPECS 0.2/0.5/0.8 逐次下传，且第 2/3 次带黑板事实；
  4. 中间尝试 keep_instance=True（省重起），最后一次交回默认（None）；
  5. 早停：attempt 解出 → 不再试；题级预算耗尽（stopped）→ 不再试；
  6. 归因落盘：state.solve_attempts 每次一条。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _ok(label: str) -> None:
    """复用 smoke.py 的 ok()，让本用例计入 PASSED 总数；单独运行时退化成 print。"""
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def _mk(tmp: Path, **extra):
    from ctfy_agent.config import load as load_config
    from ctfy_agent.crew import Crew
    from ctfy_agent.state import Store

    config = load_config()
    config.mcp_url = "http://127.0.0.1:9/mcp"
    config.token = "pf_test_token"
    config.runs_dir = str(tmp / "clusterb")
    config.model = "deepseek-v4.1-flash"
    config.max_steps = 6
    config.submit_limit = 3
    config.max_tokens_per_challenge = 511_000
    config.max_llm_calls = 80
    config.extra.update(extra)

    store = Store(tmp / "clusterb").load()
    store.challenges.clear()
    store.upsert({"id": "web-01", "name": "Login Bypass", "category": "web",
                  "description": "登录处存在逻辑缺陷。"})
    store.save()

    class StubPlatform:
        """只接 solve_clusterb 用到的两个查询面。"""

        @staticmethod
        def my_solved_ids():
            return []

        @staticmethod
        def tool_names():
            return []

        @staticmethod
        def openai_tools_for(names):
            return []  # _role_specs 需要；真实平台才返回工具规格，这里空即可

    crew = Crew(config, StubPlatform(), store, lambda model=None: None)
    return crew, store, config


def _install_solve_stub(crew, script):
    """script: [{reason, tokens, facts, dead_ends, tail_action, submitted, cumulative}...]"""
    calls = []

    def fake_solve(challenge_id, *, attempt_caps=None, prior_text="", temperature=None,
                   keep_instance=None):
        idx = len(calls)
        calls.append({
            "caps": dict(attempt_caps or {}),
            "prior_text": prior_text,
            "temperature": temperature,
            "keep_instance": keep_instance,
            "idx": idx,
        })
        step = script[min(idx, len(script) - 1)]
        tokens = int(step.get("tokens") or 0)
        cumulative = int(step.get("cumulative") or tokens * (idx + 1))
        crew.store.set_tokens(challenge_id, {"total_tokens": cumulative, "calls": 10 * (idx + 1)})
        crew.store.save()
        return {
            "challenge_id": challenge_id,
            "submitted": bool(step.get("submitted")),
            "reason": step.get("reason") or "attempt_cap",
            "steps": 3,
            "tokens_spent": tokens,
            "budget": {"total_tokens": cumulative, "calls": 10 * (idx + 1)},
            "facts": list(step.get("facts") or []),
            "dead_ends": list(step.get("dead_ends") or []),
            "tail_action": step.get("tail_action") or "http_request",
        }

    crew.solve = fake_solve
    return calls


def case_clusterb_facts_reflow(tmp: Path) -> None:
    """集群-B facts 回流：run_role 返回的结论事实 → _delegate 收进 facts_sink（不回协调者文本）。

    修复根因（SAMPLE-007 实测「黑板事实继承空盒」）：crew 路径的黑板 facts 原本只读
    ledger.observations（仅 FLAG_RE 匹配才产出，非 flag 题恒空）；现在专家 run_role 的
    FactsBoard 结论（endpoint/form…）也回流，合并进 summary.facts。
    """
    import types

    crew, store, _ = _mk(tmp / "reflow")

    class FakeSolver:
        llm = types.SimpleNamespace(budget=None)

        def run_role(self, challenge_id, **kwargs):
            return {"report": "已发现 /api", "steps": 2, "reason": "ok",
                    "facts": ["endpoint:GET /api → 200", "form:user,pass"]}

    crew.llm_factory = lambda model=None: types.SimpleNamespace(budget=None, temperature=0.2)

    sink: list[str] = []
    text = crew._delegate("web-01", "web", "侦察 /api", FakeSolver(), facts_sink=sink)
    assert "endpoint:GET /api → 200" in sink and "form:user,pass" in sink, sink
    assert "GET /api" not in text, text  # 事实只进黑板，不进协调者看到的专家结论文本
    _ok("集群-B facts 回流：run_role 结论事实经 _delegate 收进 facts_sink")


def case_clusterb_crew(tmp: Path) -> None:
    from ctfy_agent.retry import STRATEGY_SPECS

    # ---------- 1) 零回归：k 缺失 / k=1 → 单次 solve，无 clusterb 段 ----------
    for extra, label in (({}, "k 缺失"), ({"clusterb_k": 1}, "k=1")):
        crew, store, _ = _mk(tmp / ("reg" + str(len(extra))), **extra)
        calls = _install_solve_stub(crew, [{"reason": "flag_accepted", "submitted": True}])
        summary = crew.solve_clusterb("web-01")
        assert len(calls) == 1, calls
        assert "clusterb" not in summary, summary
        assert calls[0]["prior_text"] == "" and calls[0]["temperature"] is None
        assert calls[0]["keep_instance"] is None and calls[0]["caps"] == {}
        assert not store.require("web-01").solve_attempts
        _ok(f"集群-B 零回归（{label}）：单次 solve、无黑板、无切片、无尝试记录")

    # ---------- 2) k=3：切片递减 / 温度 0.2-0.5-0.8 / 黑板继承 / 实例保留 ----------
    crew, store, cfg = _mk(tmp / "k3", clusterb_k=3)
    script = [
        {"reason": "attempt_cap", "tokens": 150_000, "cumulative": 150_000,
         "facts": ["/kb 与 /docs 均 200"], "dead_ends": ["盲打 /debug 无效"]},
        {"reason": "attempt_cap", "tokens": 150_000, "cumulative": 300_000,
         "facts": ["/admin/export 需要 X-Intern-al 头"]},
        {"reason": "flag_accepted", "submitted": True, "tokens": 90_000, "cumulative": 390_000},
    ]
    calls = _install_solve_stub(crew, script)
    summary = crew.solve_clusterb("web-01")

    assert len(calls) == 3, calls
    # 切片：第1次 (511000-0)*0.88/3=149893；第2次 (511000-150000)*0.88/2=158840
    #       第3次 (511000-300000)*0.88/1=185680
    caps = [c["caps"]["tokens"] for c in calls]
    assert caps == [149_893, 158_840, 185_680], caps
    assert all(c["caps"]["calls"] >= 4 for c in calls), calls
    # 温度三档
    assert [c["temperature"] for c in calls] == [s["temperature"] for s in
                                                (STRATEGY_SPECS[0], STRATEGY_SPECS[1], STRATEGY_SPECS[2])], calls
    # 黑板：第 1 次不带；第 2 次带第 1 次事实+死路；第 3 次仍带（累积）
    assert calls[0]["prior_text"] == "", calls[0]
    assert "[前次尝试已确认事实]" in calls[1]["prior_text"], calls[1]
    assert "/kb 与 /docs 均 200" in calls[1]["prior_text"], calls[1]
    assert "盲打 /debug 无效" in calls[1]["prior_text"], calls[1]
    assert "/admin/export 需要 X-Intern-al 头" in calls[2]["prior_text"], calls[2]
    # 中间尝试保留实例，最后一次交回默认
    assert [c["keep_instance"] for c in calls] == [True, True, None], calls
    # 汇总 / 归因落盘
    assert summary["submitted"] is True and summary["clusterb"]["solved"] is True, summary
    assert summary["clusterb"]["attempts"] == 3 and summary["clusterb"]["k"] == 3, summary
    assert summary["clusterb"]["temperatures"] == [0.2, 0.5, 0.8], summary
    attempts = store.require("web-01").solve_attempts
    assert [a["index"] for a in attempts] == [0, 1, 2], attempts
    assert [a["reason"] for a in attempts] == ["attempt_cap", "attempt_cap", "flag_accepted"], attempts
    assert attempts[0]["tokens_spent"] == 150_000, attempts
    assert len({a["strategy"] for a in attempts}) == 3, attempts
    _ok("集群-B k=3：3 次尝试/切片递减(149893→185680)/温度0.2-0.5-0.8/黑板累积/实例保留/归因落盘")

    # ---------- 3) 早停：第 1 次就解出 ----------
    crew, store, _ = _mk(tmp / "early", clusterb_k=3)
    calls = _install_solve_stub(crew, [{"reason": "flag_accepted", "submitted": True}])
    summary = crew.solve_clusterb("web-01")
    assert len(calls) == 1 and summary["clusterb"]["attempts"] == 1, (calls, summary)
    _ok("集群-B 早停：attempt#1 解出 → 不再尝试")

    # ---------- 4) 早停：题级预算真耗尽（stopped）→ 不白烧 ----------
    crew, store, _ = _mk(tmp / "stop", clusterb_k=3)
    calls = _install_solve_stub(crew, [{"reason": "budget_exhausted", "tokens": 150_000}])
    summary = crew.solve_clusterb("web-01")
    assert len(calls) == 1 and summary["clusterb"]["blocked"] == "budget", (calls, summary)
    _ok("集群-B 早停：attempt#1 题级 budget_exhausted → blocked=budget，不再尝试")

    # ---------- 5) 归因：attempt_cap 不被误记成题级耗尽 ----------
    crew, store, _ = _mk(tmp / "cap", clusterb_k=2)
    calls = _install_solve_stub(crew, [{"reason": "attempt_cap", "tokens": 100_000}])
    summary = crew.solve_clusterb("web-01")
    assert len(calls) == 2, calls
    assert summary["clusterb"]["blocked"] == "", summary
    assert [a["reason"] for a in store.require("web-01").solve_attempts] == ["attempt_cap", "attempt_cap"]
    _ok("集群-B 归因：attempt_cap（切片用尽）会继续尝试，不误判题级耗尽")

    # ---------- 6) 失败不污染：未解出时状态落 stalled、submitted=False ----------
    ch = store.require("web-01")
    assert summary["submitted"] is False, summary
    assert ch.status == "stalled", ch.status
    _ok("集群-B 未解出：status=stalled、submitted=False（与旧版一致）")

    # 摘要尾部打印 attempt 归因，方便故障定位
    print("      attempt 归因: " + json.dumps(
        [{"i": a["index"], "reason": a["reason"], "tok": a["tokens_spent"]}
         for a in ch.solve_attempts], ensure_ascii=False))