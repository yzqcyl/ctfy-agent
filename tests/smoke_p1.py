#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 验收：工具 schema 按实测门控（traffic）+ 事实块硬值保真（ctxcompress）。

依据（docs/report-large-scale-test.md §10.7 + 本会话补测）：
  - bank-500k-v1 全部 1495 次工具调用中，traffic 工具仅 9 次且全部出自 web 角色，
    但其 schema（~413 tok）无条件随每次调用发给所有角色；
  - 压缩只保留 flag/路径/凭据/表单的逐字片段，JWT/长hex id/ISO 时间戳/token 键值
    会被压掉 → 模型重新获取（Sleeping Agent 2608.11775：标识符改写=churn 主因）；
  - FactsBoard render 按 520 字符窗口截断，先到的端点事实会把后发现的 flag 挤掉。

判定项：
  - 硬值保真：JWT/sha256/ISO 时间戳/api_key 键值在压缩输出中逐字存在；
  - 多 flag 候选：不同值各占一条（按值去重），首个沿用固定键；
  - 渲染优先级：flag/会话/组件先于端点，窗口不够时软事实先被截；
  - traffic 门控：仅 web（默认集）与显式声明 local 的角色可见，traffic_enabled=false 全关。

并入方式：smoke.py 顶部 `from smoke_p1 import case_p1`；cases 加 `("p1", case_p1)`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.ctxcompress import FactsBoard, shrink_tool_text  # noqa: E402


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def case_p1(tmp: Path) -> None:  # noqa: ARG001
    # ---------- 1) 硬值保真：压缩后逐字保留 ----------
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    sha = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    stamp = "2026-10-29T21:48:07"
    big = (
        "<html><body>" + "filler " * 400
        + f' session_token={jwt} '
        + "filler " * 100
        + f' checksum: {sha} '
        + "filler " * 100
        + f' created_at="{stamp}" '
        + ' "api_key": "sk-live-9f2c7b1a4d6e8f0a1234" '
        + "filler " * 100
        + "</body></html>"
    )
    slim = shrink_tool_text(big, 1600)
    assert jwt in slim, "JWT 必须逐字保留"
    assert sha in slim, "sha256 hex id 必须逐字保留"
    assert stamp in slim, "ISO 时间戳必须逐字保留"
    assert "sk-live-9f2c7b1a4d6e8f0a1234" in slim, "api_key 键值必须逐字保留"
    assert len(slim) < len(big), "长文本仍应被压缩"

    # ---------- 2) 多 flag 候选（不同值各一条，同值去重）----------
    board = FactsBoard()
    board.observe("http_request", {"url": "https://h/t1"}, "FLAG{aaaaaaaaaaaaaaaaaaaa}")
    board.observe("http_request", {"url": "https://h/t1"}, "FLAG{aaaaaaaaaaaaaaaaaaaa}")  # 同值去重
    board.observe("http_request", {"url": "https://h/t2"}, "FLAG{bbbbbbbbbbbbbbbbbbbb}")
    facts = board.as_list()
    flags = [f for f in facts if "候选 flag" in f]
    assert len(flags) == 2, f"两个不同 flag 应各占一条，实得 {flags}"
    assert board.quotable_flag() == "FLAG{aaaaaaaaaaaaaaaaaaaa}", "首个 flag 仍是可引用原串"

    # ---------- 3) 渲染优先级：后发现的 flag 不被先到的端点挤掉 ----------
    late = FactsBoard(max_facts=8, max_chars=520)
    for i in range(8):  # 先灌满软事实（端点）
        late.observe("http_request", {"url": f"https://h.example/very/long/path/{i}"}, '{"status": 200}')
    late.observe("http_request", {"url": "https://h.example/final"}, "FLAG{latebutcritical1234}")
    rendered = late.render()
    assert "FLAG{latebutcritical1234}" in rendered, f"后发现的 flag 被窗口挤掉：\n{rendered}"
    assert rendered.index("FLAG{latebutcritical1234}") < rendered.index("/very/long/path"), \
        "flag 行应排在端点软事实之前"
    assert len(rendered) <= 520

    # ---------- 4) traffic 门控（实测：9/9 次调用全出自 web）----------
    from ctfy_agent.config import load as load_config
    from ctfy_agent.crew import Crew
    from ctfy_agent.state import Store

    class _StubPlatform:
        @staticmethod
        def tool_names():
            return []

        @staticmethod
        def openai_tools_for(names):
            return []

    config = load_config()
    config.runs_dir = str(tmp / "p1_gate")
    store = Store(tmp / "p1_gate").load()
    store.challenges.clear()
    store.upsert({"id": "x", "name": "x", "category": "web"})
    store.save()
    crew = Crew(config, _StubPlatform(), store, lambda model=None: None)

    def names_of(role: str) -> set[str]:
        return {s["function"]["name"] for s in crew._role_specs(role)}

    assert "traffic" in names_of("web"), "web 默认应可见 traffic（实测唯一使用方）"
    for role in ("coordinator", "rev", "crypto", "pwn", "misc", "ai"):
        assert "traffic" not in names_of(role), f"{role} 不应无条件携带 traffic schema"
    # 显式声明可见：local 里加 "traffic" 的角色应拿回
    crew.roles["rev"] = {**crew.roles["rev"], "local": [*crew.roles["rev"].get("local", []), "traffic"]}
    assert "traffic" in names_of("rev"), "local 显式声明 traffic 应生效"
    # traffic_enabled=false：web 也关
    crew.config.extra["traffic_enabled"] = False
    assert "traffic" not in names_of("web"), "traffic_enabled=false 应全关"
    crew.config.extra["traffic_enabled"] = True

    _ok("P1：traffic 按角色门控（实测 9/9=web）+ 事实块硬值保真/多候选/渲染优先级")


if __name__ == "__main__":
    import shutil
    import traceback

    tmp = ROOT / "runs" / "_tmp_p1_case"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        case_p1(tmp)
        print("OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
