#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P2-1 链段边界压缩验收：软压缩只动已闭合链段（PLAN 未变=同段），当前段内不截断。

背景（论文 2/6/9 + 本仓库实机形态）：
  长链题常见 10+ 步连续段（分页翻 JS、多步构造 payload），旧逻辑每步检查软阈值，
  一旦越过就在段中间截断旧步文本——本段推理的工作集（前几步的响应片段）被抽走。
  链段边界取模型自己声明的换方向（PLAN 变更；提示词要求计划不变就原样复制，
  变了=换段，这是现成的确定性界标，不引入任何新提示词）。

钉住的行为：
  1. seg_start=None 与旧实现输出逐字节一致（零回归兜底）；
  2. 软压缩只动下标 < seg_start 的闭合链段；当前段与最近 3 步保持原文；
  3. seg_start=0（自始至终一段）→ 软压缩完全不动作；
  4. 硬窗优先：单段超 max_tokens 仍整块丢最旧直到塞下（API 约束 > 段保护），配对不拆散；
  5. 行为：run_role 真跑 14 步（第 7 步换 PLAN），后期请求里段 A（步 1-6）已压缩、
     段 B（步 7 起）原文完整——任何请求中当前段都不出现压缩标记。
"""
from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ctfy_agent.solver import (  # noqa: E402
    KEEP_TAIL_GROUPS, SOFT_COMPACT_RATIO, Solver, _compact_history, _est_tokens,
    _flatten, _group_messages, _shrink_group,
)
from ctfy_agent.testing import fake_llm  # noqa: E402
from ctfy_agent.testing.fake_llm import tool_call  # noqa: E402

MARKER = "旧步骤已压缩"


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


# ---------- 造消息：n 步，每步 assistant(tool_calls) + 一个长 tool 结果 ----------

def _mk_messages(n: int, tool_chars: int = 3000) -> list[dict]:
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task-head"},
    ]
    for i in range(n):
        msgs.append({
            "role": "assistant", "content": f"PLAN: 步{i} 思考", "tool_calls": [
                {"id": f"c{i}", "type": "function",
                 "function": {"name": "http_request", "arguments": "{}"}},
            ],
        })
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * tool_chars})
    return msgs


def _tool_texts(messages: list[dict]) -> list[str]:
    return [str(m.get("content") or "") for m in messages if m.get("role") == "tool"]


def _old_compact(messages: list[dict], max_tokens: int) -> list[dict]:
    """改动前的实现原样拷贝，供逐字节回归比对。"""
    if len(messages) <= 2:
        return messages
    head, groups = _group_messages(messages)
    if _est_tokens(messages) <= max_tokens * SOFT_COMPACT_RATIO:
        return messages
    if len(groups) > KEEP_TAIL_GROUPS:
        cut = len(groups) - KEEP_TAIL_GROUPS
        compressed = [_shrink_group(g) if i < cut else g for i, g in enumerate(groups)]
        flattened = _flatten(head, compressed)
        if _est_tokens(flattened) <= max_tokens:
            return flattened
        groups = compressed
    budget = max_tokens - _est_tokens(head)
    kept: list[list[dict]] = []
    used = 0
    for group in reversed(groups):
        cost = _est_tokens(group)
        if used + cost > budget:
            if not kept:
                kept = [group]
            break
        kept.insert(0, group)
        used += cost
    return _flatten(head, kept)


def case_p21(tmp: Path) -> None:
    # ---------- 1. seg_start=None 与旧实现逐字节一致 ----------
    for n, chars, max_tok in ((12, 3000, 15000), (12, 3000, 4000), (12, 3000, 26000),
                              (5, 3000, 15000), (14, 900, 2600)):
        msgs = _mk_messages(n, chars)
        a = json.dumps(_compact_history(msgs, max_tok), ensure_ascii=False, sort_keys=True)
        b = json.dumps(_old_compact(msgs, max_tok), ensure_ascii=False, sort_keys=True)
        assert a == b, f"seg_start=None 必须与旧实现一致：n={n} chars={chars} max={max_tok}"
    _ok("p21:seg_start=None 与旧实现逐字节一致（零回归兜底）")

    # ---------- 2. 软压缩只动闭合链段，当前段保持原文 ----------
    msgs = _mk_messages(12, 3000)
    assert _est_tokens(msgs) > 15000 * SOFT_COMPACT_RATIO, "用例尺寸应越过软阈值"
    new = _compact_history(msgs, 15000, seg_start=6)   # 当前段 = group 6..11（步 7..12）
    texts = _tool_texts(new)
    assert len(texts) == 12, "软压缩不丢整步"
    for i in range(6):
        assert MARKER in texts[i], f"闭合段 group{i} 应被压缩"
    for i in range(6, 12):
        assert MARKER not in texts[i] and len(texts[i]) == 3000, f"当前段 group{i} 必须保持原文"
    old_texts = _tool_texts(_old_compact(msgs, 15000))
    assert MARKER in old_texts[8], "旧实现会截到 group8（=新逻辑的当前段内），行为确有差异"
    _ok("p21:软压缩只动闭合链段；当前段（含 KEEP_TAIL 之外更早的段内步）保持原文")

    # ---------- 3. 自始至终一段 → 软压缩完全不动作 ----------
    same = _compact_history(msgs, 15000, seg_start=0)
    texts = _tool_texts(same)
    assert all(MARKER not in t for t in texts), "单段历史不应出现压缩标记"
    assert all(len(t) == 3000 for t in texts), "单段历史应保持原文"
    assert len(same) == len(msgs), "软阈值内不应丢步"
    _ok("p21:seg_start=0（全程一段）→ 软压缩不动作，原文保留")

    # ---------- 4. 硬窗优先：段保护不能挡住 API 窗约束 ----------
    hard = _compact_history(msgs, 4000, seg_start=0)
    assert _est_tokens(hard) <= 4000, f"硬窗必须塞得下：est={_est_tokens(hard)}"
    texts = _tool_texts(hard)
    assert len(texts) < 12 and len(texts[-1]) == 3000, "整块丢最旧、最新一步保持原文"
    head, groups = _group_messages(hard)
    for g in groups:  # 配对不拆散：每步 assistant 的 tool_calls 都有对应 tool 结果
        ids = {c.get("id") for c in g[0].get("tool_calls", [])} if g[0].get("role") == "assistant" else set()
        got = {m.get("tool_call_id") for m in g if m.get("role") == "tool"}
        assert ids <= got, "tool_call 配对必须整块保留"
    _ok("p21:硬窗优先（超 max_tokens 仍整块丢最旧到塞下），配对不拆散")

    # ---------- 5. 行为：run_role 真跑，换 PLAN 后旧段压缩、新段全程原文 ----------
    _behavioral(tmp)
    _ok("p21:行为链 14 步（步 7 换 PLAN）：段 A 压缩、段 B 原文、任何请求当前段无标记")


class _Handler(BaseHTTPRequestHandler):
    """GET /p21?i=N 返回含序号的 4000 字符正文（进上下文前被裁到 ~1600）。"""

    def log_message(self, *args):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802
        from urllib.parse import urlparse  # noqa: PLC0415

        q = urlparse(self.path).query
        body = (f"<html>page-{q} " + "x" * 4000 + "</html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _behavioral(tmp: Path) -> None:
    from ctfy_agent.config import load as load_config
    from ctfy_agent.platform import CtfyPlatform
    from ctfy_agent.state import Store

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    mcp, mcp_url = fake_llm.start_fake_mcp()
    config = load_config()
    config.mcp_url = mcp_url
    config.token = "pf_test_token_123456"
    config.runs_dir = str(tmp / "p21")
    config.max_context_tokens = 6800  # 软阈值 3740 ≈ 第 7 步越线；软压后全历史可容纳（不触发硬丢）
    platform = CtfyPlatform(mcp_url, config.token)
    platform.connect()
    store = Store(tmp / "p21").load()
    store.challenges.clear()
    ch = store.upsert({"id": "web-01", "name": "Chain", "category": "web",
                       "points": 100, "description": "长链分页题"})
    ch.instance = {"id": "inst-1", "surface": {"services": [
        {"service_type": "http", "host": "127.0.0.1", "port": port}
    ]}}
    store.save()

    base = f"http://127.0.0.1:{port}"
    script = []
    for i in range(1, 15):
        plan = "A 枚举端点" if i <= 6 else "B 构造利用"
        script.append({
            "content": f"PLAN: {plan}\nREFLECTION: 第{i}步",
            "tool_calls": [tool_call("http_request", {"url": f"{base}/p21?i={i}"})],
        })
    llm = fake_llm.ScriptedLLM(script)

    solver = Solver(config, platform, fake_llm.ScriptedLLM([]), store)
    try:
        result = solver.run_role(
            "web-01", role="web", llm=llm, system="s", task="t", specs=[],
            max_steps=14, platform_names=solver._platform_names(),
        )
    finally:
        server.shutdown()
        mcp.shutdown()

    assert result["steps"] == 14, f"应跑满 14 步：{result}"
    assert len(llm.received) >= 14, len(llm.received)  # 第 15 次可能是步数耗尽后的救援收口

    # 任何一次请求：历史恰好是已完成的步（不触发硬丢整块），当前段（步 7 起）不出现压缩标记
    for k, messages in enumerate(llm.received[:14]):
        texts = _tool_texts(messages)
        assert len(texts) == k, f"call{k} 应有前 {k} 步历史，实际 {len(texts)}（硬窗不应触发）"
        for idx in range(6, len(texts)):
            assert MARKER not in texts[idx], f"call{k} 当前段 group{idx} 被截断"

    # 第 14 步的请求（received[13]）：段 A（步 1-6 = group 0-5）已全部压缩，段 B 原文
    final = _tool_texts(llm.received[13])
    assert len(final) == 13, len(final)  # call14 的历史 = 步 1..13
    for i in range(6):
        assert MARKER in final[i], f"闭合段步{i + 1} 应已压缩"
    for i in range(6, 13):
        assert MARKER not in final[i] and len(final[i]) >= 1000, \
            f"当前段步{i + 1} 必须保持原文（~1600 字符裁剪版）"


def _run_standalone(tmp: Path) -> None:
    case_p21(tmp)
    print("P2-1 smoke OK")


if __name__ == "__main__":
    _run_standalone(ROOT / "runs" / "_tmp_p21_case")
