#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CTFy 平台封装。

平台通过 MCP 暴露 ~97 个工具（实测 ctfy 3.4.6）。这里做三件事：

  1. **语义动作 → 真实工具名**：先查 DEFAULT_TOOLS（线上实测名），不存在再按词义猜，
     并允许 config/agent.json 的 tool_map 强制覆盖。这样线上和离线假服务都能跑。
  2. **只把「解题需要的工具」交给模型**：97 个工具里有覆盖报名档案、上传题目、
     改 AWD 网络等危险动作，一律不暴露（见 MODEL_TOOLS）。提示类工具
     （list_hints / view_hint）2026-11 起放行：正赛有 hints 的题不给模型就结构性拿不到；
     view_hint **扣费且按队永久计费不可回滚**，代码里绝不自动调用，只由模型显式发起。
  3. **按 inputSchema 自适应参数形态**：真实平台是「实例中心」的
     （submit_answer/verify_answer 要 instance_id + answer），
     假服务是「题目中心」的（submit_flag 要 challenge_id + flag）。不写死，读 schema。

关键实测结论（2026-10）：
  - POST https://ctfy.tsinghua.edu.cn/api/v1/mcp/   Streamable HTTP
  - start_instance(challenge_id[, competition_id]) -> 返回实例 id(UUID) + host/port/凭据
  - verify_answer(instance_id, answer[, question_id])  → 判题但**不写提交记录**（可白试）
  - submit_answer(instance_id, answer[, question_id])  → 真正提交（消耗次数）
  - stop_instance/renew_instance/reset_instance 都以 **instance_id** 为键
"""

from __future__ import annotations

import json
import re
import time
from json import JSONDecodeError

from .mcp_client import McpHttpClient, mcp_tool_to_openai, render_call_result, split_tool_name

# ---------- 运行时事实修正：模型最容易在「实例/问题 id」上编参数 ----------
# 实测 2026-10-29 SAMPLE-007：worker 已被打掉（POST /api/worker/stop -> {"worker_alive":false}），
# 但 verify_exploit 传了 instance_id="s2mzomar64iepo3qh"（主机前缀，不是实例 UUID）、
# question_id="deface"（幻觉；本题真实 question id 是 "outage"）→ instance_not_found，
# 平台始终没记解。下面按 get_challenge/start_instance/get_instance 拿到的真实事实做参数修正。
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# 这些工具以 instance_id 为键（平台文档：instance UUID returned by start_instance）
_INSTANCE_TOOLS = frozenset({
    "verify_exploit", "verify_answer", "submit_answer", "submit_qa_answer", "submit_flag",
    "get_instance", "get_instance_status", "get_instance_traffic",
    "stop_instance", "renew_instance", "reset_instance",
})
# 这些工具带 question_id（平台默认 "flag"，但 checker 题的真实 id 可能叫 outage/…）
_QUESTION_TOOLS = frozenset({
    "verify_exploit", "verify_answer", "submit_answer", "submit_qa_answer", "submit_flag",
})

# ---------- 语义动作 → 线上真实工具名 ----------

DEFAULT_TOOLS = {
    "whoami": "get_me",
    "list_competitions": "list_competitions",
    "get_competition": "get_competition",
    "list_challenges": "list_competition_challenges",
    "get_challenge": "get_challenge",
    "start_instance": "start_instance",
    "get_instance": "get_instance",
    "stop_instance": "stop_instance",
    "renew_instance": "renew_instance",
    "verify_answer": "verify_answer",
    "submit_flag": "submit_answer",
    "submit_qa": "submit_qa_answer",
    "leaderboard": "get_scoreboard",
    "list_instances": "list_instances",
    "my_solves": "get_my_solves",
    "my_progress": "get_my_milestone_progress",
    "list_submissions": "list_submissions",
}

# 允许暴露给模型的工具白名单（其余 97 个里的一律不给模型）
MODEL_TOOLS = frozenset({
    # 只读：侦察
    "get_me",
    "list_competitions",
    "get_competition",
    "list_competition_challenges",
    "get_challenge",
    "list_challenge_attachments",
    "download_challenge_attachment",
    "list_instances",
    "get_instance",
    "get_instance_status",
    "get_instance_traffic",
    "list_instance_attachments",
    "download_instance_attachment",
    "get_my_solves",
    "get_my_milestone_progress",
    "list_submissions",
    "get_scoreboard",
    "search_challenges",
    # 只读：提示（A0 附带项）。list_hints 免费列层级，view_hint 扣费且不可回滚
    "list_hints",
    "view_hint",
    # 写：由 solver 的幂等/频控/上限约束
    "start_instance",
    "stop_instance",
    "renew_instance",
    "verify_answer",
    "submit_answer",
    "submit_qa_answer",
})

# 自动归类用的词表（DEFAULT_TOOLS 里没有该工具时的回退）
KIND_WORDS = {
    "list_challenges": {"list", "challenges", "challenge", "problems", "browse", "catalog"},
    "get_challenge": {"get", "detail", "details", "info", "view", "challenge"},
    "start_instance": {"start", "launch", "create", "instance", "env", "environment", "up", "deploy"},
    "stop_instance": {"stop", "destroy", "kill", "delete", "instance", "env", "down"},
    "submit_flag": {"submit", "flag", "answer", "check", "verify"},
    "leaderboard": {"leaderboard", "rank", "ranking", "scoreboard", "board", "scores"},
}

KIND_EXPECT = {
    "list_challenges": "read",
    "get_challenge": "read",
    "leaderboard": "read",
    "start_instance": "write",
    "stop_instance": "write",
    "submit_flag": "write",
}


class PlatformError(RuntimeError):
    pass


class CtfyPlatform:
    def __init__(self, mcp_url: str, token: str, *, timeout: float = 30.0, tool_map: dict | None = None,
                 verbose: bool = False):
        self.client = McpHttpClient(mcp_url, token, timeout=timeout, verbose=verbose)
        self.tool_map = dict(tool_map or {})
        self.tools: list[dict] = []
        self._by_name: dict[str, dict] = {}
        self._resolved: dict[str, str] = {}
        # 本次运行的真实事实（供参数修正）：当前实例 UUID、当前题 id、题目 questions 元信息
        self._active_instance_id: str = ""
        self._active_challenge_id: str = ""
        self._question_meta: dict[str, dict[str, dict]] = {}
        # 最近一次 verify_* 判过 true 的 question_id；多问题题提交时优先用它当默认题号
        self._verified_qid: str = ""

    # ---------- 连接 ----------

    def connect(self) -> dict:
        result = self.client.initialize()
        self.tools = self.client.list_tools()
        self._by_name = {tool.get("name") or "": tool for tool in self.tools}
        self._resolved.clear()
        return result or {}

    @property
    def server_info(self) -> dict:
        return self.client.server_info or {}

    def tool_names(self) -> list[str]:
        return [tool.get("name") or "?" for tool in self.tools]

    def schema_of(self, name: str) -> dict:
        tool = self._by_name.get(name) or {}
        return tool.get("inputSchema") or {}

    def prop_names(self, name: str) -> set[str]:
        return set((self.schema_of(name).get("properties") or {}).keys())

    # ---------- 暴露给模型的工具（白名单） ----------

    def _allowlist_active(self) -> bool:
        """只有确认是真实平台时才收紧工具面（假服务/自测全放开）。"""
        names = set(self._by_name)
        return "get_me" in names or "submit_answer" in names

    def model_tools(self) -> list[dict]:
        """只返回白名单内的工具；非真实平台（如离线假服务）退回全部。"""
        if not self._allowlist_active():
            return list(self.tools)
        allowed = [tool for tool in self.tools if (tool.get("name") or "") in MODEL_TOOLS]
        return allowed or list(self.tools)

    def openai_tools(self) -> list[dict]:
        return [mcp_tool_to_openai(tool) for tool in self.model_tools()]

    def openai_tools_for(self, names: set[str]) -> list[dict]:
        """只把 names 指定的平台工具转成 OpenAI 规格（多智能体按角色配工具面）。"""
        return [mcp_tool_to_openai(tool) for tool in self.tools if (tool.get("name") or "") in names]

    def is_model_tool(self, name: str) -> bool:
        names = {tool.get("name") or "" for tool in self.model_tools()}
        return name in names

    # ---------- 语义动作解析 ----------

    def resolve(self, kind: str) -> str:
        """config.tool_map > 线上实测名 > 自动归类。只返回平台上真实存在的工具名。"""
        if kind in self._resolved:
            return self._resolved[kind]

        candidate = self.tool_map.get(kind) or DEFAULT_TOOLS.get(kind)
        if candidate and candidate in self._by_name:
            self._resolved[kind] = candidate
            return candidate
        if self.tool_map.get(kind):
            # 显式指定了但平台上没有：直接报错，别悄悄换成别的工具
            raise PlatformError(
                f"tool_map 把「{kind}」指定为 {self.tool_map[kind]}，但平台没有这个工具。\n"
                f"现有工具：{', '.join(sorted(self._by_name))}"
            )

        words = KIND_WORDS.get(kind)
        if not words:
            raise PlatformError(f"未知语义动作 {kind}，也没有对应默认工具")
        expect = KIND_EXPECT.get(kind)
        best_name, best_score = None, 0
        for name in self._by_name:
            tokens = set(split_tool_name(name))
            score = len(tokens & words)
            if score == 0:
                continue
            if kind == "submit_flag":
                # 关键：verify_* 是不记账的探针，绝不能当提交用
                if name.startswith("verify"):
                    continue
                if not (tokens & {"submit", "answer", "flag"}):
                    continue
            if kind == "start_instance" and (tokens & {"stop", "down", "delete", "destroy"}):
                continue
            if kind == "stop_instance" and not (tokens & {"stop", "down", "delete", "destroy", "kill"}):
                continue
            if expect and _classify(name) == expect:
                score += 0.5
            if score > best_score:
                best_name, best_score = name, score

        if not best_name:
            raise PlatformError(
                f"没能在平台工具里找到「{kind}」对应的工具。\n"
                f"现有工具：{', '.join(sorted(self._by_name)) or '(空)'}\n"
                f'可在 config/agent.json 的 tool_map 里手动指定，例如 {{"tool_map": {{"{kind}": "真实工具名"}}}}'
            )
        self._resolved[kind] = best_name
        return best_name

    def try_resolve(self, kind: str) -> str | None:
        try:
            return self.resolve(kind)
        except PlatformError:
            return None

    def resolution_report(self) -> dict:
        report = {}
        for kind in set(KIND_WORDS) | set(DEFAULT_TOOLS):
            report[kind] = self.try_resolve(kind)
        return report

    # ---------- 参数校验 ----------

    def _clean_args(self, name: str, arguments: dict | None) -> dict:
        """轻量校验：required 缺失报错，integer 自动纠正类型。"""
        cleaned = dict(arguments or {})
        schema = self.schema_of(name)
        if not schema:
            return cleaned
        properties = schema.get("properties") or {}
        for field in schema.get("required") or []:
            if cleaned.get(field) in (None, ""):
                raise PlatformError(f"{name}: 缺少必填参数 {field}")
        for field, value in list(cleaned.items()):
            prop = properties.get(field) or {}
            if prop.get("type") == "integer" and not isinstance(value, int):
                try:
                    cleaned[field] = int(value)
                except (TypeError, ValueError):
                    raise PlatformError(f"{name}: 参数 {field} 应为整数，收到 {value!r}")
        return cleaned

    # ---------- 运行时事实修正（防模型编造 instance_id / question_id） ----------

    def _remember_questions(self, challenge_id, data) -> None:
        """从 get_challenge / start_instance / get_instance 的返回里记下题目 questions。"""
        if not isinstance(challenge_id, str) or not challenge_id or not isinstance(data, dict):
            return
        questions = data.get("questions") or []
        if not isinstance(questions, list) or not questions:
            return
        meta: dict[str, dict] = {}
        for q in questions:
            if not isinstance(q, dict):
                continue
            qid = q.get("id") or q.get("question_id")
            if not qid:
                continue
            meta[str(qid)] = {
                "mode": str(q.get("mode") or ""),
                "prompt": str(q.get("prompt") or "")[:160],
            }
        if meta:
            self._question_meta[challenge_id] = meta

    def _learn_from_result(self, name: str, arguments: dict, result) -> None:
        """把工具返回里的事实沉淀下来；只认 dict 返回值，解析失败静默跳过。"""
        if name not in ("start_instance", "get_instance", "get_instance_status", "get_challenge",
                        "verify_answer", "verify_exploit"):
            return
        try:
            data = self._as_json(render_call_result(result))
        except Exception:  # noqa: BLE001 - 事实学习失败不能影响主流程
            return
        # verify_* 返回 true/{"result": true}：记住这个题号，后续 submit 默认打向它。
        # 否则平台会按默认 "flag" 提交，而多问题题（user/admin/techniques）根本没有这个题号，
        # 会以 422 unknown_question_id 失败——正确答案就此被丢掉（实测 CTFARENA-048 踩到）。
        if name in ("verify_answer", "verify_exploit"):
            verdict = data.get("result") if isinstance(data, dict) else data
            if verdict is True or str(verdict).strip().lower() == "true":
                qid = arguments.get("question_id")
                if isinstance(qid, str) and qid:
                    self._verified_qid = qid
            return
        if not isinstance(data, dict):
            return
        cid = arguments.get("challenge_id") or self._active_challenge_id
        if isinstance(cid, str) and cid:
            self._active_challenge_id = cid
        iid = data.get("id") or data.get("instance_id")
        if (name in ("start_instance", "get_instance", "get_instance_status")
                and isinstance(iid, str) and iid):
            self._active_instance_id = iid
        self._remember_questions(cid, data)

    def _pick_question_id(self, tool_name: str) -> str:
        """在本题 questions 里挑最可能的目标：verify_exploit 优先唯一 checker 题，否则唯一题。"""
        known = self._question_meta.get(self._active_challenge_id) or {}
        if not known:
            return ""
        # 已用 verify_* 判过 true 的题号最可信（多问题题里这是唯一确定「答案对得上」的那个）
        if self._verified_qid and self._verified_qid in known:
            return self._verified_qid
        if tool_name == "verify_exploit":
            checkers = [qid for qid, m in known.items() if (m.get("mode") or "") == "checker"]
            if len(checkers) == 1:
                return checkers[0]
        if len(known) == 1:
            return next(iter(known))
        return ""

    def preferred_question_id(self, tool_name: str = "submit_answer") -> str:
        """供调用方（solver/crew）决定默认题号的公开入口；拿不到就返回空串。"""
        return self._pick_question_id(tool_name)

    def _repair_runtime_args(self, name: str, arguments: dict | None) -> dict:
        """按运行时事实修正参数；只改 schema 里真实存在的字段，绝不新增字段。"""
        args = dict(arguments or {})
        props = self.prop_names(name)
        if not props:  # 假服务/无 schema：不猜，原样放行
            return args
        if "instance_id" in props and name in _INSTANCE_TOOLS:
            iid = args.get("instance_id")
            if (self._active_instance_id and isinstance(iid, str)
                    and not _UUID_RE.match(iid.strip())):
                args["instance_id"] = self._active_instance_id
        if "question_id" in props and name in _QUESTION_TOOLS:
            qid = args.get("question_id")
            current = self._question_meta.get(self._active_challenge_id) or {}
            if not qid and current:
                # 没给题号时才自动补：别让平台落到默认 "flag"（本题真实题号可能是 user/outage/…）。
                better = self._pick_question_id(name)
                if better:
                    args["question_id"] = better
            # [v6.4] 显式给的题号一律信任，不再改写成「已知唯一题号」：门控题（SAMPLE-001）的 flag 题
            # 不在 get_challenge 的 questions 里，旧逻辑会把 question_id="flag" 改写成 "slug"，
            # 导致正确答案被当成 slug 判错、flag 题从未真正提交（001 实测 wrong_attempts=0）。
            # 真写错题号时平台会回明确的 422 unknown_question_id，模型自己会纠，比静默错投更安全。
        return args

    # ---------- 调用 ----------

    def call(self, name: str, arguments: dict | None = None, *, retries: int = 0):
        arguments = self._clean_args(name, arguments)
        arguments = self._repair_runtime_args(name, arguments)
        result = self.client.call_tool(name, arguments, retries=retries)
        # 兜底：参数已修正仍报 instance_not_found（例如缓存的实例已被平台回收）→ 用当前实例重试一次
        if isinstance(result, dict) and result.get("isError") and self._active_instance_id:
            try:
                err_text = render_call_result(result)
            except Exception:  # noqa: BLE001
                err_text = ""
            if "instance_not_found" in err_text and arguments.get("instance_id") != self._active_instance_id:
                arguments["instance_id"] = self._active_instance_id
                result = self.client.call_tool(name, arguments, retries=retries)
        self._learn_from_result(name, arguments, result)
        return result

    def call_raw(self, name: str, arguments: dict | None = None, *, retries: int = 0):
        """返回原始 result dict（含 isError），供提交判定精确读取。"""
        return self.call(name, arguments, retries=retries)

    def call_text(self, name: str, arguments: dict | None = None, *, retries: int = 0) -> str:
        result = self.call(name, arguments, retries=retries)
        text = render_call_result(result)
        if isinstance(result, dict) and result.get("isError"):
            text = f"[工具报错] {text}"
        return text

    @staticmethod
    def _as_json(text: str):
        """解析工具返回里的第一个 JSON 值。

        平台返回常是「content 文本 + structuredContent 美化 JSON」两段拼接，
        所以用 raw_decode 只取开头那个 JSON，忽略后面的重复美化段。
        """
        raw = text or ""
        if raw.startswith("-----"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.removeprefix("[工具报错] ").lstrip()
        if not raw:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(raw)
            return value
        except JSONDecodeError:
            return None

    # ---------- 便捷方法 ----------

    def my_solved_ids(self) -> set:
        """平台侧「我已解出」的题 id 集合（确定性事实源，供 proof-of-exploitation 判定回传）。

        `get_my_solves` 返回 [{"challenge_id": "...", "solved_at": ...}]；失败时返回空集合，
        绝不让判定回传把主流程拖垮。带 10 s 缓存：同一轮里 solver 与 crew 都会问。
        """
        now = time.time()
        cached = getattr(self, "_solved_cache", None)
        if cached and now - cached[0] < 10.0:
            return cached[1]
        solved: set = set()
        # get_my_solves 不是语义动作（不在 DEFAULT_TOOLS/KIND_WORDS 里），按真实工具名直接找
        name = self.tool_map.get("my_solves") or "get_my_solves"
        if name not in self._by_name:
            name = ""
        if name:
            try:
                text = self.call_text(name, {})
                data = self._as_json(text)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            cid = item.get("challenge_id") or item.get("id")
                            if cid:
                                solved.add(str(cid))
                elif isinstance(data, dict):
                    for item in data.get("solves") or data.get("items") or data.get("result") or []:
                        if isinstance(item, dict):
                            cid = item.get("challenge_id") or item.get("id")
                            if cid:
                                solved.add(str(cid))
            except Exception:  # noqa: BLE001 - 判定增强项，失败就当没解出
                solved = set()
        self._solved_cache = (now, solved)
        return solved

    def whoami(self):
        return self._json_or_text("whoami", {})

    def list_competitions(self, **filters):
        return self._json_or_text("list_competitions", {k: v for k, v in filters.items() if v is not None})

    def get_competition(self, competition_id: str):
        return self._json_or_text("get_competition", {"competition_id": competition_id})

    def list_challenges(self, competition_id: str | None = None, **filters):
        payload = {k: v for k, v in filters.items() if v is not None}
        if competition_id:
            payload["competition_id"] = competition_id
        return self._json_or_text("list_challenges", payload)

    def get_challenge(self, challenge_id: str, competition_id: str | None = None):
        payload = {"challenge_id": challenge_id}
        if competition_id:
            payload["competition_id"] = competition_id
        return self._json_or_text("get_challenge", payload)

    def start_instance(self, challenge_id: str, competition_id: str | None = None):
        payload = {"challenge_id": challenge_id}
        if competition_id:
            payload["competition_id"] = competition_id
        return self._json_or_text("start_instance", payload)

    def get_instance(self, instance_id: str):
        return self._json_or_text("get_instance", {"instance_id": instance_id})

    def stop_instance(self, instance_id: str):
        return self._json_or_text("stop_instance", {"instance_id": instance_id})

    def list_instances(self):
        return self._json_or_text("list_instances", {})

    def my_solves(self):
        return self._json_or_text("my_solves", {})

    def leaderboard(self, competition_id: str | None = None, **filters):
        payload = {k: v for k, v in filters.items() if v is not None}
        if competition_id:
            payload["competition_id"] = competition_id
        return self._json_or_text("leaderboard", payload)

    def _json_or_text(self, kind: str, payload: dict):
        name = self.resolve(kind)
        text = self.call_text(name, payload, retries=2 if _classify(name) == "read" else 0)
        parsed = self._as_json(text)
        return parsed if parsed is not None else text


def _classify(name: str) -> str:
    return "read" if (set(split_tool_name(name)) & {
        "list", "get", "search", "read", "view", "info", "status", "query", "fetch",
        "my", "show", "describe", "board", "detail", "details", "download", "summary", "traffic",
    }) and not (set(split_tool_name(name)) & {
        "submit", "answer", "start", "stop", "launch", "create", "delete", "remove",
        "run", "exec", "reset", "restart", "kill", "update", "write", "set", "put", "post",
        "upload", "deploy", "destroy", "renew", "verify", "join", "leave", "save", "reply",
    }) else "write"
