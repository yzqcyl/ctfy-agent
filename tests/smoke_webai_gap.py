#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[web-ai-gap 2026-10-30] web / AI 安全「剩余短板」补齐的回归用例（独立 runner）。

覆盖截图清单里的可落地项：
  AI：verify_exploit 进工具面（checker 型自校验）、shell/decode_blob 进本地工具面、
      步数 12→14、提示词工具面条目、roles/ 与 config 零漂移。
  WEB：侦察预算条款（recon 波动 185k vs 801k）、evidence 逐字片段精度（report_rejected 13 次）、
       multipart 上传走 curl -F（upload-labs 无专用上传工具）、sqlilabs/upload-labs 专用打法卡。
不可本地修复项（平台占位/VPN-only、需 Linux 容器、M6 无 Docker）只做文档登记，不在此断言。

运行：python tests\\smoke_webai_gap.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

PASSED = FAILED = 0


def case(fn):
    global PASSED, FAILED
    try:
        fn()
        PASSED += 1
        print(f"PASS {fn.__name__}")
    except Exception as exc:  # noqa: BLE001
        FAILED += 1
        print(f"FAIL {fn.__name__}：{exc}")
    return fn


def _cfg() -> dict:
    return json.load(open(ROOT / "config" / "agent.json", encoding="utf-8"))


def _role_ai() -> dict:
    return json.load(open(ROOT / "roles" / "ai.json", encoding="utf-8"))["ai"]


# ---------------- AI 安全 ----------------

@case
def case_ai_verify_exploit_channel():
    """checker 型 AI 题要能自校验：verify_exploit 必须在 ai 平台工具面与提示词里。"""
    ai = _role_ai()
    assert "verify_exploit" in ai["platform"], ai["platform"]
    assert "verify_answer" in ai["platform"], ai["platform"]
    assert "submit_flag" not in ai["platform"] and "submit_answer" not in ai["platform"]
    prompt = open(ROOT / "roles" / "ai.system.txt", encoding="utf-8").read()
    assert "verify_exploit" in prompt, "提示词没教 checker 型题用 verify_exploit"
    assert "questions[].id" in prompt and "UUID" in prompt, "缺 instance_id/question_id 用法约束"


@case
def case_ai_shell_and_decode():
    """无 shell 是截图短板：shell + decode_blob 必须进 ai 本地工具面，且 allow_shell 打开。"""
    ai = _role_ai()
    assert ai["allow_shell"] is True, ai["allow_shell"]
    assert "shell" in ai["local"] and "decode_blob" in ai["local"], ai["local"]
    prompt = open(ROOT / "roles" / "ai.system.txt", encoding="utf-8").read()
    assert "curl -F" in prompt, "提示词没给 multipart 上传打法"
    assert "python3 -c" in prompt, "提示词没教用 shell 生成载荷"


@case
def case_ai_steps_20():
    """步数 14 → 20 → 40（与 r/p/c 统一到 40，平台无步数上限），提示词与配置一致（防漂移）。"""
    ai = _role_ai()
    assert ai["max_steps"] == 40, ai["max_steps"]
    prompt = open(ROOT / "roles" / "ai.system.txt", encoding="utf-8").read()
    assert "上限 40 步" in prompt, "提示词步数预算没同步"
    assert "上限 12 步" not in prompt


@case
def case_ai_zero_drift():
    """test/smoke_ai_role 的同款契约：config.roles.ai == roles/ai.json + ai.system.txt。"""
    live = dict(_cfg()["roles"]["ai"])
    want = dict(_role_ai())
    want["system"] = open(ROOT / "roles" / "ai.system.txt", encoding="utf-8").read().strip()
    assert live == want, sorted(set(live) ^ set(want))


# ---------------- Web ----------------

@case
def case_web_recon_budget():
    """recon 波动 4 倍：提示词必须有侦察预算上限条款。"""
    system = _cfg()["roles"]["web"]["system"]
    assert "侦察预算" in system, "web 提示词缺侦察预算条款"
    assert "http_map 最多 2 次" in system, "缺 http_map 调用上限"
    assert "1/3" in system, "缺侦察投入占总步数上限"


@case
def case_web_evidence_precision():
    """report_rejected 13 次：evidence 条款必须写清「同一份输出 + 连续 + 长度 + 不拼接」。"""
    system = _cfg()["roles"]["web"]["system"]
    assert "20–200 字符" in system, "evidence 缺长度建议"
    assert "不跨两次输出拼接" in system, "evidence 缺禁止拼接"
    assert "空白差异容忍" in system, "evidence 缺空白容忍口径"


@case
def case_web_upload_via_curl():
    """upload-labs 无专用上传工具：http_request 不支持 multipart，提示词必须指路 curl -F。"""
    system = _cfg()["roles"]["web"]["system"]
    assert "multipart" in system and "curl -F" in system, "缺 multipart 上传指路"
    # 上传打法卡也要有落点（shell 落盘生成载荷）
    from ctfy_agent.method_cards import method_cards  # noqa: PLC0415

    cards = "\n".join(method_cards(["file_upload", "zip_slip"]))
    assert "文件上传靶场" in cards, cards[:120]
    assert "curl -F" in cards, cards[:200]


@case
def case_web_sqli_labs_card():
    """sqlilabs 式 69 题：union_based/quote_breakout 等标签必须命中专用卡片。"""
    from ctfy_agent.method_cards import method_cards  # noqa: PLC0415

    for tags in (["union_based"], ["quote_breakout"], ["boolean_blind", "order_by"],
                 ["waf_bypass", "post_body"], ["stacked_queries"]):
        cards = "\n".join(method_cards(tags))
        assert "SQLi 靶场序列" in cards, (tags, cards[:120])
    # 通用注入卡与新卡叠加：先通用后专项，问题标签仍能覆盖
    both = method_cards(["sqli", "union_based"])
    assert any("注入" in c for c in both) and any("SQLi 靶场" in c for c in both), both


@case
def case_cards_no_overmatch():
    """防串味：新靶场卡不能被无关标签命中（php 97 题、tomcat 等不许拉出上传卡）。"""
    from ctfy_agent.method_cards import MAX_CARDS, method_cards  # noqa: PLC0415

    for tags in (["php"], ["flask"], ["vue3"], ["tomcat"], ["sqli"]):
        cards = method_cards(tags)
        assert not any("文件上传靶场" in c for c in cards), tags
        assert not any("SQLi 靶场序列" in c for c in cards if "sqli" not in tags), (tags, cards)
    heavy = method_cards(["file_upload", "zip_slip", "jsp_upload", "sqli", "union_based",
                          "waf_bypass", "php", "owasp_2025_a03"])
    assert 1 <= len(heavy) <= MAX_CARDS, len(heavy)


@case
def case_web_role_toolface_untouched():
    """只补提示词不动工具面：web 平台白名单必须仍是 10 个（防顺手改坏裁工具护栏）。"""
    web = _cfg()["roles"]["web"]
    assert web["platform"] == [
        "get_challenge", "list_challenge_attachments", "download_challenge_attachment",
        "list_hints", "view_hint", "start_instance",
        "list_instance_attachments", "download_instance_attachment",
        "verify_answer", "verify_exploit",
    ], web["platform"]
    assert "sqli" in web["local"] and "shell" in web["local"], web["local"]


@case
def case_ai_role_specs_resolve():
    """运行时解析：用全量目录 stub，ai 工具面恰好是白名单 10 个（含 verify_exploit）。"""
    from ctfy_agent.crew import PLATFORM_SCOPE, Crew  # noqa: PLC0415

    class _FullCatalog:
        @staticmethod
        def tool_names():
            return set().union(*PLATFORM_SCOPE.values())

    crew = Crew.__new__(Crew)
    crew.roles = {"ai": _role_ai()}
    crew.platform = _FullCatalog()
    names = crew._role_platform_names("ai")
    assert names == {
        "get_challenge", "list_challenge_attachments", "download_challenge_attachment",
        "list_hints", "view_hint", "start_instance",
        "list_instance_attachments", "download_instance_attachment",
        "verify_answer", "verify_exploit",
    }, names


@case
def case_default_ai_fallback_sync():
    """agent.json 丢失/裁剪时 ai 不退化为无 shell：DEFAULT_ROLES.ai 与活配置关键字段对齐。"""
    from ctfy_agent.crew import DEFAULT_ROLES, PLATFORM_SCOPE  # noqa: PLC0415

    d = DEFAULT_ROLES["ai"]
    live = _role_ai()
    assert d["max_steps"] == live["max_steps"] == 40, (d.get("max_steps"), live["max_steps"])
    assert d["allow_shell"] is True, "兜底 allow_shell 必须 True（与活配置同步）"
    assert {"shell", "decode_blob", "chat", "http_request", "http_map"} <= set(d["local"]), d["local"]
    # 兜底 platform 用组名（oracle = verify_answer + verify_exploit），展开后校验通道必须在
    names = set()
    for g in d["platform"]:
        names |= PLATFORM_SCOPE.get(g, {g})
    assert {"verify_answer", "verify_exploit", "start_instance"} <= names, sorted(names)


def main() -> int:
    print("=" * 60)
    print("web-ai-gap 回归：web 补救 + AI 安全工具面/步数")
    print("=" * 60)
    print(f"\n汇总：{PASSED}/{PASSED + FAILED} 通过")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
