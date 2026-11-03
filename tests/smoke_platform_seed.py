# -*- coding: utf-8 -*-
"""平台预下发实例的适配层回归（2026-11-03 实测 SAMPLE-004 的 P0 修复）。

事故形态：平台在 task.json 里给了 target_instance / target_endpoints / attachments，但适配层
只把 id/name/description 写进 state，于是容器内 agent 不知道实例存在 → 调 start_instance
（平台 token 无权）→ 403 → 判定「没有实例」→ 收口。实测代价 44 轮 / 649,031 token / $0.0715。

本用例锁死三条契约：
  1. `_seed_challenge` 把 target_instance / target_endpoints / attachments / scope 写进 state，
     并把 host:port 写进 description（模型能直接看到）；
  2. 白名单：这些端点会被 `LocalTools.allow_many(challenge.instance)` 自动登记（http 与 tcp 都通），
     非下发主机仍然被合规护栏拦住；
  3. `_build_config` 在预下发模式下置 `instance_preissued=True`（全链路禁止自启）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROUTE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROUTE / "src"))
sys.path.insert(0, str(_ROUTE))


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


TASK = {
    "schema_version": 3,
    "run_mode": "instance",
    "challenge": {
        "id": "SAMPLE-004",
        "name": "Padding Oracle Warmup — Cookie Wrapping Service",
        "description": "Our internal session-issuing service hands back wrapped session cookies.",
    },
    "competition_id": "b702278527c048ffa0f68b6e7337547f",
    "questions": [{"id": "flag", "prompt": "Capture the secret"}],
    "scope": ["sppiazvvbp42beia5.internal:31313"],
    "target_instance": "f69a8be6-1ccf-4901-a7e6-a592397f7042",
    "target_endpoints": [{"label": "chal", "type": "tcp", "url": "sppiazvvbp42beia5.internal:31313"}],
    "attachments": [{"name": "README.txt", "size": 236, "path": "/ctfy/work/attachments/README.txt"}],
}


def case_platform_seed(tmp: Path) -> None:
    from ctfy_agent.agent_main import _build_config, _seed_challenge, _split_host_port
    from ctfy_agent.state import Store
    from ctfy_agent.tools_local import LocalTools, ToolError

    # --- 0) host:port 解析：tcp 形态与 URL 形态都要认 ---
    assert _split_host_port("sppiazvvbp42beia5.internal:31313") == ("sppiazvvbp42beia5.internal", 31313)
    assert _split_host_port("https://a.example:8443/x") == ("a.example", 8443)
    assert _split_host_port("nohost") == ("nohost", None)
    _ok("P0 host:port 解析：tcp 直连形态 / URL 形态 / 无端口")

    # --- 1) seed：实例、端点、附件、范围全部落库，且写进会被模型看到的 description ---
    store = Store(tmp / "seed").load()
    store.challenges.clear()
    cid = _seed_challenge(store, TASK, {})
    challenge = store.challenges[cid]
    assert cid == "SAMPLE-004", cid
    assert challenge.instance_id == "f69a8be6-1ccf-4901-a7e6-a592397f7042", challenge.instance_id
    assert challenge.instance.get("endpoints"), challenge.instance
    endpoint = challenge.instance["endpoints"][0]
    assert endpoint["host"] == "sppiazvvbp42beia5.internal" and endpoint["port"] == 31313, endpoint
    assert challenge.instance.get("attachments"), challenge.instance
    assert "sppiazvvbp42beia5.internal:31313" in challenge.description, challenge.description
    assert "禁止调用 start_instance" in challenge.description, challenge.description
    assert "/ctfy/work/attachments/README.txt" in challenge.description, challenge.description
    _ok("P0 seed：实例/端点/附件/范围落库，host:port 与「禁止 start_instance」进 description")

    # --- 2) 白名单：下发的端点自动放行（http + tcp），其它主机仍被拦 ---
    store.save()
    local = LocalTools(store, cid, allow_shell=True)
    local.allow_many(challenge.instance)
    local._check_target("http://sppiazvvbp42beia5.internal:31313/")     # 不抛 = 放行
    local._check_endpoint("sppiazvvbp42beia5.internal", 31313)          # tcp 也放行
    try:
        local._check_target("http://evil.example.com:80/")
        raise AssertionError("非下发主机应被合规护栏拦住")
    except ToolError as exc:
        assert "合规拦截" in str(exc) or "不是平台为本题下发" in str(exc), exc
    _ok("P0 白名单：下发端点 http/tcp 均放行，非下发主机仍被拦")

    # --- 3) 预下发标记：全链路禁止自启 ---
    config = _build_config(
        TASK, tmp / "work", None,
        {"CTFY_TOKEN": "pf_demo", "LLM_API_KEY": "sk-demo", "LLM_MODEL": "m"},
    )
    assert config.extra.get("instance_preissued") is True, config.extra.get("instance_preissued")
    normal = _build_config(
        {"run_mode": "instance", "challenge": {"id": "X"}},
        tmp / "work2", None,
        {"CTFY_TOKEN": "pf_demo", "LLM_API_KEY": "sk-demo", "LLM_MODEL": "m"},
    )
    assert normal.extra.get("instance_preissued") is False, normal.extra.get("instance_preissued")
    _ok("P0 instance_preissued：有下发实例=True，普通任务=False")


if __name__ == "__main__":
    case_platform_seed(_ROUTE / "runs" / "_tmp_platform_seed")
    print("PLATFORM-SEED smoke OK")
