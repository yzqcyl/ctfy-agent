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
    "schema_version": 2,
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


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_qa_test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        for fn in (
            case_qa_passes_gate,
            case_defence_still_refused,
            case_build_config_qa_flags,
            case_seed_qa_only,
        ):
            fn(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{4} passed, 0 failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
