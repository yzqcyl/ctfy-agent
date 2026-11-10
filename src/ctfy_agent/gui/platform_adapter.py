# -*- coding: utf-8 -*-
"""GUI 用的「异构平台参数桥接」：只翻译参数，不改 platform.py 一行。

为什么需要
----------
平台工具名对上之后，**参数名也常常不一样**。以 DASCTF 练习场实测为例：

  list_challenges 要 practice_ground_id，我们发的是 competition_id
  get_instance    根本没有 instance_id，要 practice_ground_id + challenge_id
  submit_flag     要 flag + confirmation(=true) + practice_ground_id + challenge_id
  且这些工具的 inputSchema 写了 additionalProperties=false —— 多传一个字段就报错

所以「名字映射」不够，还需要「参数映射」。

做法
----
按目标工具的 inputSchema 做四件事（全通用，不写死任何平台）：

  1) 别名改名：competition_id → practice_ground_id 之类
  2) 记住事实：见过的 id 值记下来，后续缺必填时自动补上
  3) 补 const 必填：required 且带 const 的字段自动填 const（如 confirmation=true）
  4) 丢多余字段：additionalProperties=false 时只保留 schema 认识的字段

安装方式：给**实例**打补丁 platform.call = wrapper。
因为 call_text / call_raw / 高层便捷方法 / 模型直接调工具 最终都会走 self.call，
所以一处挂钩即可全覆盖，且不触碰 platform.py 的类定义。
对 CTFY 这类 schema 本来就吻合的平台，这层是纯 no-op。
"""
from __future__ import annotations

import time

# 容器类平台的实例启动是异步的：start 只返回 starting，要过几十秒才 access_ready。
# 不轮询的话，模型永远只看到 starting、拿不到 access_url，只能干等或乱翻本地文件。
_READY_BUDGET = 120.0    # 最长等多久（秒）
_READY_INTERVAL = 3.0    # 每次轮询间隔（秒）
_WAIT_STATUS = {"starting", "pending", "creating", "deploying", "provisioning", "queued"}
_DEAD_STATUS = {"failed", "error", "stopped", "deleted", "expired"}

# 我方通用参数名 → 平台可能用的专用名（仅当目标 schema 里有后者、没有前者时才换）
_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("competition_id", ("practice_ground_id", "ground_id", "contest_id")),
    ("challenge_id", ("problem_id", "task_id")),
    ("flag", ("answer",)),
    ("instance_id", ("environment_id", "env_id")),
)

# 出现即视为「可复用事实」的字段
_FACT_FIELDS = frozenset({
    "competition_id", "practice_ground_id", "ground_id", "stage_id",
    "challenge_id", "instance_id", "environment_id",
})


class ArgBridge:
    """把我们的通用参数翻译成目标工具真正接受的参数。"""

    def __init__(self, platform) -> None:
        self.platform = platform
        self.facts: dict[str, str] = {}
        self._instance_tools: dict[str, str] | None = None
        # 硬停止信号：置位后 wait_ready 立刻放弃轮询，别让「立即停止」卡在 sleep 里
        self.cancel_event = None

    def instance_tools(self) -> dict[str, str]:
        """解析 start_instance / get_instance 对应的真实工具名（带缓存）。"""
        if self._instance_tools is None:
            found: dict[str, str] = {}
            for kind in ("start_instance", "get_instance"):
                try:
                    found[kind] = self.platform.resolve(kind)
                except Exception:  # noqa: BLE001 - 缺工具是正常情况
                    pass
            self._instance_tools = found
        return self._instance_tools

    def _parse(self, result):
        try:
            from ..platform import render_call_result  # noqa: PLC0415

            return self.platform._as_json(render_call_result(result))
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _needs_wait(data) -> bool:
        if not isinstance(data, dict) or "access_ready" not in data:
            return False
        if data.get("access_ready") is True:
            return False
        status = str(data.get("status") or "").lower()
        return status not in _DEAD_STATUS and (status in _WAIT_STATUS or status == "")

    def wait_ready(self, name: str, args: dict, result, invoke):
        """实例工具返回「还在启动」时，自动轮询到就绪（或超时）再交给上层。

        这一步是必须的：ctf2 的 start 返回只有 access_ready=false / status=starting，
        真正的连接地址 access_url 只有轮询到 running 才会出现。
        """
        tools = self.instance_tools()
        get_tool = tools.get("get_instance")
        if not get_tool or name != tools.get("start_instance") and name != get_tool:
            return result
        data = self._parse(result)
        if not self._needs_wait(data):
            return result

        poll_args = self.translate(get_tool, dict(args))
        deadline = time.monotonic() + _READY_BUDGET
        latest = result
        while time.monotonic() < deadline:
            # 用 Event.wait 代替 sleep：用户点「立即停止」时立刻返回，而不是再等一个轮询间隔
            if self.cancel_event is not None:
                if self.cancel_event.wait(_READY_INTERVAL):
                    return latest
            else:
                time.sleep(_READY_INTERVAL)
            try:
                latest = invoke(get_tool, poll_args)
            except Exception:  # noqa: BLE001 - 单次轮询失败不算失败，继续等
                continue
            now = self._parse(latest)
            if isinstance(now, dict):
                if now.get("access_ready") is True:
                    return latest
                if str(now.get("status") or "").lower() in _DEAD_STATUS:
                    return latest
        return latest

    def seed(self, **facts) -> None:
        """预置已知事实。解题前已知比赛/练习场 id 时先塞进来，
        否则 get_challenge 这类「只传 challenge_id」的调用会缺必填的 practice_ground_id。"""
        for key, value in facts.items():
            self._remember(key, value)

    # 同一事实的多种叫法互相同步，这样「比赛模式记的」也能补给「练习场模式用」
    _SAME_THING = (
        ("competition_id", "practice_ground_id", "ground_id"),
        ("instance_id", "environment_id", "env_id"),
    )

    def _remember(self, key: str, value) -> None:
        if not isinstance(value, str) or not value:
            return
        self.facts[key] = value
        for group in self._SAME_THING:
            if key in group:
                for alias in group:
                    self.facts.setdefault(alias, value)

    def translate(self, name: str, arguments: dict | None) -> dict:
        args = dict(arguments or {})
        props = self.platform.prop_names(name)
        if not props:  # 没有 schema（假服务/自测）→ 不猜，原样放行
            return args
        schema = self.platform.schema_of(name)
        required = list(schema.get("required") or [])
        properties = schema.get("properties") or {}

        # 0) 先记住这一轮带来的事实
        for key in list(args):
            if key in _FACT_FIELDS:
                self._remember(key, args[key])

        # 1) 别名改名：只在目标 schema 不认识原名、且认识新名时换
        for generic, targets in _ALIASES:
            if generic not in args or generic in props:
                continue
            for target in targets:
                if target in props:
                    if target in args:
                        # 目标字段已有值（模型两种叫法都给了）→ 保留原值，丢掉重复的那个，
                        # 避免用 flag 覆盖掉正确答案 answer。
                        args.pop(generic)
                    else:
                        args[target] = args.pop(generic)
                    break

        # 改名后的值同样是事实
        for key in list(args):
            if key in _FACT_FIELDS:
                self._remember(key, args[key])

        # 2) 补齐必填：先用记住的事实，再用 schema 里的 const
        for field in required:
            if args.get(field) not in (None, ""):
                continue
            if field in self.facts:
                args[field] = self.facts[field]
            elif "const" in (properties.get(field) or {}):
                args[field] = properties[field]["const"]

        # 3) const 兜底（required 里仍缺、但有 const 的）
        for field in required:
            if field not in args or args[field] in (None, ""):
                prop = properties.get(field) or {}
                if "const" in prop:
                    args[field] = prop["const"]

        # 4) additionalProperties=false：丢掉 schema 不认识的字段，否则平台直接报错
        if schema.get("additionalProperties") is False:
            args = {k: v for k, v in args.items() if k in props}

        return args


# 单键包装的键名白名单：{"challenge": {...}} 这类需要拆掉
_WRAPPER_KEYS = frozenset({
    "challenge", "data", "item", "result", "detail", "ground", "competition",
    "environment", "instance", "profile", "agent",
})


def _unwrap(value, limit: int = 3):
    """拆掉 {"challenge": {...}} / {"data": {...}} 这类单键包装。

    只拆「唯一键、值是 dict、键名在白名单里」的情况，避免误拆真正的业务对象。
    DASCTF 的 get_practice_challenge 就是包在 challenge 里的，不拆则题目名/描述全取不到。
    """
    for _ in range(limit):
        if not isinstance(value, dict) or len(value) != 1:
            break
        key, inner = next(iter(value.items()))
        if isinstance(inner, dict) and str(key).lower() in _WRAPPER_KEYS:
            value = inner
            continue
        break
    return value


def install(platform):
    """给平台实例装上参数桥接（幂等），返回同一个实例。

    两处补丁，都在实例上，不动类定义：
      * platform.call   —— 参数翻译（call_text / call_raw / 高层方法 / 模型直调全覆盖）
      * platform._as_json —— 返回值解包（{"challenge": {...}} → {...}）
    """
    if getattr(platform, "_arg_bridge", None) is not None:
        return platform
    bridge = ArgBridge(platform)
    original = platform.call
    original_as_json = platform._as_json

    def call(name, arguments=None, *, retries=0):
        args = bridge.translate(name, arguments)
        result = original(name, args, retries=retries)
        # 实例类工具：start 常只返回 starting，这里自动轮询到就绪再交回上层
        return bridge.wait_ready(name, args, result, original)

    def as_json(text):
        return _unwrap(original_as_json(text))

    platform.call = call  # 实例属性覆盖类方法：call_text/call_raw 内部都走 self.call
    platform._as_json = as_json
    platform._arg_bridge = bridge
    return platform