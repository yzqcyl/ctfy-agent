#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_action_loop.py — 重复被拦动作断路器（crew.solve 的 action_loop 护栏）回归。

背景（2026-11-10 从 runs/plat 转录核实，26 题 13 解）
--------------------------------------------------
7 道题把**同一个已被提交闸门拦下的答案**连交 34~39 次：
  satisfied 96 次调用里 35 次、t9 51 次里 38 次、winxy-pistol 86 次里 37 次、
  spn 88 次里 35 次、Not My Mod Files 105 次里 35 次、TranscendentRenovation 88 次里 37 次、
  vorpal-sword 160 次里 14 次。
闸门每次都正确拦截（只有第 1 次真到平台），但模型看到拦截文案仍原样重试，
单题 40%~75% 的调用预算被零信息量循环烧掉，直接导致「撞 max_llm_calls」的假象。

本测试用**脚本化假 LLM + 假平台**离线驱动 crew.solve，断言：
  1) 同一动作连续被拦 → 输出断路器提示、该工具从下一轮工具面消失；
  2) 连续到上限 → reason=action_loop 收口，且只真提交了 1 次（不是 40 次）；
  3) 动作不重复时断路器完全不介入（对既有行为透明）。
不连真实平台、不消耗额度。

运行: PYTHONPATH=src python tests/smoke_action_loop.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import crew as crew_mod  # noqa: E402
from ctfy_agent.cli import open_store  # noqa: E402
from ctfy_agent.config import load as load_config  # noqa: E402
from ctfy_agent.crew import Crew  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402

FAILS: list[str] = []
CID = "c-loop-1"


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ---------------------------------------------------------------- 假平台
class StubPlatform:
    """只实现 crew.solve 协调者循环走到的那几个接口。"""

    def __init__(self) -> None:
        self.submitted: list[dict] = []
        self.tools_seen: list[set[str]] = []

    # --- 工具面 ---
    def tool_names(self) -> list[str]:
        return ["submit_flag", "get_challenge"]

    def openai_tools_for(self, names) -> list[dict]:
        return []

    def try_resolve(self, kind: str) -> str:
        return {"submit_flag": "submit_flag", "get_challenge": ""}.get(kind, "")

    def resolve(self, kind: str) -> str:
        if kind == "submit_flag":
            return "submit_flag"
        raise KeyError(kind)

    def schema_of(self, name: str) -> dict:
        return {"additionalProperties": False,
                "properties": {"answer": {"type": "string"}, "challenge_id": {"type": "string"}},
                "required": ["answer", "challenge_id"]}

    def prop_names(self, name: str) -> set[str]:
        if name == "submit_flag":
            return {"answer", "challenge_id"}
        return set()

    def preferred_question_id(self, name: str) -> str:
        return ""

    def _as_json(self, payload):
        if isinstance(payload, dict):
            return payload
        text = str(payload or "").strip()
        if text.startswith("-----"):
            text = text.split("\n", 1)[1] if "\n" in text else ""
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    # --- 业务 ---
    def call_raw(self, name: str, arguments=None, *, retries: int = 0):
        if name != "submit_flag":
            raise AssertionError(f"unexpected call {name}")
        self.submitted.append(dict(arguments or {}))
        return {"content": [{"type": "text",
                             "text": json.dumps({"accepted": False, "is_solved": False})}],
                "isError": False}

    def my_solved_ids(self) -> set[str]:
        return set()

    def stop_instance(self, instance_id: str) -> None:  # pragma: no cover - 无实例场景不会调
        return None

    def list_instances(self, *a, **kw):
        return []


# ---------------------------------------------------------------- 脚本化假 LLM
class ScriptedLLM:
    """按脚本逐轮返回 tool_calls；脚本用完后一律返回 done。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.calls = 0
        self.max_tokens = 4096
        self.temperature = 0.2
        self.budget = None
        self.tools_history: list[list[str]] = []

    def chat(self, messages, tools=None, tool_choice="auto"):
        self.calls += 1
        self.tools_history.append([(t.get("function") or {}).get("name") for t in (tools or [])])
        if self.script:
            call = self.script.pop(0)
        else:
            call = {"name": "done", "args": {}}
        tool_calls = [{
            "id": f"call_{self.calls}",
            "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call.get("args") or {})},
        }]
        return {
            "content": "PLAN: 测试脚本推进。\nREFLECTION: 断言用。",
            "tool_calls": tool_calls,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "finish_reason": "tool_calls",
        }


def _fresh(cfg, plat, llm, run_id: str, events: list | None = None) -> tuple[Store, Crew]:
    store = open_store(cfg, run_id)
    store.upsert({"id": CID, "name": "loop-demo", "category": "MISC",
                  "description": "断路器回归", "points": 1})
    store.save()
    sink = events if events is not None else []
    crew = Crew(cfg, plat, store, lambda model=None: llm,
                on_event=lambda kind, payload=None: sink.append((kind, payload or {})))
    return store, crew


def _transcript_notes(store: Store) -> str:
    path = store.transcript_path(CID)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def main() -> int:
    # 注意：临时根放工作区（受限环境下 %TEMP% 不可写），且用普通 mkdir 建。
    tmp = ROOT / "runs" / f"_t_action_loop_{__import__('os').getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    cfg.runs_dir = str(tmp)

    # ---------- 1) 纯逻辑：指纹与拦截识别 ----------
    check("同样参数同指纹",
          crew_mod._action_signature("submit", {"answer": "A"}) ==
          crew_mod._action_signature("submit", {"answer": "A"}))
    check("参数顺序不影响指纹",
          crew_mod._action_signature("submit", {"a": 1, "b": 2}) ==
          crew_mod._action_signature("submit", {"b": 2, "a": 1}))
    check("不同值不同指纹",
          crew_mod._action_signature("submit", {"answer": "A"}) !=
          crew_mod._action_signature("submit", {"answer": "B"}))
    check("提交拦截被识别", crew_mod._looks_blocked("[提交被拦截] 这个答案之前已经提交过…"))
    check("派活拒绝被识别", crew_mod._looks_blocked("[拒绝] delegate 缺少 objective"))
    check("正常结果不算拦截", not crew_mod._looks_blocked('{"accepted": true}'))
    check("平台判错不算拦截", not crew_mod._looks_blocked("----- 工具返回 -----\n{\"accepted\":false}"))

    # ---------- 2) 端到端：同一答案连交 → 断路器收口 ----------
    plat = StubPlatform()
    llm = ScriptedLLM([{"name": "submit", "args": {"answer": "L3AK{same_guess}"}}] * 20)
    events: list = []
    store, crew = _fresh(cfg, plat, llm, "loop1", events)
    summary = crew.solve(CID)

    check("收口原因是 action_loop", summary.get("reason") == "action_loop", str(summary.get("reason")))
    check("未误报解出", summary.get("submitted") is False)
    check("只真提交了 1 次（其余被闸门拦下）", len(plat.submitted) == 1, f"实际 {len(plat.submitted)} 次")
    check("LLM 调用远少于 40 轮上限", llm.calls <= crew_mod.REPEAT_BLOCK_LIMIT + 1, f"实际 {llm.calls} 次")
    check("submitted 参数正确", plat.submitted and plat.submitted[0].get("answer") == "L3AK{same_guess}",
          str(plat.submitted))
    tr = _transcript_notes(store)
    check("转录记了移出工具面", "已移出工具面" in tr)
    check("转录记了 action_loop 收口", "action_loop" in tr)
    check("状态记为 stalled", store.require(CID).status == "stalled", store.require(CID).status)
    # [接手 2026-11-11] 生效预算必须落成确定性一行（来源可辨，避免"改了配置没用"的误判）
    check("转录记了生效预算与来源", "生效预算：调用上限" in tr and "来源" in tr, tr[-300:])
    check("预算事件已发出",
          any(k == "budget_plan" for k, _ in events), str([k for k, _ in events])[:200])

    # ---------- 3) 工具面真的少了 submit ----------
    names_after = crew._coordinator_tools({"submit"})
    check("被摘工具不在工具面", all((t.get("function") or {}).get("name") != "submit" for t in names_after))
    names_all = crew._coordinator_tools()
    check("不传 suppressed 时工具面不变",
          any((t.get("function") or {}).get("name") == "submit" for t in names_all))
    # 断言第 2 轮起模型确实看不到 submit（tools_history 由假 LLM 记录）
    # 时序：第 1 轮真提交→平台判错（不算拦截）；第 2 次同值→闸门拦（streak=1）；
    # 第 3 次同值→streak=2 触发摘除（该轮工具面已发出）；第 4 轮起工具面才真的没有 submit。
    seen = [names for names in llm.tools_history]
    check("首轮还能看见 submit", any("submit" in n for n in seen[:1]))
    check("触发摘除后轮次看不到 submit", all("submit" not in n for n in seen[3:]),
          str(seen))

    # ---------- 4) 透明性：动作不重复时不介入 ----------
    # 每次都是不同答案：即使平台都判错，也不该被当成「重复动作」拦（判错 ≠ 被守卫拦下）。
    plat2 = StubPlatform()
    llm2 = ScriptedLLM([
        {"name": "submit", "args": {"answer": "guess_one"}},
        {"name": "submit", "args": {"answer": "guess_two"}},
        {"name": "submit", "args": {"answer": "guess_three"}},
    ])
    store2, crew2 = _fresh(cfg, plat2, llm2, "loop2")
    summary2 = crew2.solve(CID)
    check("不重复时不触发 action_loop", summary2.get("reason") != "action_loop", str(summary2.get("reason")))
    check("不重复时每次都真提交", len(plat2.submitted) == 3, f"实际 {len(plat2.submitted)} 次")
    tr2 = _transcript_notes(store2)
    check("不重复时 submit 不被断路器摘除", "断路器：submit" not in tr2)

    # ---------- 5) 不同值被判错但不算「被拦」---------- 
    check("不同参数会重置连续计数",
          crew_mod._action_signature("submit", {"answer": "x"}) !=
          crew_mod._action_signature("submit", {"answer": "y"}))

    print()
    if FAILS:
        print(f"smoke_action_loop 失败 {len(FAILS)} 项：" + "、".join(FAILS))
        return 1
    print("smoke_action_loop 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())