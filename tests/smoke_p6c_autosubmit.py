#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P6c 回归：verify 通过后的自动补交必须在实例释放前发生。

背景（CTFARENA-084~090 实证）：flag 按实例动态生成，实例一停旧答案即失效；原 P6 把自动补交
放在 finally（stop_instance）之后，补交必然无效。P6c 把补交移进 finally 开头（实例仍存活时）。

本测试不需要真实 LLM/平台：
  1) `Crew._auto_submit_verified` 单元行为：verified_qids -> submit -> submitted_ok=True；
  2) 源码顺序静态断言：补交调用出现在 stop_instance 之前。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(TESTS))

from ctfy_agent.config import Config  # noqa: E402
from ctfy_agent.crew import Crew  # noqa: E402
from ctfy_agent.state import Store  # noqa: E402


class _PlatformStub:
    """Crew 构造只用到 platform 的属性访问；补交路径不需要真实平台。"""

    def tool_names(self):
        return {"submit_answer", "verify_answer", "start_instance", "stop_instance"}

    def try_resolve(self, name):
        return name

    def resolve(self, name):
        return name

    def call_text(self, name, args=None):
        return "{}"


class _SolverStub:
    def __init__(self, ok: bool):
        self.ok = ok
        self.calls: list[dict] = []

    def _submit(self, args, challenge_id):
        self.calls.append({"args": dict(args), "cid": challenge_id})
        return ("ok", True) if self.ok else ("[提交失败] mock", False)


def _seed(store: Store, *, instance_id: str = "inst-1"):
    store.upsert({
        "id": "web-01",
        "name": "P6c fixture",
        "category": "web",
        "description": "fixture",
        "tags": ["web"],
    })
    item = store.require("web-01")
    item.instance_id = instance_id
    item.status = "instance_up"
    store.save()
    return item


def case_p6c_autosubmit(tmp: Path) -> None:
    run_dir = tmp / "p6c"
    store = Store(run_dir).load()
    _seed(store)
    item = store.require("web-01")
    item.verified_qids = {"flag": "FLAG{p6c}"}
    store.save()

    crew = Crew(Config(), _PlatformStub(), store, lambda model=None: None)
    solver = _SolverStub(ok=True)
    submitted, reason = crew._auto_submit_verified("web-01", solver, False, "coordinator_done")
    assert submitted is True, (submitted, reason)
    assert reason == "auto_submit_after_verify", reason
    assert solver.calls and solver.calls[0]["args"]["answer"] == "FLAG{p6c}", solver.calls
    assert solver.calls[0]["args"]["question_id"] == "flag", solver.calls
    item = store.require("web-01")
    assert item.submitted_ok is True and item.status == "submitted", (item.status, item.submitted_ok)

    # 已提交时不重复补交
    solver2 = _SolverStub(ok=True)
    submitted2, reason2 = crew._auto_submit_verified("web-01", solver2, True, "flag_accepted")
    assert submitted2 is True and solver2.calls == [], (submitted2, reason2, solver2.calls)

    # submit 失败时不误标成功
    store2 = Store(tmp / "p6c-fail").load()
    _seed(store2)
    i2 = store2.require("web-01")
    i2.verified_qids = {"flag": "FLAG{bad}"}
    store2.save()
    crew2 = Crew(Config(), _PlatformStub(), store2, lambda model=None: None)
    solver3 = _SolverStub(ok=False)
    submitted3, _ = crew2._auto_submit_verified("web-01", solver3, False, "coordinator_done")
    assert submitted3 is False, submitted3
    assert store2.require("web-01").submitted_ok is False

    # 源码顺序：补交必须发生在 stop_instance 之前（finally 内）
    src = inspect.getsource(Crew.solve)
    idx_call = src.find("self._auto_submit_verified(challenge_id, solver, submitted, reason)")
    idx_stop = src.find("self.platform.stop_instance(challenge.instance_id)")
    assert idx_call != -1, "solve() 中缺少 _auto_submit_verified 调用"
    assert idx_stop != -1, "solve() 中缺少 stop_instance"
    assert idx_call < idx_stop, f"补交位置错误：call@{idx_call} 必须在 stop_instance@{idx_stop} 之前"


def main() -> int:
    import shutil

    td = ROOT / "runs" / "_tmp_p6c"
    shutil.rmtree(td, ignore_errors=True)
    td.mkdir(parents=True, exist_ok=True)
    try:
        case_p6c_autosubmit(td)
    finally:
        shutil.rmtree(td, ignore_errors=True)
    print("PASS p6c_autosubmit: 补交在实例释放前 / 幂等 / 失败不误标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
