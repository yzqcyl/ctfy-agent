#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地工具：给模型的手，但带着合规护栏。

核心护栏：http_request 只能访问「平台为这道题下发的实例地址」。
  - 白名单按 host:port 端点级校验（端口不再形同虚设）；
  - 重定向每一跳都重新校验，且禁止 https→http 降级（堵死 SSRF 绕过）；
  - 只有实例信息里没有端口时，才放行该主机的任意端口。

shell 默认关闭（config: allow_shell=false）。开启后：
  - 工作目录被限制在 runs/<run>/sandbox/<challenge_id>，避免误读项目根目录的 .env；
  - 命令超时被夹在 [1s, 120s]，模型无法挂死或传非法超时；
  - 首次调用会向 stderr 打一条警告。真正隔离仍需容器层。
"""

from __future__ import annotations

import difflib
import inspect
import json
from http.cookies import SimpleCookie
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urlparse

from .httputil import GuardedRedirect, build_opener, read_limited
from .tools_misc_ext import MISC_EXT_SPECS, call_ext as _call_ext

MAX_BODY = 8000
# http_request 单次返回的正文窗口（字符）。定 5000 而非 8000：solver 会把工具结果
# 截到 MAX_TOOL_TEXT=6000，还得给 JSON 包装 + 转义膨胀留余量，否则翻页会出现盲区。
HTTP_WINDOW = 5000
MAX_GREP_BYTES = 2 * 1024 * 1024  # 本地读取上限：小响应读到 EOF 即止；大响应保留中/尾可切片
MAX_GREP_MATCHES = 30             # 30×~175 字符 ≈ 5.2KB，整张匹配表能塞进 6000 的可见上限
MAX_GREP_PATTERN_LEN = 256        # 正则长度上限（stdlib re 无超时，尽力缓解灾难性回溯）
SHELL_TIMEOUT_MAX = 120
CACHE_MAX_ENTRIES = 16            # body 缓存条数上限（FIFO）
CACHE_MAX_BYTES = 8 * 1024 * 1024  # body 缓存字节上限
DIFF_MAX_CHARS = 4000             # diff 文本上限（solver 还会把工具结果截到 MAX_TOOL_TEXT=6000）
DIFF_CONTEXT_LINES = 1            # 差异上下文行数：IDOR 枚举只要「哪个字段变了」，别把整段再塞回上下文
CHAT_MAX_REPLY = 2500             # chat 单次回复上限：一句回复 + 元数据要塞进 6000 可见窗口
CHAT_MAX_HISTORY = 6              # history 参数最多回几条（回太多等于把整段对话塞回上下文）
CHAT_MAX_TURNS = 40               # 单个会话最多保留的消息条数（防无界增长）
CHAT_TIMEOUT = 20                 # 与 http_request 同量级；流式接口必须限时，否则挂死整步


def as_bool(value) -> bool:
    """宽松布尔：模型常把 true 写成字符串 "true"/"1"/"yes"。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in ("1", "true", "yes", "y", "on")


class ToolError(RuntimeError):
    pass


HTTP_SPEC = {
    "type": "function",
    "function": {
        "name": "http_request",
        "description": (
            "向题目实例发一个 HTTP 请求（只允许访问平台下发的实例地址，重定向也会被拦截校验）。"
            "返回状态码、响应头与一个正文窗口（默认前 5000 字符；窗口后还有内容时带 "
            "truncated/next_offset，可继续翻页）。大文件（打包 JS/HTML）务必用 grep 搜关键词，"
            "或带 offset 分段读，别整段读进上下文——那会很快耗尽 token。不要用 Range 头逐段"
            "硬翻（会被自动当作 offset 处理）。枚举类题目（IDOR/改 id 看回显）用 diff=true："
            "只回与同路径上一次响应的差异行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL，例如 http://10.0.0.9:31001/login"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"], "default": "GET"},
                "body": {"type": "string", "description": "请求体（表单或 JSON 字符串）"},
                "headers": {"type": "object", "description": "附加请求头，例如 {\"Content-Type\": \"application/json\"}"},
                "grep": {
                    "type": "string",
                    "description": (
                        "可选正则。提供时先读完整响应（上限 2MB）再只返回每个匹配附近 ±80 字符的片段"
                        "（带字符偏移），最多 30 条。用于在大 JS/HTML 里搜 flag/凭据/接口/路径，"
                        "例如 \"flag\\\\{[^}]+\\\\}\" 或 \"(admin|token|secret|api)\"。"
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "从正文第 offset 个字符开始返回一个窗口（配合返回的 next_offset 翻页）。"
                        "用于分段读大文件的中段/尾段；与 grep 互斥（给 grep 时忽略 offset）。"
                    ),
                },
                "diff": {
                    "type": "boolean",
                    "description": (
                        "与本 host 同路径（忽略 query）上一次响应做行级差异，只回差异行，"
                        "返回 added_lines/removed_lines/baseline_had/truncated。"
                        "用于 IDOR 枚举（改 id 看哪个字段变了）。true 时跳过缓存读、强制重新请求；"
                        "没有基线（该路径本轮第一次请求）时退回普通窗口输出并标注 baseline_had=false。"
                    ),
                },
            },
            "required": ["url"],
        },
    },
}

# AI 安全题的核心工具：面对的是「会聊天的靶子」，不是静态页面。
# 历史存在工具侧（跨 delegate 共享），模型每次只发「这一句」——这样 10 步能覆盖 10 轮真实对话，
# 而不是把整段对话重发进 body 把上下文和 token 一起烧掉（EnIGMA 的 interactive-tool 论点）。
CHAT_SPEC = {
    "type": "function",
    "function": {
        "name": "chat",
        "description": (
            "与题目里的 AI 助手/机器人对话（LLM 应用、agent、客服 bot 类题）。"
            "工具替你保存上下文，你只需要给**这一句**；返回靶子这一轮的回复与 turn 序号。"
            "多轮技巧：先 benign 开场探能力，之后每轮**引用上一句回复的原文**再逐步加码"
            "（直接索要 key 通常被拒）。同一句重复发会被账本当重复调用拦掉，想重试就改措辞。"
            "不知道端点时先 GET 首页用 http_request 的 grep 找 chat/send/message/api。"
            "支持 SSE/流式响应与 cookie 会话；单次 20s 超时。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "聊天端点完整 URL，例如 http://10.0.0.9:31001/api/chat",
                },
                "message": {"type": "string", "description": "这一轮要发给靶子的话（只有这一句）"},
                "session": {
                    "type": "string",
                    "description": "可选会话名，同一题里同时开多条互不干扰的对话（默认 default）",
                },
                "reset": {"type": "boolean", "description": "清空该会话历史、从第 1 轮重开（换攻击路线时用）"},
                "history": {
                    "type": "integer",
                    "description": f"额外返回最近 N 条历史（默认 0；上限 {CHAT_MAX_HISTORY}）",
                },
                "mode": {
                    "type": "string",
                    "enum": ["json", "form", "text"],
                    "description": "请求体编码：json={\"message\",\"history\"}，form=message=...，text=纯文本",
                },
            },
            "required": ["url", "message"],
        },
    },
}

NOTE_SPEC = {
    "type": "function",
    "function": {
        "name": "note",
        "description": (
            "记录中间线索（接口、凭据、待验证的猜测），写入本题笔记供后续步骤使用。"
            "它**不会**结束本回合，也不会把内容交给协调者。"
            "不要用它交付结论——交付结论必须用 report。"
        ),
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}

SHELL_SPEC = {
    "type": "function",
    "function": {
        "name": "shell",
        "description": "在解题沙箱目录里执行一条 shell 命令（仅在 allow_shell=true 时可用，超时上限 120s）。返回 stdout/stderr 与退出码。",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "default": 60},
            },
            "required": ["command"],
        },
    },
}

# 多智能体协作里，专家角色用它把阶段结论交还给协调者；调用即结束本回合。
# 它是结论的唯一出口：note 只落盘、不回传，协调者看不到。
REPORT_SPEC = {
    "type": "function",
    "function": {
        "name": "report",
        "description": (
            "交付阶段结论的**唯一**方式，调用后本回合立即结束，结论会回到协调者手里。"
            "判断、框架识别、已拿到的 flag、卡点与下一步建议都写在这里。"
            "note 只会写进本地笔记，协调者看不到，不要用它交付结论。"
            "不要把 flag 当结论粘贴（避免污染协调者上下文）。"
            "evidence 必须是你某一次工具输出的**逐字连续片段**（原样复制，不要改写/概括）；"
            "说「发现了/拿到了/绕过了」却不给 evidence 会被拒绝重交。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "evidence": {
                    "type": "string",
                    "description": "支撑上述结论的逐字片段，必须原样复制自某一次工具输出（单次输出内的连续子串）。",
                },
            },
            "required": ["text", "evidence"],
        },
    },
}


class LocalTools:
    def __init__(self, store, challenge_id: str, *, allow_shell: bool = False, verbose: bool = False, cache: dict | None = None, jar: dict | None = None, chats: dict | None = None, allow_chat: bool = True):
        self.store = store
        self.challenge_id = challenge_id
        self.allow_shell = allow_shell
        self.allow_chat = allow_chat
        self.verbose = verbose
        # 本轮 body 缓存：key=(netloc, path?query) → {status, headers, payload, content_length, capped}
        # 由 Solver 统一持有并跨 delegate 共享（省掉重复抓取；桩机制省 token）。
        self.cache = cache if cache is not None else {}
        # 端点白名单：allowed_endpoints 为 "host:port"，allowed_hosts 为无端口时放行的裸主机名
        self.allowed_hosts: set[str] = set()
        self.allowed_endpoints: set[str] = set()
        self._warned_shell = False
        # 会话 cookie jar：netloc -> {cookie 名: 值}。登录后的 Set-Cookie 自动回传，
        # 省得模型手动从响应头抠 Cookie 再拼 headers（a07 认证题 / a01 需登录触达 IDOR 是高频路径）。
        # 与 body 缓存一样由 Solver 持有并跨 delegate 共享：否则登录会话每轮 delegate 归零，
        # 认证题直接退回匿名态（审计 B1）。
        self._jar: dict[str, dict[str, str]] = jar if jar is not None else {}
        # 对话历史：key=(netloc, session) → {"url", "turn", "messages":[{"role","content"}]}
        # 与 jar/cache 同级、由 Solver 持有并跨 delegate 共享——否则每轮 delegate 归零，
        # 多轮阶梯（Crescendo）根本无从谈起。
        self._chats: dict[tuple[str, str], dict] = chats if chats is not None else {}

    # ---------- 合规：允许的目标 ----------

    @staticmethod
    def _norm_host(host: str | None) -> str:
        host = (host or "").strip().lower().rstrip(".")
        if host.startswith("[") and host.endswith("]"):
            host = host[1:-1]
        return host

    def allow_instance(self, host: str, port: int | None = None) -> None:
        host = self._norm_host(host)
        if not host:
            return
        if port:
            self.allowed_endpoints.add(f"{host}:{int(port)}")
        else:
            # 没有端口信息时才放行整台主机；有端口就只放行该端点
            self.allowed_hosts.add(host)

    def allow_many(self, value) -> None:
        """从 start_instance 的返回值里递归提取可访问端点。

        真实平台形状：{"id": ..., "surface": {"services": [{"host": ..., "port": 443, ...}]}}
        所以按「任何 dict 里出现 host/ip/addr/address/hostname 就登记该 host:port」来做，
        不绑定具体层级，平台改结构也不会漏。
        """
        self._walk(value)

    HOST_KEYS = ("host", "hostname", "ip", "addr", "address", "endpoint")

    def _walk(self, value, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, dict):
            host = None
            for key in self.HOST_KEYS:
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    host = candidate.strip()
                    break
            if host:
                port = value.get("port")
                port_int = int(port) if str(port if port is not None else "").isdigit() else None
                self.allow_instance(host, port_int)
            for inner in value.values():
                if isinstance(inner, (dict, list)):
                    self._walk(inner, depth + 1)
        elif isinstance(value, list):
            for inner in value:
                self._walk(inner, depth + 1)
        elif isinstance(value, str):
            self._allow_url(value)

    def _allow_url(self, text: str) -> None:
        try:
            parsed = urlparse(text)
            if parsed.hostname:
                self.allow_instance(parsed.hostname, parsed.port)
        except Exception:
            pass

    def _check_target(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ToolError(f"只支持 http/https，收到 {parsed.scheme or '(空)'}")
        host = self._norm_host(parsed.hostname)
        if not host:
            raise ToolError(f"URL 里没有主机名: {url}")
        if not self.allowed_hosts and not self.allowed_endpoints:
            raise ToolError(
                "还没有登记任何可访问实例。先用 start_instance 启动题目实例，再访问它返回的 host:port。"
            )
        if host in self.allowed_hosts:
            return
        try:
            port = parsed.port
        except ValueError:
            raise ToolError(f"URL 端口非法: {url}")
        if port is None:
            # URL 没写端口时按协议默认端口对齐（https=443, http=80），
            # 否则访问 https://host/ 会被「只登记了 host:443」误拦（CTFARENA-035 实测）。
            port = 443 if parsed.scheme == "https" else 80
        if f"{host}:{port}" in self.allowed_endpoints:
            return
        raise ToolError(
            f"合规拦截：{host} 不是平台为本题下发的实例地址。"
            f"已登记主机：{', '.join(sorted(self.allowed_hosts)) or '(无)'}；"
            f"已登记端点：{', '.join(sorted(self.allowed_endpoints)) or '(无)'}。"
            "只允许攻击平台实例，禁止访问第三方站点。"
        )

    # ---------- 规格 ----------

    def specs(self) -> list[dict]:
        specs = [HTTP_SPEC, NOTE_SPEC]
        if self.allow_chat:
            specs.append(CHAT_SPEC)
        if self.allow_shell:
            specs.append(SHELL_SPEC)
        specs.extend(MISC_EXT_SPECS)  # MISC 扩展工具（单一来源：tools_misc_ext.MISC_EXT_SPECS）
        return specs

    def names(self) -> set[str]:
        return {spec["function"]["name"] for spec in self.specs()}

    # ---------- MISC 路径锚定 ----------

    def _sandbox_dir(self) -> Path:
        """本题沙盒根，与 shell() 的 cwd 一致，让相对路径在「shell 里能读写」的地方也能被 misc 工具读写。"""
        run_dir = getattr(self.store, "run_dir", None)
        if run_dir is None:
            return Path(self.store)  # 测试直接传 Path 作为工作根
        return Path(run_dir) / "sandbox" / str(self.challenge_id)

    def _resolve_path(self, p: str) -> str:
        """把模型给的路径锚定到沙盒：相对路径拼到沙盒根；绝对路径必须仍在沙盒内，否则拒绝（审计 A2/A9）。"""
        base = self._sandbox_dir().resolve()
        raw = Path(p)
        target = (raw if raw.is_absolute() else base / raw).resolve()
        if not target.is_relative_to(base):
            raise ToolError(f"路径越界：{p!r} 不在本题沙盒 {base} 内（misc 工具只允许读写沙盒目录）")
        return str(target)

    # ---------- 执行 ----------

    def call(self, name: str, arguments: dict | None = None) -> str:
        """按签名分发，只接受工具真实声明的参数。

        模型常幻觉出 schema 之外的参数（实测 http_request 被传过 `limit`），
        直接 `**arguments` 会 TypeError 崩掉整步（[执行异常]）。这里：
          - 丢弃未知参数，回显「已忽略 + 合法参数清单」，让模型一步自愈；
          - 缺必需参数（签名级 TypeError）统一转成带清单的 ToolError，不再裸崩。
        """
        arguments = dict(arguments or {})
        if name == "shell" and not self.allow_shell:
            raise ToolError("shell 未开启：config/agent.json 里 allow_shell=false")
        if name not in self.names():
            raise ToolError(f"未知本地工具 {name}")
        fn = getattr(self, name)
        sig = inspect.signature(fn)
        accepted: dict = {}
        dropped: list[str] = []
        for key, value in arguments.items():
            if key in sig.parameters:
                accepted[key] = value
            else:
                dropped.append(str(key))
        note = ""
        if dropped:
            legal = ", ".join(sorted(sig.parameters))
            note = f"（已忽略未知参数：{', '.join(sorted(dropped))}；合法参数：{legal}）"
        try:
            result = fn(**accepted)
        except TypeError as exc:
            legal = ", ".join(sorted(sig.parameters))
            raise ToolError(f"参数错误：{exc}。合法参数：{legal}") from None
        return note + "\n" + result if note else result

    # ---------- MISC 扩展工具 ----------
    # 实现在 tools_misc_ext.py（纯 stdlib、可独立单测）；这里只做契约适配：
    # 同名方法 + 参数名与 SPEC 一致 + 返回值交给 call_ext 保证 ≤6000 与 ToolError 语义。

    def decode_blob(self, text: str, max_layers: int = 5) -> str:
        return _call_ext("decode_blob", {"text": text, "max_layers": max_layers})

    def unpack_archive(self, path: str, max_depth: int = 3, out_dir: str = "extracted") -> str:
        return _call_ext(
            "unpack_archive",
            {
                "path": self._resolve_path(path),
                "max_depth": max_depth,
                "out_dir": self._resolve_path(out_dir),
            },
        )

    def hexpeek(self, path: str, offset: int = 0, length: int = 256) -> str:
        return _call_ext("hexpeek", {"path": self._resolve_path(path), "offset": offset, "length": length})

    @staticmethod
    def _parse_range(value: str, current: int) -> int:
        """把模型常写的 `Range: bytes=A-` 转成本地字符 offset（只取起点 A）。

        纯 ASCII 的 JS/HTML 与字节一一对应；含多字节字符时是近似，CTF web 场景可接受。
        """
        m = re.search(r"bytes\s*=\s*(\d+)-", (value or "").lower())
        return max(0, int(m.group(1))) if m else current

    def _invalidate_host(self, netloc: str) -> None:
        for key in [k for k in self.cache if k[0] == netloc]:
            del self.cache[key]

    def _cache_put(self, key, entry: dict) -> None:
        if key is None:
            return
        self.cache.pop(key, None)  # 同 key 重抓：先删再插，刷新 FIFO 位置（否则最新反而最先被逐，审计 A2）
        self.cache[key] = entry
        while len(self.cache) > CACHE_MAX_ENTRIES:
            self.cache.popitem(last=False)  # FIFO
        total = sum(len(e.get("payload") or "") for e in self.cache.values())
        while total > CACHE_MAX_BYTES and self.cache:
            k = next(iter(self.cache))
            total -= len(self.cache[k].get("payload") or "")
            del self.cache[k]

    @staticmethod
    def _render_cache_stub(entry: dict) -> str:
        payload = entry.get("payload") or ""
        raw_total = entry.get("content_length")
        if raw_total is None:
            total = f">={MAX_GREP_BYTES}" if entry.get("capped") else len(payload)
        else:
            total = int(raw_total)
        return json.dumps(
            {
                "status": entry.get("status"),
                "cached": True,
                "total_bytes": total,
                "body": payload[:500],
                "note": "该 URL 本轮已读过（缓存命中，未重复返回全文）。要看任意片段请带 offset 或 grep。",
            },
            ensure_ascii=False,
        )

    def _capture_cookies(self, netloc: str, header_items) -> None:
        """从响应头捞 Set-Cookie 存进 jar。

        urllib 会把重复的 Set-Cookie 头拆成多条 items，每条一个 cookie。用 stdlib
        SimpleCookie 解析：它正确处理 Expires 属性里的逗号（"Wed, 21 Oct ..."），也保留
        cookie 值里的逗号——手写 split(",") 在这两处都会截断（审计 B4）。
        空值 k= 表示服务器删除该 cookie，从 jar 移除（审计 B5）。
        """
        jar = self._jar.setdefault(netloc, {})
        for key, value in header_items:
            if str(key).lower() != "set-cookie":
                continue
            try:
                parsed = SimpleCookie()
                parsed.load(str(value))
            except Exception:  # noqa: BLE001 - 单个畸形 cookie 跳过，别因坏头击穿整个请求
                continue
            for name, morsel in parsed.items():
                if not name:
                    continue
                if morsel.value == "":
                    jar.pop(name, None)   # 空值 = 删除语义
                else:
                    jar[name] = morsel.value

    def _cookie_header(self, netloc: str) -> str | None:
        jar = self._jar.get(netloc)
        return "; ".join(f"{k}={v}" for k, v in jar.items()) if jar else None

    def http_request(self, url: str, method: str = "GET", body: str = "", headers: dict | None = None, grep: str = "", offset: int = 0, limit: int = 0, diff: bool = False) -> str:
        self._check_target(url)
        netloc = urlparse(url).netloc
        method = (method or "GET").upper()
        diff = as_bool(diff)
        data = body.encode("utf-8") if body else None

        # 拦截模型常写的 Range 头：不转发给服务器（越界会 416、且分片再被截断就成盲区），
        # 本地解析成 offset。这样服务器永远回 200，416 从根上消失。
        forwarded: dict[str, str] = {}
        for key, value in (headers or {}).items():
            if str(key).lower() == "range":
                offset = self._parse_range(str(value), int(offset or 0))
            else:
                forwarded[str(key)] = str(value)
        # 显式要 identity，防服务器回 gzip 乱码（urllib 不自动解压）
        if not any(str(k).lower() == "accept-encoding" for k in forwarded):
            forwarded["Accept-Encoding"] = "identity"
        # 会话续期：jar 里有该 host 的 cookie 且模型没显式传 Cookie 时，自动带上
        if not any(str(k).lower() == "cookie" for k in forwarded):
            cookie = self._cookie_header(netloc)
            if cookie:
                forwarded["Cookie"] = cookie

        request = urllib.request.Request(url, data=data, method=method)
        for key, value in forwarded.items():
            request.add_header(key, value)
        if not request.has_header("User-agent"):
            request.add_header("User-Agent", "ctfy-agent/0.2")

        offset = max(0, int(offset or 0))
        limit = max(0, int(limit or 0))

        # 缓存只服务 GET：非 GET 会改变服务端状态，清掉同 host 的旧缓存防脏读
        if method != "GET":
            self._invalidate_host(netloc)

        cache_key = None
        if method == "GET":
            path_q = urlparse(url).path + (("?" + urlparse(url).query) if urlparse(url).query else "")
            # cookie 也进缓存 key：登录前/后的同名 URL 响应可能不同，不区分就会串会话
            effective_cookie = next((v for k, v in forwarded.items() if str(k).lower() == "cookie"), "") or ""
            cache_key = (netloc, path_q, effective_cookie)
            hit = self.cache.get(cache_key)
            # diff 要「这次真实响应」当右侧：跳过缓存读（cache_key 仍留着事后落盘）
            if hit is not None and not diff:
                if not grep and not offset:
                    # 完全重复读：返回桩而不是再次塞全文进上下文（省 token 的真正来源）
                    return self._render_cache_stub(hit)
                # 带 offset/grep：直接从缓存切片/筛，省掉一次网络往返
                return self._render_http(
                    hit.get("status") or 200,
                    hit.get("headers") or {},
                    hit.get("payload") or "",
                    grep,
                    offset,
                    hit.get("content_length"),
                    limit,
                )

        # 恒读满 2MB：小响应读到 EOF 即止（无额外成本），大响应保留中/尾供 offset 切片。
        opener = build_opener(GuardedRedirect(self._check_target))
        status = 0
        interesting: dict = {}
        content_length = None
        payload = ""
        try:
            with opener.open(request, timeout=20) as response:
                payload = read_limited(response, MAX_GREP_BYTES).decode("utf-8", "replace")
                status = response.status
                interesting = {
                    key: value for key, value in response.headers.items()
                    if key.lower() in ("content-type", "location", "set-cookie", "server", "www-authenticate")
                }
                self._capture_cookies(netloc, response.headers.items())
                content_length = response.headers.get("Content-Length")
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                payload = read_limited(exc, MAX_GREP_BYTES).decode("utf-8", "replace")
            except Exception:
                payload = ""
            if exc.headers:
                self._capture_cookies(netloc, exc.headers.items())
                content_length = exc.headers.get("Content-Length")
        except urllib.error.URLError as exc:
            raise ToolError(f"请求失败 {url}: {exc.reason}") from None
        except OSError as exc:
            # 连接被重置/中断/读超时等（http.client 会直接抛 ConnectionAbortedError 之类，
            # 不走 URLError），统一转成 ToolError 回给模型，别让整轮崩掉。
            raise ToolError(f"请求失败 {url}: {exc}") from None
        # ToolError 从 GuardedRedirect 的 _check_target 抛出来，原样上抛

        # 基线必须在上面的 _cache_put 之前取：否则刚抓到的这一份会变成「最近一条」，自己和自己比。
        baseline = self._diff_baseline(netloc, urlparse(url).path) if diff else None

        if method == "GET" and status == 200:
            self._cache_put(
                cache_key,
                {
                    "status": status,
                    "headers": interesting,
                    "payload": payload,
                    "content_length": content_length,
                    "capped": len(payload) >= MAX_GREP_BYTES,
                },
            )
        if diff:
            return self._render_diff(status, interesting, payload, baseline, grep, offset, content_length, limit)
        return self._render_http(status, interesting, payload, grep, offset, content_length, limit)

    def _diff_baseline(self, netloc: str, path: str) -> str | None:
        """diff 的基线：缓存里同 host + 同 path（忽略 query）的**最近一条**响应的正文。

        缓存是 dict（插入序），倒着扫第一条同 path 的就是最近一次。带 query 枚举 IDOR 时
        /api/user?id=1 是 /api/user?id=2 的天然基线；cookie 不参与匹配（同一路径换会话也少见）。
        """
        for key in reversed(list(self.cache)):
            if key[0] != netloc:
                continue
            if str(key[1]).split("?", 1)[0] != path:
                continue
            payload = (self.cache.get(key) or {}).get("payload")
            if isinstance(payload, str):
                return payload
        return None

    @staticmethod
    def _render_diff(status: int, headers: dict, payload: str, baseline: str | None,
                     grep: str, offset: int, content_length: str | None, limit: int) -> str:
        """diff=true 的输出：有基线只回行级差异；没基线退回普通窗口（不假装比过）。"""
        if baseline is None:
            out = json.loads(LocalTools._render_http(status, headers, payload, grep, offset, content_length, limit))
            out["baseline_had"] = False
            out["added_lines"] = 0
            out["removed_lines"] = 0
            out.setdefault("truncated", False)
            out["note"] = (
                "该 host+path 本轮还没有更早的响应，没有基线可比——下面是完整窗口输出。"
                "再请求一次同 path（只改 query 即可）并带 diff=true 就能看到差异行。"
            )
            return json.dumps(out, ensure_ascii=False)

        added = removed = 0
        shown: list[str] = []
        used = 0
        truncated = False
        for index, line in enumerate(
            difflib.unified_diff(
                baseline.splitlines(),
                payload.splitlines(),
                fromfile="baseline",
                tofile="current",
                lineterm="",
                n=DIFF_CONTEXT_LINES,
            )
        ):
            if index < 2:
                continue  # "--- baseline" / "+++ current" 文件头，不是内容行
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
            size = len(line) + 1
            if used + size <= DIFF_MAX_CHARS:
                shown.append(line)
                used += size
            else:
                truncated = True  # 计数仍然全量统计，只是不再往上下文里塞
        out = {
            "status": status,
            "headers": headers,
            "diff": "\n".join(shown),
            "added_lines": added,
            "removed_lines": removed,
            "baseline_had": True,
            "truncated": truncated,
        }
        if content_length is not None:
            out["total_bytes"] = int(content_length)
        if not shown:
            out["note"] = "与基线逐行相同（这次响应没有变化）。"
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def _render_http(status: int, headers: dict, payload: str, grep: str, offset: int = 0, content_length: str | None = None, limit: int = 0) -> str:
        offset = max(0, int(offset or 0))
        wsize = HTTP_WINDOW
        if limit:
            # 模型主动要更小的窗口时尊重它；夹在 [200, 5000] 防 limit=1 这类退化请求
            wsize = max(200, min(int(limit), HTTP_WINDOW))
        total = len(payload)
        capped = total >= MAX_GREP_BYTES  # 读到本地 2MB 上限（未必是文件末尾）

        if grep:
            if len(grep) > MAX_GREP_PATTERN_LEN:
                raise ToolError(f"grep 正则过长（{len(grep)}>{MAX_GREP_PATTERN_LEN} 字符），请精简")
            try:
                pattern = re.compile(grep)
            except re.error as exc:
                raise ToolError(f"grep 正则无效: {exc}") from None
            # 按「匹配片段 ±80 字符」抽取，而非按行——压缩过的 JS 常是单行，按行会把整包返回。
            # 边扫边截到 MAX_GREP_MATCHES：绝不全量物化（宽正则打 2MB 会 OOM，审计 B11）。
            shown: list[str] = []
            truncated = False
            for m in pattern.finditer(payload):
                if len(shown) >= MAX_GREP_MATCHES:
                    truncated = True
                    break
                shown.append(f"[{m.start()}] {payload[max(0, m.start() - 80): m.end() + 80]}")
            out = {
                "status": status,
                "headers": headers,
                "grep": grep,
                "matched": len(shown),
                "truncated": truncated,
                "body": "\n".join(shown),
            }
            if content_length is not None:
                out["total_bytes"] = int(content_length)
            elif capped:
                out["total_bytes"] = f">={MAX_GREP_BYTES}"
            return json.dumps(out, ensure_ascii=False)

        window = payload[offset : offset + wsize]
        # 元数据放在 body 之前：即使结果被 solver 截断，翻页锚点也不会丢
        out = {"status": status, "headers": headers}
        if offset > 0:
            out["offset"] = offset
        more = offset + wsize < total
        if more:
            out["truncated"] = True
            out["next_offset"] = offset + len(window)
        if content_length is not None:
            out["total_bytes"] = int(content_length)
        elif capped:
            # 读到本地上限但未知真实末尾：显式打标记，别让模型把工具上限当成文件末尾
            out["local_cap"] = True
        if offset >= total and total:
            out["past_end"] = True
        out["body"] = window
        return json.dumps(out, ensure_ascii=False)

    # ---------- 对话工具（AI 安全题型：靶子是「会聊天的服务」） ----------

    CHAT_REPLY_KEYS = (
        "reply", "response", "answer", "message", "content", "text",
        "output", "result", "data", "completion", "generated_text",
    )
    # 兜底遍历时跳过的元数据键：content 为空时，别把 "role":"assistant" /
    # "finish_reason":"stop" / "object":"chat.completion" 这类字段当成回复抽出来
    CHAT_META_KEYS = frozenset({
        "role", "id", "object", "created", "model", "finish_reason", "usage",
        "index", "type", "name", "status", "code", "error_type", "session_id",
    })

    @staticmethod
    def _extract_reply(value, depth: int = 0) -> str | None:
        """从靶子的 JSON 回复里抽出「那一句」。深度受限；抽不到返回 None（调用方退回原文）。"""
        if depth > 4:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for key in LocalTools.CHAT_REPLY_KEYS:
                if key in value:
                    got = LocalTools._extract_reply(value[key], depth + 1)
                    if got:
                        return got
            for key, inner in value.items():
                if key in LocalTools.CHAT_META_KEYS:
                    continue
                got = LocalTools._extract_reply(inner, depth + 1)
                if got:
                    return got
            return None
        if isinstance(value, list):
            parts = [p for p in (LocalTools._extract_reply(v, depth + 1) for v in value) if p]
            return "\n".join(parts) if parts else None
        return None

    @staticmethod
    def _parse_sse(payload: str) -> str:
        """把 SSE/流式正文拼成一整句：data: 里取 delta/content/token，[DONE] 结束。

        AGENTBENCH-005 这类题就是 SSE 输出 + 输出过滤；http_request 会等连接关闭而超时，
        这里按行聚合、遇到结束标记即止，顺带把分片拼回可引用的一整句。
        """
        parts: list[str] = []
        for line in (payload or "").splitlines():
            line = line.strip()
            if not line or line.startswith(":") or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except ValueError:
                parts.append(chunk)
                continue
            piece = None
            if isinstance(obj, str):
                piece = obj
            elif isinstance(obj, dict):
                delta = obj.get("delta")
                if isinstance(delta, dict):
                    piece = delta.get("content") or delta.get("text")
                if not piece:
                    for key in ("content", "text", "token", "message", "output", "reply"):
                        if key in obj and isinstance(obj[key], str):
                            piece = obj[key]
                            break
            if piece:
                parts.append(piece)
        return "".join(parts).strip()

    def chat(self, url: str, message: str, session: str = "default", reset: bool = False,
             history: int = 0, mode: str = "json") -> str:
        """与靶子对话一轮：历史存工具侧，只回这一轮的回复。

        合规与 http_request 完全一致（只能打本题实例、重定向逐跳校验）；
        会话 cookie 复用同一个 jar，登录态跨轮保持。
        """
        self._check_target(url)
        text = (message or "").strip()
        if not text:
            raise ToolError("chat 需要非空 message")
        session = (session or "default").strip() or "default"
        netloc = urlparse(url).netloc
        key = (netloc, session)
        if as_bool(reset):
            self._chats.pop(key, None)
        state = self._chats.get(key)
        if state is None:
            state = {"url": url, "turn": 0, "messages": []}
            self._chats[key] = state
        state["url"] = url
        pairs: list[dict] = state["messages"]

        mode = (mode or "json").strip().lower()
        if mode == "text":
            data = text.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        elif mode == "form":
            data = urlencode({"message": text}).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        else:
            mode = "json"
            data = json.dumps(
                {"message": text, "session": session, "history": pairs[-12:]}, ensure_ascii=False
            ).encode("utf-8")
            content_type = "application/json"

        forwarded = {
            "Content-Type": content_type,
            "Accept": "text/event-stream, application/json, text/plain;q=0.9",
            "Accept-Encoding": "identity",
        }
        cookie = self._cookie_header(netloc)
        if cookie:
            forwarded["Cookie"] = cookie

        request = urllib.request.Request(url, data=data, method="POST")
        for header, value in forwarded.items():
            request.add_header(header, value)
        request.add_header("User-Agent", "ctfy-agent/0.2")

        opener = build_opener(GuardedRedirect(self._check_target))
        status = 0
        payload = ""
        ctype = ""
        try:
            with opener.open(request, timeout=CHAT_TIMEOUT) as response:
                status = response.status
                ctype = str(response.headers.get("Content-Type") or "").lower()
                payload = read_limited(response, MAX_GREP_BYTES).decode("utf-8", "replace")
                self._capture_cookies(netloc, response.headers.items())
        except urllib.error.HTTPError as exc:
            # 4xx/5xx 往往是靶子的「拒绝」语义本身：把 body 交回模型，别当异常吞掉
            status = exc.code
            try:
                ctype = str((exc.headers or {}).get("Content-Type") or "").lower()
                payload = read_limited(exc, MAX_GREP_BYTES).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001 - 错误体读不到就算了
                payload = ""
            if exc.headers:
                self._capture_cookies(netloc, exc.headers.items())
        except urllib.error.URLError as exc:
            raise ToolError(f"对话请求失败 {url}: {exc.reason}") from None
        except OSError as exc:
            raise ToolError(
                f"对话请求失败 {url}: {exc}（流式端点若一直不关连接就会超时；"
                f"换成非流式路径，或先确认这个端点是否需要先建立会话）"
            ) from None

        reply = ""
        if "event-stream" in ctype or payload.lstrip().startswith("data:"):
            reply = self._parse_sse(payload)
        if not reply:
            obj = None
            try:
                obj = json.loads(payload)
            except ValueError:
                obj = None
            if obj is not None:
                reply = self._extract_reply(obj) or ""
        if not reply:
            reply = payload.strip()
        reply = reply[:CHAT_MAX_REPLY]

        pairs.append({"role": "user", "content": text})
        if reply:
            pairs.append({"role": "assistant", "content": reply[:CHAT_MAX_REPLY]})
        if len(pairs) > CHAT_MAX_TURNS:
            del pairs[: len(pairs) - CHAT_MAX_TURNS]
        state["turn"] += 1

        try:
            want = max(0, min(int(history or 0), CHAT_MAX_HISTORY))
        except (TypeError, ValueError):
            want = 0

        # 输出预算（审核 P2-1）：head≤~170 + reply≤2500 + 历史块≤2400 + 尾注≤~70 ≈ 5.2k，
        # 必须整体压在 solver 的 MAX_TOOL_TEXT=6000 之内——超了会把尾部的 [note] 和后几条
        # 历史截掉，而那些恰恰是「下一句怎么写」的锚点。
        path = (urlparse(url).path or "/")[:80]
        head = f"turn {state['turn']} | POST {path} | {status}"
        if status >= 400:
            head += "（端点或参数可能不对：先 GET 首页用 http_request + grep 确认聊天路径）"
        lines = [head, "[reply]", reply if reply else "(空回复)"]
        if want:
            lines.append(f"[history 最近 {want} 轮]")
            budget = 2400
            for item in pairs[-2 * want:]:
                piece = f"{item['role']}: {item['content'][:200]}"
                if budget - len(piece) < 0:
                    lines.append("…（历史太长，用更小的 history 或 reset=true 重开）")
                    break
                lines.append(piece)
                budget -= len(piece) + 1
        lines.append("[note] 下一句请引用上面的原文再推进；换攻击路线用 reset=true，别重复发同一句")
        return "\n".join(lines)

    def note(self, text: str) -> str:
        self.store.add_note(self.challenge_id, text)
        return f"已记录（{len(text)} 字），这道题已有 {len(self.store.require(self.challenge_id).notes)} 条笔记。"

    @staticmethod
    def _windows_command_words(command: str) -> list[str]:
        """抽出真正会被当作「程序名」的词：命令首词 + 每个 shell 分隔符之后的首词。

        只查这些位置，避免把 `curl https://host/cat.jpg` 里的 `cat` 误判成命令。
        """
        words: list[str] = []
        for part in re.split(r"\||&&|;|\r|\n", command or ""):
            m = re.match(r"\s*([^\s]+)", part)
            if m:
                words.append(m.group(1).strip("\"'"))
        return words

    @classmethod
    def _windows_shell_guard(cls, command: str) -> str | None:
        unix_only = ("ls", "cat", "grep", "head", "tail", "pwd", "which", "wc", "cp", "mv", "sed", "awk", "xxd", "sh", "bash")
        cmds = cls._windows_command_words(command)
        notes: list[str] = []
        missing = sorted({c for c in cmds if c in unix_only})
        if missing:
            notes.append(f"Windows shell 无 {'/'.join(missing)}；文本处理改用 http_request 的 grep/offset")
        if "python3" in cmds:
            notes.append("Windows 上解释器叫 `python`（不是 python3）")
        if "<<" in command:
            notes.append("Windows cmd 不支持 heredoc(<<)，把脚本写进文件再跑，或改 python -c")
        if "'" in command:
            notes.append("Windows cmd 不认单引号，用双引号")
        return "；".join(notes) if notes else None

    def shell(self, command: str, timeout: int = 60) -> str:
        if not self._warned_shell:
            self._warned_shell = True
            print(
                "[warn] shell 工具已开启：命令以当前用户身份执行，工作目录受限但并非真正沙箱。"
                "正式比赛请在隔离容器内运行。",
                file=sys.stderr,
            )

        try:
            limit = int(timeout)
        except (TypeError, ValueError):
            limit = 60
        limit = max(1, min(limit, SHELL_TIMEOUT_MAX))

        # 本地 Windows 开发专用护栏：容器是 Linux（这些命令都在），只有 cmd 会踩空。
        # 与其让 cmd 吐一句没用的英文报错，不如提前把模型指回 http_request 的 grep/offset。
        if os.name == "nt":
            hint = self._windows_shell_guard(command)
            if hint:
                raise ToolError(hint)

        cwd = self._sandbox_dir()
        cwd.mkdir(parents=True, exist_ok=True)

        # 让 shell 里的 `python` 指向当前解释器：宿主 PATH 上的 python 可能只是
        # 应用商店占位符（实测会报 "Python was not found"），模型写脚本时就会踩空。
        env = dict(os.environ)
        interpreter_dir = str(Path(sys.executable).parent)
        env["PATH"] = interpreter_dir + os.pathsep + env.get("PATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")

        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                timeout=limit,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(cwd),
                env=env,
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"命令超时（{limit}s）: {command}") from None
        output = {
            "exit_code": completed.returncode,
            "stdout": (completed.stdout or "")[-MAX_BODY:],
            "stderr": (completed.stderr or "")[-2000:],
        }
        return json.dumps(output, ensure_ascii=False)
