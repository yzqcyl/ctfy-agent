# -*- coding: utf-8 -*-
"""离线模式后端：不接平台接口，只「本地解题 + 本地记录候选 flag」。

OfflinePlatform 是 CtfyPlatform 的鸭子类型替身，只覆盖 Solver/Crew 实际用到的
十几个方法（逐条核验过的接口面）。语义：

  - 平台工具面为空 → 模型只剩本地攻击工具（http_request / shell / sqli / fuzz / …），
    本地工具由 crew._role_specs 独立追加，与平台完全解耦（方案核验 #1）。
  - submit_answer / submit_qa_answer 本地化：记录候选值并返回 accepted 回执，
    让协调者走正常的 `flag_accepted` 收口；不联网、不提交任何平台（方案核验 #3）。
  - 其余平台动作（get_challenge / list_instances / start_instance / …）返回空或视为不可用，
    绝不向 ctfy 平台发任何请求；起靶场与提交 flag 均由人工完成。

本文件是纯增量：不 import 时不会产生任何副作用，也不被 Solver/Crew 反向依赖。
"""
from __future__ import annotations

import json
from json import JSONDecodeError

from .platform import PlatformError

# 离线 submit 回执：`content` 必须为空、只放 `structuredContent` 纯 JSON，
# 这样 render_call_result → looks_accepted 才能 raw_decode 出 accepted=true（方案核验 #3/#4）。
# challenge_fully_solved 置 True（单 flag 题默认全解），让 mark_submitted 置 status=submitted。
def _submit_ok(answer: str) -> dict:
    return {
        "content": [],
        "structuredContent": {
            "accepted": True,
            "challenge_fully_solved": True,
            "flag": answer,
            "note": "离线模式：仅本地记录候选，未提交任何平台（人工提交）。",
        },
    }


class OfflinePlatform:
    """不接平台的鸭子类型替身。record_answer 回调用于把候选实时推给 GUI。"""

    def __init__(self, record_answer=None):
        self._record_answer = record_answer
        self.tool_map: dict = {}
        self.tools: list = []
        self._by_name: dict = {}
        self._resolved: dict = {}
        # 与真实 CtfyPlatform 同名的运行态属性，某些路径（提交参数修正）会读
        self._active_instance_id = ""
        self._active_challenge_id = ""
        self._question_meta: dict = {}
        self._question_ids: dict = {}
        self._SUBMIT = {"submit_answer", "submit_qa_answer"}

    # ---------- 工具面（空） ----------

    def tool_names(self) -> list:
        return []

    def schema_of(self, name: str) -> dict:
        return {}

    def prop_names(self, name: str) -> set:
        # submit 只需 answer；不给 instance_id / competition_id / question_id，
        # 让 solver._submit 走「离线：challenge_id 定位、answer 直传」分支。
        return {"answer"} if name in self._SUBMIT else set()

    def model_tools(self) -> list:
        return []

    def openai_tools(self) -> list:
        return []

    def openai_tools_for(self, names) -> list:
        return []

    def is_model_tool(self, name: str) -> bool:
        return False

    # ---------- 语义解析 ----------

    def resolve(self, kind: str) -> str:
        if kind == "submit_flag":
            return "submit_answer"
        if kind == "submit_qa":
            return "submit_qa_answer"
        raise PlatformError(f"离线模式不支持平台动作 {kind!r}")

    def try_resolve(self, kind: str):
        try:
            return self.resolve(kind)
        except PlatformError:
            return None

    # ---------- 调用 ----------

    def call(self, name, arguments=None, *, retries: int = 0):
        return self.call_raw(name, arguments, retries=retries)

    def call_raw(self, name, arguments=None, *, retries: int = 0):
        if name in self._SUBMIT:
            args = dict(arguments or {})
            answer = str(args.get("answer") or args.get("flag") or args.get("value") or "").strip()
            cid = str(args.get("challenge_id") or args.get("id") or "").strip()
            if self._record_answer:
                try:
                    self._record_answer(cid, answer)
                except Exception:  # noqa: BLE001 - 回调解耦，失败不影响主流程
                    pass
            return _submit_ok(answer)
        raise PlatformError(f"离线模式不支持平台工具 {name!r}（只接本地工具与 submit）")

    def call_text(self, name, arguments=None, *, retries: int = 0) -> str:
        if name in self._SUBMIT:
            from .mcp_client import render_call_result  # noqa: PLC0415

            return render_call_result(self.call_raw(name, arguments, retries=retries))
        raise PlatformError(f"离线模式不支持平台工具 {name!r}")

    # ---------- 便捷 / 只读 ----------

    @staticmethod
    def _as_json(text):
        raw = (text or "").strip()
        if raw.startswith("-----"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.removeprefix("[工具报错] ").removeprefix("[工具报错]").lstrip()
        if not raw:
            return None
        try:
            value, _ = json.JSONDecoder().raw_decode(raw)
            return value
        except JSONDecodeError:
            return None

    def my_solved_ids(self) -> set:
        return set()

    def preferred_question_id(self, tool_name: str = "submit_answer") -> str:
        return ""

    def list_competitions(self, **filters):
        return []

    def list_challenges(self, competition_id: str | None = None, **filters):
        return []

    def list_instances(self, **filters):
        return []

    def start_instance(self, challenge_id: str, competition_id: str | None = None):
        raise PlatformError("离线模式不接平台：请人工启动靶场，并用 --url 指定可达地址")

    def stop_instance(self, instance_id: str):
        return {"ok": True, "stopped": 0}