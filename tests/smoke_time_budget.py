# -*- coding: utf-8 -*-
"""按难度动态分配时间预算的守卫测试（只测 GUI 批量链路的新增逻辑，不碰 Crew/并行会话文件）。

覆盖 _budget_minutes 的难度映射/开关/覆盖，以及 _arm_budget 的软停止与全局取消传播。
"""
from __future__ import annotations

import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from ctfy_agent.gui import platform_worker as pw  # noqa: E402


class FakeConfig:
    def __init__(self, extra=None):
        self.extra = dict(extra or {})


def checks() -> int:
    ok = named = 0
    def check(name, cond):
        nonlocal ok, named
        named += 1
        assert cond, name
        ok += 1
        print(f"  PASS {name}")

    # ---- _budget_minutes 映射 ----
    cfg_default = FakeConfig()
    check("easy→10", pw._budget_minutes(cfg_default, "easy") == 10)
    check("medium→25", pw._budget_minutes(cfg_default, "medium") == 25)
    check("hard→40", pw._budget_minutes(cfg_default, "hard") == 40)
    check("大小写归一", pw._budget_minutes(cfg_default, "Hard") == 40)
    check("未知难度→兜底 20", pw._budget_minutes(cfg_default, "") == 20)

    # ---- 开关与覆盖 ----
    check("开关关闭→None", pw._budget_minutes(FakeConfig({"time_budget": False}), "hard") is None)
    check("兜底覆盖→30", pw._budget_minutes(FakeConfig({"time_budget_fallback": 30}), "") == 30)
    cfg_ov = FakeConfig({"time_budget_minutes": {"easy": 12, "hard": 99}})
    check("覆盖 easy=12", pw._budget_minutes(cfg_ov, "easy") == 12)
    check("覆盖 hard=99", pw._budget_minutes(cfg_ov, "hard") == 99)
    check("未覆盖 medium 仍 25", pw._budget_minutes(cfg_ov, "medium") == 25)

    # ---- _arm_budget 语义 ----
    ev, engaged = pw._arm_budget(threading.Event(), None)
    check("无预算→不装计时器", engaged is False and ev.is_set() is False)
    _, engaged0 = pw._arm_budget(threading.Event(), 0)
    check("0 分钟→不装计时器", engaged0 is False)

    g = threading.Event()
    ev2, engaged2 = pw._arm_budget(g, 999)
    check("有预算→装了计时器且初始未置位", engaged2 is True and ev2.is_set() is False)

    # 全局取消会传播到该题的软停止信号（监视线程 ≤1s 轮询）
    g.set()
    deadline = time.monotonic() + 3.0
    while not ev2.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    check("全局取消传播到软停止信号", ev2.is_set() is True)

    print(f"\n{ok}/{named} passed")
    return 0 if ok == named else 1


if __name__ == "__main__":
    try:
        sys.exit(checks())
    except AssertionError as exc:
        print(f"FAIL {exc}")
        sys.exit(1)