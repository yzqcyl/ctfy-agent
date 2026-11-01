#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S1 上下文结构优化验收（阶段1：稳定前缀 / 思考护栏 / 轮间会话重置）。

钉住的行为：
  S1a 稳定前缀：run_role 全程 messages[1]（任务头）字节不变；事实板/预算行是
      「尾部时变消息」——出现在每次请求末尾、但不进历史（下一步请求里没有它）。
  S1c 思考护栏：finish=length 且无输出 = 思考烧穿——空 assistant 不回喂历史；
      连续 2 次终止（reason=completion_burned）；烧穿 1 次后恢复正常则继续。
  S1b 轮间会话重置：crew 第 2 轮协调者消息 = [system, 任务头, 摘要卡(, 预算行)]，
      不携带第 1 轮的 assistant/tool 历史；extra.fresh_sessions=false 恢复旧行为。
"""
from __future__ import annotations

import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.crew import Crew  # noqa: E402
from ctfy_agent.platform import CtfyPlatform  # noqa: E402
from ctfy_agent.solver import Solver  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402
from ctfy_agent.testing import fake_llm, fake_mcp_server  # noqa: E402
from ctfy_agent.testing.fake_llm import tool_call  # noqa: E402

TOKEN = "pf_test_token_123456"


def _ok(label: str) -> None:
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = (f"<html>ok " + "y" * 800 + "</html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # 静音
        pass


def _make_config(url: str, tmp: Path):
    from ctfy_agent.config import load as load_config

    config = load_config()
    config.mcp_url = url
    config.token = TOKEN
    config.runs_dir = str(tmp)
    config.model = "deepseek-v4.1-flash"
    config.max_steps = 6
    return config


class _BurnLLM(fake_llm.ScriptedLLM):
    """支持 finish_reason 的脚本化 LLM（默认恒 stop）。"""

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        self.received.append([dict(m) for m in messages])
        step = self.script.pop(0) if self.script else {"content": "", "tool_calls": []}
        return {
            "content": step.get("content") or "",
            "tool_calls": step.get("tool_calls") or [],
            "finish_reason": step.get("finish_reason", "stop"),
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "raw": {},
        }


def _seed(store: Store):
    store.challenges.clear()
    store.upsert({"id": "web-01", "name": "Stable", "category": "web",
                  "points": 100, "description": "前缀稳定题"})
    store.save()


# ---------- S1a + S1c：run_role 路径 ----------

def case_s1a_s1c(tmp: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    mcp, mcp_url = fake_llm.start_fake_mcp()
    try:
        config = _make_config(mcp_url, tmp / "s1a")
        config.max_context_tokens = 32000
        platform = CtfyPlatform(mcp_url, config.token)
        platform.connect()
        store = Store(tmp / "s1a").load()
        _seed(store)
        ch = store.require("web-01")
        ch.instance = {"id": "i1", "surface": {"services": [
            {"service_type": "http", "host": "127.0.0.1", "port": port}]}}
        store.save()
        base = f"http://127.0.0.1:{port}"
        solver = Solver(config, platform, fake_llm.ScriptedLLM([]), store)

        # ---- S1a：4 步 http_request，任务头必须字节不变，事实板走尾部 ----
        script = [{
            "content": f"PLAN: A 探测 第{i}步",
            "tool_calls": [tool_call("http_request", {"url": f"{base}/s1a?i={i}"})],
        } for i in range(1, 5)]
        llm = fake_llm.ScriptedLLM(script)
        result = solver.run_role("web-01", role="web", llm=llm, system="s", task="t",
                                 specs=[], max_steps=4,
                                 platform_names=solver._platform_names())
        assert result["steps"] == 4, result
        heads = {m_list[1]["content"] for m_list in llm.received}
        assert len(heads) == 1, f"messages[1] 应字节稳定，实际 {len(heads)} 种：{[h[:40] for h in heads]}"
        # 第 2 步起：尾部时变消息（事实板）存在且是最后一条
        for k in range(1, len(llm.received)):
            tail = llm.received[k][-1]
            assert tail["role"] == "user" and tail["content"].strip(), f"call{k} 尾部消息缺失"
            # 首步（call0）无尾部（事实板还是空的），跳过泄漏检查
            if k - 1 == 0 and len(llm.received[0]) <= 2:
                continue
            prev_tail = llm.received[k - 1][-1]["content"]
            persisted = [m["content"] for m in llm.received[k][:-1]]
            assert prev_tail not in persisted, f"call{k-1} 的尾部时变消息泄漏进历史"
        _ok("S1a 任务头字节稳定；事实板=尾部时变消息，不入历史")

        # ---- S1c：烧穿×2 → completion_burned；烧穿×1 后恢复 → 继续 ----
        burn2 = _BurnLLM([
            {"content": "", "finish_reason": "length"},
            {"content": "", "finish_reason": "length"},
        ])
        r2 = solver.run_role("web-01", role="web", llm=burn2, system="s", task="t",
                             specs=[], max_steps=6,
                             platform_names=solver._platform_names())
        assert r2["reason"] == "completion_burned", r2
        assert r2["steps"] == 2, r2
        assert len(burn2.received) == 2, len(burn2.received)
        # 第 2 次请求的历史与第 1 次等长：空 assistant 没有回喂
        assert len(burn2.received[1]) == len(burn2.received[0]), "烧穿步的空消息不应回喂历史"

        burn1 = _BurnLLM([
            {"content": "", "finish_reason": "length"},
            {"content": "PLAN: A 恢复",
             "tool_calls": [tool_call("http_request", {"url": f"{base}/recover"})]},
            {"content": "结论：侦察完成，/recover 返回 200。"},
        ])
        r1 = solver.run_role("web-01", role="web", llm=burn1, system="s", task="t",
                             specs=[], max_steps=6,
                             platform_names=solver._platform_names())
        assert r1["reason"] == "no_more_actions", r1
        assert "侦察完成" in r1["report"], r1
        assert r1["steps"] == 3 and len(burn1.received) == 3, r1
        _ok("S1c 思考烧穿不回喂；连续2次终止=completion_burned，单次可恢复")
    finally:
        server.shutdown()
        mcp.shutdown()


# ---------- S1b：crew 轮间会话重置 ----------

def case_s1b(tmp: Path, fresh: bool = True) -> fake_llm.ScriptedLLM:
    mcp, mcp_url = fake_llm.start_fake_mcp()
    try:
        config = _make_config(mcp_url, tmp / ("s1b" if fresh else "s1b_off"))
        config.extra["fresh_sessions"] = fresh
        config.extra["roles"] = {
            "coordinator": {"model": "coord-script", "max_steps": 4},
            "web": {"max_steps": 4, "allow_shell": False,
                    "platform": ["read", "instance", "oracle"], "local": ["http_request"],
                    "model": "web-script"},
        }
        platform = CtfyPlatform(mcp_url, config.token)
        platform.connect()
        store = Store(tmp / ("s1b" if fresh else "s1b_off")).load()
        _seed(store)

        coord = fake_llm.ScriptedLLM([
            {"content": "PLAN: 派 web 找名字",
             "tool_calls": [tool_call("delegate", {"role": "web", "objective": "拿 /api/team 里的名字",
                                                   "done_when": "拿到名字清单"})]},
            {"content": "PLAN: 按结论提交",
             "tool_calls": [tool_call("submit", {"answer": fake_mcp_server.ACCEPT_FLAG})]},
            {"content": "收工", "tool_calls": [tool_call("done", {})]},
        ])
        web = fake_llm.ScriptedLLM([
            {"content": "", "tool_calls": [tool_call("get_challenge", {"challenge_id": "web-01"})]},
            {"content": "PLAN: 直接报告",
             "tool_calls": [tool_call("report", {"text": "拿到名字：admin", "evidence": '"name": "Login Bypass"'})]},
        ])
        factory = lambda model=None: {"coord-script": coord, "web-script": web}.get(model, coord)  # noqa: E731
        crew = Crew(config, platform, store, factory)
        summary = crew.solve("web-01")
        assert summary.get("submitted") is True, summary
        assert len(coord.received) >= 2, "至少两轮协调者调用"
        return coord
    finally:
        mcp.shutdown()


def case_s1b_run(tmp: Path) -> None:
    coord = case_s1b(tmp, fresh=True)
    round2 = coord.received[1]
    brief = coord.received[0][1]["content"]
    assert round2[1]["content"] == brief, "第 2 轮任务头应与第 1 轮一致（前缀稳定）"
    assert len(round2) <= 4, f"第 2 轮应为 [system,任务头,摘要卡(,预算行)]，实际 {len(round2)} 条"
    digest_msg = round2[2]["content"]
    assert "轮间摘要卡" in digest_msg and "admin" in digest_msg, digest_msg[:120]
    assert all(m["role"] in ("system", "user") for m in round2), "第 2 轮不应携带第 1 轮的 assistant/tool 历史"
    _ok("S1b 第2轮会话重置：[system,任务头,摘要卡]，结论进入摘要、链路仍提交成功")

    coord_off = case_s1b(tmp, fresh=False)
    round2_off = coord_off.received[1]
    assert len(round2_off) > 4, f"关闭开关时应携带完整跨轮历史，实际 {len(round2_off)} 条"
    assert any(m.get("role") in ("assistant", "tool") for m in round2_off), "旧行为应含 assistant/tool 历史"
    _ok("S1b 开关回退：fresh_sessions=false 恢复旧行为（携带全量历史）")


def case_s2(tmp: Path) -> None:
    """S2a keep_tail 按角色 / S2b 事实板逐族上限与永不驱逐。"""
    from ctfy_agent.ctxcompress import FactsBoard
    from ctfy_agent.solver import KEEP_TAIL_GROUPS, _compact_history

    MARKER = "[旧步骤已压缩"

    def _mk(n: int, chars: int = 3000) -> list[dict]:
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task-head"}]
        for i in range(n):
            msgs.append({"role": "assistant", "content": f"PLAN: 步{i}",
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "http_request", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * chars})
        return msgs

    def _tool_texts(messages: list[dict]) -> list[str]:
        return [m["content"] for m in messages if m.get("role") == "tool"]

    # ---- S2a：seg_start=None 时，keep_tail 直接决定尾部保护窗 ----
    base = _mk(8)
    out5 = _compact_history([dict(m) for m in base], 6500, keep_tail=5)
    shrunk5 = [t for t in _tool_texts(out5) if MARKER in t]
    kept5 = [t for t in _tool_texts(out5) if MARKER not in t]
    assert len(shrunk5) == 3 and len(kept5) == 5, (len(shrunk5), len(kept5))
    out3 = _compact_history([dict(m) for m in base], 6500)  # 默认 KEEP_TAIL_GROUPS
    shrunk3 = [t for t in _tool_texts(out3) if MARKER in t]
    assert len(shrunk3) == 8 - KEEP_TAIL_GROUPS, len(shrunk3)
    # 硬窗优先不受 keep_tail 影响：超窗仍整块丢最旧（API 约束 > 角色偏好）
    out_hard = _compact_history([dict(m) for m in base], 2600, keep_tail=5)
    assert len(_tool_texts(out_hard)) < 8, "硬窗应仍触发整块丢弃"
    _ok("S2a keep_tail=5 保护尾 5 组（默认 3）；硬窗优先级不变")

    # ---- S2b：事实板逐族上限 + 永不驱逐 ----
    fb = FactsBoard(max_facts=8, max_chars=520)
    for i in range(1, 9):
        fb._add(f"endpoint:GET /e{i}", f"已访问 GET /e{i} → 200")
    eps = [k for k in fb._facts if k.startswith("endpoint:")]
    assert len(eps) == 6, f"端点族应滑窗到 6，实际 {len(eps)}"
    assert "endpoint:GET /e1" not in fb._facts and "endpoint:GET /e8" in fb._facts, "应丢最旧保最新"

    fb._add("flag", "候选 flag：FLAG{aa11}（下一步 submit 验证）")
    fb._add("session", "会话：cookie jar 已建立")
    assert "flag" in fb._facts and "session" in fb._facts, "flag/会话必须已记录"
    # 总量满员（6 端点 + flag + session = 8）：新的软事实拒收，不挤硬值
    fb._add("href:/x", "链接 /x")
    assert "href:/x" not in fb._facts, "软事实不应在满员时挤入"
    # 高价值新事实（tool 面 prio1）进来 → 驱逐最旧端点，flag/会话不动
    fb._add("tool:ai_search", "AI 工具面：可调用 ai_search()")
    assert "tool:ai_search" in fb._facts and "flag" in fb._facts
    assert len([k for k in fb._facts if k.startswith("endpoint:")]) == 5, "应驱逐一个端点"
    # flag 族上限 4：第 5 个候选拒收（保留最先出现的）
    fb2 = FactsBoard(max_facts=16, max_chars=520)
    for i in range(5):
        fb2._add(f"flag:FLAG{{v{i}}}", f"候选 flag：FLAG{{v{i}}}")
    flags = [k for k in fb2._facts if k.split(":")[0] == "flag"]
    assert len(flags) == 4 and "flag:FLAG{v4}" not in fb2._facts, f"flag 族应封顶 4，实际 {flags}"
    # 渲染：预算内 flag/会话永远在最前且不被截掉
    r = fb.render()
    assert len(r) <= 520 and "候选 flag" in r and "会话" in r, r[:100]
    _ok("S2b 事实板：端点滑窗6、flag族封顶4、满员只驱软、flag/会话永存且渲染优先")


def case_a(tmp: Path) -> None:
    """S-A2 elision 开关 / S-A3 预算缓存折价（默认全量计）。"""
    from ctfy_agent.budget import Budget
    from ctfy_agent.solver import _compact_history

    MARKER = "[旧步骤已压缩"

    def _mk(n: int, chars: int = 3000) -> list[dict]:
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task-head"}]
        for i in range(n):
            msgs.append({"role": "assistant", "content": f"PLAN: 步{i}",
                         "tool_calls": [{"id": f"c{i}", "type": "function",
                                         "function": {"name": "http_request", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * chars})
        return msgs

    # ---- A2：elide 模式整组丢弃，配对完整；默认 truncate 逐字节旧行为 ----
    base = _mk(8)
    out = _compact_history([dict(m) for m in base], 6500, soft_mode="elide")
    tools = [m for m in out if m.get("role") == "tool"]
    assert len(tools) == 3, f"elide 默认保尾 3 组、丢前 5 组，实际剩 {len(tools)}"
    assert not any(MARKER in m["content"] for m in tools), "elide 模式不应出现截断标记"
    ids_called = {c["id"] for m in out if m.get("role") == "assistant" for c in (m.get("tool_calls") or [])}
    ids_tool = {m["tool_call_id"] for m in tools}
    assert ids_tool <= ids_called, "丢弃后 tool_call 配对必须完整"
    out_t = _compact_history([dict(m) for m in base], 6500)
    assert len([m for m in out_t if m.get("role") == "tool"]) == 8, "默认 truncate 不丢组"
    assert sum(1 for m in out_t if m.get("role") == "tool" and MARKER in m["content"]) == 5
    _ok("A2 elide=整组丢且配对完整；默认 truncate 行为不变")

    # ---- A3：缓存折价记账（默认全量，折价只影响计数口径，审计计数恒在） ----
    u = {"prompt_tokens": 10000, "completion_tokens": 100,
         "prompt_tokens_details": {"cached_tokens": 9000}}
    b0 = Budget()
    b0.record(u)
    assert b0.prompt_tokens == 10000 and b0.cached_tokens == 9000, "默认=全量计，cached 仅统计"
    b1 = Budget(cache_discount=True)
    b1.record(u)
    assert b1.prompt_tokens == 1900, f"折价=(10000-9000)+900，实际 {b1.prompt_tokens}"
    snap = b1.snapshot()
    assert snap["cached_tokens"] == 9000 and snap["raw_prompt_tokens"] == 10000
    b2 = Budget(cache_discount=True)
    b2.record({"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 500}})
    assert b2.prompt_tokens == 10, "脏数据防御：cached 钳到 100 后仍按折价计 (0+10)"
    assert b2.cached_tokens == 100, "审计计数同样按钳制值记录"
    _ok("A3 缓存折价：默认全量计；开关=命中按1/10；cached/raw 审计计数恒记")


def case_ctxopt(tmp: Path) -> None:
    case_s1a_s1c(tmp)
    case_s1b_run(tmp)
    case_s2(tmp)
    case_a(tmp)


if __name__ == "__main__":
    case_ctxopt(ROOT / "runs" / "_tmp_ctxopt")
    print("CTXOPT smoke OK")
