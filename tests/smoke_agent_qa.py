#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QA 模式接入验收（容器入口 run_agent + 配置/落库钩子）。

钉住的行为：
  - run_mode=qa 不再被拒跑：通过 run_mode 闸门，走到「无 MCP 端点 → halted」，
    而不是旧的 refused/unsupported_run_mode。
  - run_mode=defence 仍显式拒跑（不烧钱）。
  - qa 模式：_build_config 置 qa_mode=True + auto_start_instance=False；
    _seed_challenge 把 challenge 落成 is_qa_only=True（提交走 submit_qa_answer）。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURE_QA = {
    "schema_version": 3,
    "run_mode": "qa",
    "challenge": {"id": "qa-01", "name": "SecQA MCQ", "description": "……"},
    "competition_id": "arena",
    "questions": [{"id": "q1", "prompt": "……"}],
    "result": {"path": "/ctfy/work/result.json", "stdout_fallback": False},
}

BASE_ENV = {
    "LLM_FLAVOR": "openai",
    "CTFY_TOKEN": "pf_test",
    "LLM_API_KEY": "sk-test",
    "LLM_BASE_URL": "https://llm.example/v1",
    "LLM_MODEL": "m",
}

PASSED = 0


def ok(label: str) -> None:
    global PASSED
    PASSED += 1
    print(f"PASS {label}")


def _run(task: dict, env: dict, tmp: Path) -> dict:
    from ctfy_agent.agent_main import run_agent  # noqa: PLC0415

    work = tmp / "w"
    work.mkdir(exist_ok=True)
    task_path = work / "task.json"
    task_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
    result_path = work / "result.json"
    code = run_agent(task_path=task_path, work_dir=work, env=env, result_path=result_path)
    assert code == 0, code
    return json.loads(result_path.read_text(encoding="utf-8"))


def case_qa_passes_gate(tmp: Path) -> None:
    rollup = _run(FIXTURE_QA, BASE_ENV, tmp)
    assert rollup["stop_reason"] == "halted", rollup
    assert "MCP" in rollup["writeup"], rollup
    ok("qa 模式过闸门：不再 refused，走到无 MCP 端点 halted")


def case_defence_still_refused(tmp: Path) -> None:
    task = {**FIXTURE_QA, "run_mode": "defence"}
    rollup = _run(task, BASE_ENV, tmp)
    assert rollup["stop_reason"] == "refused", rollup
    assert "run_mode" in rollup["writeup"], rollup
    ok("defence 模式仍显式拒跑（不烧钱）")


def case_build_config_qa_flags(tmp: Path) -> None:
    from ctfy_agent.agent_main import _build_config  # noqa: PLC0415

    task = {"run_mode": "qa", "questions": [{"id": "q1", "prompt": "x"}], "competition_id": "c1"}
    cfg = _build_config(task, tmp, "http://x/api/v1/mcp/", {})
    assert cfg.extra.get("qa_mode") is True, cfg.extra
    assert cfg.extra.get("auto_start_instance") is False, cfg.extra
    ok("qa 模式 _build_config 置 qa_mode=True + auto_start_instance=False")


def case_seed_qa_only(tmp: Path) -> None:
    from ctfy_agent.agent_main import _seed_challenge  # noqa: PLC0415
    from ctfy_agent.state import Store  # noqa: PLC0415

    store = Store(tmp / "store").load()
    task = {"run_mode": "qa", "challenge": {"id": "qa-01", "name": "n", "description": "d"}}
    cid = _seed_challenge(store, task, {})
    assert cid == "qa-01", cid
    assert store.require(cid).is_qa_only is True
    ok("qa 模式落库 is_qa_only=True（提交走 submit_qa_answer）")


def case_llm_endpoint_allowlist(tmp: Path) -> None:
    """[规则 v2.5 审计 #6] LLM 端点白名单告警：只告警不拦截，rollup 里有字段可盯。"""
    from ctfy_agent.agent_main import (  # noqa: PLC0415
        LLM_ENDPOINT_ALLOWLIST,
        _llm_endpoint_note,
        _rollup,
    )

    # 名单内：精确命中 / 子域名命中 都不告警
    assert _llm_endpoint_note("https://dashscope.aliyuncs.com/compatible-mode/v1") == ""
    assert _llm_endpoint_note("https://open.bigmodel.cn/api/paas/v4") == ""
    assert _llm_endpoint_note("https://ark.cn-beijing.volces.com/api/v3") == ""
    ok("白名单端点（百炼/智谱/火山）不告警")
    # 名单外：本地联调域名 → 告警文本（但绝不抛错拦截）
    note = _llm_endpoint_note("https://llm.example/v1")
    assert note and "白名单" in note, note
    ok("非白名单端点产生告警文本（不拦截）")
    # 空端点：单独文案
    assert "为空" in _llm_endpoint_note(""), _llm_endpoint_note("")
    # 名单完整性：手册 v2.5 的 12 家一个不少
    must = ("api.deepseek.com", "dashscope.aliyuncs.com", "open.bigmodel.cn",
            "qianfan.baidubce.com", "api.moonshot.cn", "api.minimax.cn",
            "api.stepfun.com", "api.baichuan-ai.com", "api.xiaomimimo.com",
            "api.hunyuan.cloud.tencent.com", "spark-api-open.xf-yun.com",
            "ark.cn-beijing.volces.com")
    missing = [h for h in must if h not in LLM_ENDPOINT_ALLOWLIST]
    assert not missing, missing
    ok("白名单 12 家与手册 v2.5 一致")
    # rollup 字段透传：有告警 → 字段非空；无告警 → 空串（旧行为 + 一个恒在字段）
    r1 = _rollup(stop_reason="done", llm_endpoint_alert=note)
    assert r1["llm_endpoint_alert"] == note
    r2 = _rollup(stop_reason="done")
    assert r2["llm_endpoint_alert"] == ""
    ok("result.json 恒有 llm_endpoint_alert 字段（空=正常）")


def case_degraded_mode_in_writeup(tmp: Path) -> None:
    """[规则 v2.5 审计 #1] 降级跑的题，degraded_run_mode 必须出现在 result.json 的 writeup 里。"""
    import json as _json  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from ctfy_agent.agent_main import _final_rollup  # noqa: PLC0415
    from ctfy_agent.state import Store  # noqa: PLC0415

    store = Store(tmp / "store2").load()
    # 造一个已落库的挑战 + 一份 summary，走真实的 _final_rollup 拼装
    ch = store.upsert({"id": "challenge-x", "name": "n"})
    ch.flag_value = "FLAG{x}"
    store.save()
    config = SimpleNamespace(extra={"degraded_run_mode": "authoring", "default_question_id": "flag"})
    rollup = _final_rollup(store, "challenge-x", {"submitted": True, "reason": "done"}, config)
    assert "degraded_run_mode='authoring'" in rollup["writeup"], rollup["writeup"]
    assert rollup["llm_endpoint_alert"] == ""
    # 非降级（instance/qa 常规）不追加噪音
    config2 = SimpleNamespace(extra={"default_question_id": "flag"})
    rollup2 = _final_rollup(store, "challenge-x", {"submitted": True, "reason": "done"}, config2)
    assert "degraded_run_mode" not in rollup2["writeup"], rollup2["writeup"]
    ok("降级 run_mode 写进 result.json writeup（常规模式零噪音）")
    # 编译期自检：json 模块被引用（防将来清理 import 时误删）
    assert _json is not None


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_qa_test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    cases = (
        case_qa_passes_gate,
        case_defence_still_refused,
        case_build_config_qa_flags,
        case_seed_qa_only,
        case_llm_endpoint_allowlist,
        case_degraded_mode_in_writeup,
    )
    try:
        for fn in cases:
            fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(cases)} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
