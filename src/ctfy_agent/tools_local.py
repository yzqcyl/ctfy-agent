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

import base64
import difflib
import inspect
import hashlib
import json
import http.client
from html.parser import HTMLParser
from http.cookies import SimpleCookie
import os
import re
import ssl
import subprocess
import sys
import threading
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlparse

from .httputil import GuardedRedirect, NoRedirect, build_opener, read_limited
from .tools_domain_ext import DOMAIN_NAMES as _DOMAIN_NAMES, call_domain as _call_domain
from .tools_misc_ext import MISC_EXT_SPECS, call_ext as _call_ext
from .tools_webscan import FUZZ_MATCH_CODES_DEFAULT, FUZZ_SPEC, run_fuzz as _webscan_fuzz
from .tools_websec import (
    AUTHDIFF_SPEC,
    JSRECON_SPEC,
    run_authdiff as _websec_authdiff,
    run_jsrecon as _websec_jsrecon,
)
from .tools_websql import SQLI_SPEC, run_sqli as _websql_sqli
from .traffic import resolve as traffic_resolve

MAX_BODY = 8000
# http_request 单次返回的正文窗口（字符）。定 5000 而非 8000：solver 会把工具结果
# 截到 MAX_TOOL_TEXT=6000，还得给 JSON 包装 + 转义膨胀留余量，否则翻页会出现盲区。
HTTP_WINDOW = 5000
MAX_GREP_BYTES = 2 * 1024 * 1024  # 本地读取上限：小响应读到 EOF 即止；大响应保留中/尾可切片
MAX_GREP_MATCHES = 30             # 30×~175 字符 ≈ 5.2KB，整张匹配表能塞进 6000 的可见上限
MAX_GREP_PATTERN_LEN = 256        # 正则长度上限（stdlib re 无超时，尽力缓解灾难性回溯）
MAX_GREP_PATTERNS = 6             # O1a：一次调用最多同时搜 6 个正则（多搜 = 少抓一轮）
MAX_GREP_PER_PATTERN = 8          # O1a：多 pattern 模式下每个 pattern 命中上限
SHELL_TIMEOUT_MAX = 120
SHELL_HEAD_BYTES = 3000           # 子进程输出保留的头部字节（报错/用法提示通常在最前）
SHELL_TAIL_BYTES = 9000           # 保留的尾部字节（模型看的是末尾，MAX_BODY=8000 从这里取）
CACHE_MAX_ENTRIES = 16            # body 缓存条数上限（FIFO）
CACHE_MAX_BYTES = 8 * 1024 * 1024  # body 缓存字节上限
CACHE_MAX_ERROR_ENTRIES = 64      # O1b：403/404 保留上限（总容量 16 时由「优先逐非 200」生效，见 _cache_put）
CACHE_ERROR_MAX_BODY = 64 * 1024  # O1b：只缓存小体积的非 200 响应（错误页通常很小）


def _normalize_grep(grep) -> tuple[list[str], int]:
    """O1a：把 grep 参数规范成 pattern 列表。

    - `str` 原样单元素（旧行为完全不变）；`list/tuple` 保序去重；
    - 丢空串与重复项，截断到 MAX_GREP_PATTERNS，返回被丢弃的个数（输出里提示模型）。
    """
    if grep is None or grep == "":
        return [], 0
    items = [grep] if isinstance(grep, str) else list(grep)
    out: list[str] = []
    for item in items:
        text = str(item or "")
        if not text or text in out:
            continue
        out.append(text)
    dropped = max(0, len(out) - MAX_GREP_PATTERNS)
    return out[:MAX_GREP_PATTERNS], dropped
DIFF_MAX_CHARS = 4000             # diff 文本上限（solver 还会把工具结果截到 MAX_TOOL_TEXT=6000）
DIFF_CONTEXT_LINES = 1            # 差异上下文行数：IDOR 枚举只要「哪个字段变了」，别把整段再塞回上下文
CHAT_MAX_REPLY = 2500             # chat 单次回复上限：一句回复 + 元数据要塞进 6000 可见窗口
CHAT_MAX_HISTORY = 6              # history 参数最多回几条（回太多等于把整段对话塞回上下文）
CHAT_MAX_TURNS = 40               # 单个会话最多保留的消息条数（防无界增长）
CHAT_TIMEOUT = 20                 # 与 http_request 同量级；流式接口必须限时，否则挂死整步
# 值得回给模型的响应头白名单（成功与 3xx/4xx/5xx 两条路径共用；401 的 WWW-Authenticate、302 的 Location 都要见得到）
_INTERESTING_HEADERS = ("content-type", "location", "set-cookie", "server", "www-authenticate")


# ---------- 子进程有界收割（P0-2） ----------

class _BoundedSink:
    """只保留「头 N 字节 + 尾 M 字节」的输出缓冲，内存占用与输出总量无关。"""

    __slots__ = ("head", "tail", "total", "_head_bytes", "_tail_bytes")

    def __init__(self, head_bytes: int, tail_bytes: int):
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._head_bytes = max(0, head_bytes)
        self._tail_bytes = max(0, tail_bytes)

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        room = self._head_bytes - len(self.head)
        if room > 0:
            self.head.extend(chunk[:room])
            chunk = chunk[room:]
        if chunk:
            self.tail.extend(chunk)
            overflow = len(self.tail) - self._tail_bytes
            if overflow > 0:
                del self.tail[:overflow]

    def text(self) -> str:
        head = self.head.decode("utf-8", errors="replace")
        tail = self.tail.decode("utf-8", errors="replace")
        omitted = self.total - len(self.head) - len(self.tail)
        if omitted > 0:
            return f"{head}\n…[中间省略 {omitted} 字节，可用重定向/offset 取回]…\n{tail}"
        return head + tail


def _kill_tree(process: subprocess.Popen) -> None:
    """杀掉整棵进程树。

    `shell=True` 时进程是 shell，真正干活的是它的子进程；只杀 shell 会留下孙进程继续占 pid
    （平台 limits.pids=32，几次就爆）。POSIX 用独立进程组 killpg，Windows 用 taskkill /T。
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, timeout=10,
            )
            if process.poll() is None:
                process.kill()
        else:
            import signal  # noqa: PLC0415 - 仅 POSIX 需要

            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
    except Exception:  # noqa: BLE001 - 清理失败也要继续，不能反过来把工具调用搞崩
        try:
            process.kill()
        except Exception:  # noqa: BLE001
            pass


def run_bounded(command: str, *, cwd: str, env: dict, timeout: float,
                head_bytes: int = SHELL_HEAD_BYTES,
                tail_bytes: int = SHELL_TAIL_BYTES) -> dict:
    """流式执行 shell 命令并**有界**收割输出；超时杀整棵进程树。

    为什么不用 `subprocess.run(capture_output=True, timeout=…)`：那是先把**全量输出灌进内存**
    再切片，一条 `yes`/`dd`/超大日志就能把容器内存打满（平台 limits.memory=8g）；
    而 timeout 只杀直接子进程，`sh -c "long & wait"` 这类孙进程会留下来继续占 pid。
    这里改为：双线程边读边丢、只留头尾，内存恒为 head_bytes+tail_bytes。
    """
    kwargs: dict = {
        "shell": True,
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        # 独立会话 = 独立进程组，超时时可以整组杀掉（含 shell 派生的所有后代）
        kwargs["start_new_session"] = True

    started = time.monotonic()
    process = subprocess.Popen(command, **kwargs)  # noqa: S602 - 故意用 shell：模型要走管道/重定向
    sinks = [_BoundedSink(head_bytes, tail_bytes), _BoundedSink(head_bytes, tail_bytes)]

    def drain(stream, sink: _BoundedSink) -> None:
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                sink.feed(chunk)
        except Exception:  # noqa: BLE001 - 进程被杀时读端会抛，正常路径
            pass
        finally:
            try:
                stream.close()
            except Exception:  # noqa: BLE001
                pass

    readers = [
        threading.Thread(target=drain, args=(stream, sinks[i]), daemon=True)
        for i, stream in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()

    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(process)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass  # 杀不动也要往下走，至少把已收到的输出还回去
    for reader in readers:
        reader.join(timeout=3)

    return {
        "exit_code": -1 if timed_out else process.returncode,
        "stdout": sinks[0].text(),
        "stderr": sinks[1].text(),
        "status": "TIMEOUT" if timed_out else "COMPLETED",
        "bytes_total": sinks[0].total + sinks[1].total,
        "duration": round(time.monotonic() - started, 3),
    }


# ---------- http_map：爬取 / 端点探测 ----------
# 灵感来自 AIxCC 决赛队伍 Theori 的经验：「窄工具 > 通用 bash，精修工具集是最耗时的部分」，
# 以及 EnIGMA 用交互式工具把 CTF agent 做到三项 SOTA。对 HTTP 靶子，最稀缺的窄工具是
# 「一次看清站点结构」：现在模型靠反复 grep href、猜路径，既烧步数又容易漏掉泄露端点。
MAP_MAX_LINKS = 40          # 站点地图里最多列多少条链接
MAP_MAX_FORMS = 8           # 最多展开多少张表单
MAP_MAX_FIELDS = 12         # 单张表单最多列多少字段
MAP_MAX_API_HINTS = 20      # JS 里挖出的接口路径上限
MAP_PROBE_LIMIT = 24        # probe 模式最多探多少个路径（每个一次请求，总墙钟受 MAP_PROBE_BUDGET 限制）
MAP_PROBE_BUDGET = 25.0     # probe 总墙钟秒数
MAP_PROBE_TIMEOUT = 6.0     # probe 单请求超时
MAP_TIMEOUT = 15            # crawl 抓页面超时
# 探测路径：AI 栈身份路径在前（决定性特征，来自 ctf-ai-infra 技能卡：按特征定型，不猜端口），
# 通用文档/工具面在后。摸到组件就该转打法，不是继续枚举端点。
# Windows 兼容层：把 head/tail/grep/wc/xxd 用 Python shim 补上。
# 模型天然写 Linux 风格命令，而本地 shell 是 cmd —— 实测「工具被拒绝」占全部调用的
# 8~12%，约一半是这类命令不存在。装上 shim 后这些命令直接可用，不再浪费一整轮。
_SHIM_DIR = Path(__file__).resolve().parents[2] / "tools" / "shims"
# 本地扩展依赖库目录（tools/fetch_pylibs.py 装；镜像里不存在，存在时只挂进 shell 子进程）
_PYLIBS_DIR = Path(__file__).resolve().parents[2] / "tools" / "pylibs"


def _shims_available() -> bool:
    return (_SHIM_DIR / "shim.py").is_file() and (_SHIM_DIR / "head.cmd").is_file()


PROBE_PATHS = (
    "/api/version", "/system_stats", "/api/tags", "/api/config", "/config",
    "/v1/models", "/console/api/setup", "/metrics",
    "/openapi.json", "/swagger.json", "/api-docs", "/docs", "/swagger", "/redoc",
    "/api", "/api/tools", "/api/v1/tools",
    "/mcp", "/sse", "/api/v1/heartbeat",
    "/health", "/debug", "/admin",
)
# 组件指纹规则（来源：ctf-ai-infra 技能卡，MIT；特征为确定性子串，非启发式）：
# (特征路径, 组件名, 正文必含子串·任一命中即定型, 下一步打法)
_FINGERPRINT_RULES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("/api/version", "Ollama", ('"version"',),
     "无鉴权推理 API：GET /api/tags 列模型；POST /api/generate 让本地模型读文件/执行描述的任务；"
     "/api/pull 可拉任意 registry 的模型（CVE-2024-7773 链），配合恶意 registry 延迟触发"),
    ("/system_stats", "ComfyUI", ("comfyui",),
     "POST /api/customnode/install 装「自定义节点」即 RCE（payload 写最小 __init__.py，"
     "import 期执行，别依赖 web 路由触发）；模型参数 ckpt_name/lora_name 试路径穿越"),
    ("/config", "Gradio", ("gradio_config", "dependencies"),
     "/upload 文件名试 ../../ 目录穿越落到可加载目录；/api/predict 或 /call/{api} 直接调后端函数；"
     "flag 常在服务端文件里，不在页面里"),
    ("/api/config", "Open WebUI", ('"features"',),
     "首个注册用户即 admin：POST /api/v1/auths/signup，然后翻 /api/v1/ 管理面、"
     "知识库文件与工作区目录"),
    ("/console/api/setup", "Dify", ('"setup"', '"status"'),
     "未完成 setup 时任何人都能创建管理员；工作流里找代码/表达式节点"
     "（Jinja SSTI：{{7*7}} 探型再升级到 __class__ 链）"),
    ("/v1/models", "vLLM/LiteLLM", ('"data"', '"object"'),
     "OpenAI 兼容推理：POST /v1/completions 带系统提示让模型读文件；/metrics 看引擎版本；"
     "路径含 litellm 时试其默认管理 key（sk-1234 / 配置里的 master_key）"),
    ("/api/v1/heartbeat", "Chroma", ("heartbeat", "nanosecond"),
     "无鉴权向量库：/api/v1/collections 列集合 → POST /api/v1/collections/{name}/get 读 points，"
     "flag 常藏在文档 payload 里"),
)
# crawl 模式的页面级强特征（只在正文出现这些专有标记时才定型，避免误报）
_PAGE_MARKERS: tuple[tuple[str, str], ...] = (
    ("ComfyUI", "__comfyui_frontend_version__"),
    ("Gradio", "gradio_config"),
)
_FINGERPRINT_VERSION_RE = re.compile(r'"version"\s*:\s*"([^"\s]{1,24})"')
# 链接排序权重：命中这些词的端点更有可能是题目入口（工单/文档/管理/接口）
_LINK_PRIORITY = ("ticket", "portal", "kb", "doc", "admin", "api", "user", "account",
                  "note", "order", "report", "flag", "debug", "internal")


class _MapParser(HTMLParser):
    """把 HTML 解析成「链接 + 表单」结构（纯 stdlib；不执行 JS，解析失败由调用方兜底）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict] = []
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._select: dict | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        amap = {key.lower(): (value or "") for key, value in attrs}
        if tag == "a":
            href = amap.get("href", "").strip()
            if href:
                self.links.append({"href": href, "text": ""})
        elif tag == "form":
            self._form = {
                "action": amap.get("action", "").strip() or "",
                "method": (amap.get("method") or "GET").upper(),
                "fields": [],
            }
            self.forms.append(self._form)
        elif tag in ("input", "textarea", "select") and self._form is not None:
            name = amap.get("name", "").strip()
            if name:
                self._form["fields"].append({
                    "name": name,
                    "type": amap.get("type") or tag,
                })
            if tag == "select":
                self._select = self._form
        elif tag == "option" and self._select is not None:
            value = amap.get("value", "").strip()
            if value and self._select["fields"]:
                last = self._select["fields"][-1]
                options = last.setdefault("options", [])
                if value not in options and len(options) < 6:
                    options.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._form = None
            self._select = None
        elif tag == "select":
            self._select = None



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
            "返回状态码、响应头（含 elapsed_ms 请求耗时，毫秒）与一个正文窗口（默认前 5000 字符；"
            "窗口后还有内容时带 truncated/next_offset，可继续翻页）。大文件（打包 JS/HTML）务必用 grep 搜关键词，"
            "或带 offset 分段读，别整段读进上下文——那会很快耗尽 token。不要用 Range 头逐段"
            "硬翻（会被自动当作 offset 处理）。枚举类题目（IDOR/改 id 看回显）用 diff=true："
            "只回与同路径上一次响应的差异行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL，例如 http://10.0.0.9:31001/login"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"], "default": "GET"},
                "body": {"type": "string", "description": "请求体（表单或 JSON 字符串）"},
                "body_b64": {"type": "string", "description": "base64 编码的二进制请求体（反序列化/上传等；与 body 互斥）"},
                "files": {
                    "type": "array",
                    "description": (
                        "可选：multipart/form-data 文件数组（上传点首选，自动拼 boundary 与 Content-Type，"
                        "不必再 write_file + curl -F 两段式）。每项 {field: 字段名, filename: 文件名, "
                        "content_b64: base64 内容} 或 {content: 文本} 或 {path: 沙箱内相对路径}，"
                        "可选 content_type（默认 application/octet-stream）。与 body/body_b64 互斥。"
                    ),
                    "items": {"type": "object"},
                },
                "form": {
                    "type": "object",
                    "description": "可选：multipart 里的普通文本字段（与 files 同用，例如 csrf token、action 名）。",
                },
                "headers": {"type": "object", "description": "附加请求头，例如 {\"Content-Type\": \"application/json\"}"},
                "grep": {
                    "type": "string",
                    "description": (
                        "可选正则。提供时先读完整响应（上限 2MB）再只返回每个匹配附近 ±80 字符的片段"
                        "（带字符偏移），最多 30 条。用于在大 JS/HTML 里搜 flag/凭据/接口/路径，"
                        "例如 \"flag\\\\{[^}]+\\\\}\" 或 \"(admin|token|secret|api)\"。"
                        "要一次搜多个关键词用 greps（数组），别对同一 URL 反复抓。"
                    ),
                },
                "greps": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "可选：正则数组（最多 6 个，与 grep 二选一或并用）。任一命中即返回，"
                        "输出按偏移升序合并、并用 grep_matched 标注每个正则命中数——"
                        "一次调用同时搜 flag/凭据/接口/路径。"
                        "例：[\"flag\\\\{\", \"(admin|token|secret|api)\", \"set-cookie\"]。"
                        "多 pattern 时每个正则最多 8 条、全局最多 30 条。"
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "从正文第 offset 个字符开始返回一个窗口（配合返回的 next_offset 翻页）。"
                        "用于分段读大文件的中段/尾段；与 grep 组合时先切出该窗口，再在窗口内搜索"
                        "（偏移仍报全文绝对位置）。"
                    ),
                },
                "save": {
                    "type": "string",
                    "description": (
                        "把响应**原始字节**存到这个沙盒路径（自动建父目录）。二进制题（ELF/pcap/zip/图片）"
                        "必须用它：正文走 utf-8 解码会把二进制弄坏。返回 saved/size/sha256/magic/head_hex，"
                        "之后用 hexpeek / elfpeek / unpack_archive 分析该文件。与 grep/offset/diff 互斥（save 优先）。"
                    ),
                },
                "fresh": {
                    "type": "boolean",
                    "description": (
                        "true 时跳过缓存读、强制重新请求（默认 false）。登录/写操作后发现之前的"
                        "403/404 或旧页面可能过期时用；缓存桩里带的 body 是上次那份。"
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
                "follow_redirect": {
                    "type": "boolean",
                    "description": (
                        "默认 true 跟随重定向。false 时不跟随，直接返回 3xx 状态码与 Location 头"
                        "（打开重定向、登录跳转链、SSRF 链要看到原始 302 时用）。"
                    ),
                },
            },
            "required": ["url"],
        },
    },
}

# http_burst 的并发护栏：一次调用顶多 24 个请求，单请求 socket 超时 20s，总墙钟 45s。
# 读满 2048 字节做「响应分组」的指纹（竞态/枚举关心的通常是错误提示 vs 成功页的前几行）。
MAX_BURST = 24
BURST_TIMEOUT = 20
BURST_TOTAL_TIMEOUT = 45
BURST_READ_BYTES = 2048
BURST_STORE_BYTES = 64 * 1024    # 流量库落盘用：比分组指纹（2048）读得更全，仍远小于 2MB 上限
MAX_BURST_GROUPS = 12             # 分组详情最多展开这么多组，其余折叠（防 JSON 被 solver 截半）

# O2'：burst 分组前对正文做归一化，消掉「响应回显 payload」造成的分组碎片化。
# 只影响分组键，输出里的 head 仍是该组首个样本的原始正文。
_BURST_VOLATILE = (
    # 浮点 epoch 秒（如 ts=1759123456.123456）：必须先整段吞掉，否则小数位不足 6 位时
    # 会残留 ".5"/".25" 之类的尾巴，让同一类结果被拆成多组（smoke_burst_group 偶发失败即此因）。
    re.compile(r"\b\d{9,}\.\d+\b"),
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),  # ISO 时间
    re.compile(r"\b\d{13}\b"),                                     # epoch 毫秒
    re.compile(r"\b\d{10}\b"),                                     # epoch 秒
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),  # UUID
    re.compile(r"\b[0-9a-fA-F]{16,}\b"),                           # ≥16 位 hex（hash/token）
    re.compile(r"\b\d{6,}\b"),                                     # 长数字（自增 ID/随机种子）
)


def _wall_advice(status_counter: dict, total: int) -> str:
    """撞墙判据（确定性版）：整批响应几乎全 401/403 或全 404 时，直接给出换法提示。

    依据 docs/audit-budget-exhausted-2026-10-31.md 的 M1/M2：历史失败题里出现过
    单端点 94 次 401（其中一次 70×POST /api/auth/otp，76 秒内盲猜 OTP）与 162 次连续 404。
    提示词约束不了「一次 burst 打 24 个值」这种形态，所以在工具输出里直接下判据。
    """
    if not total:
        return ""
    auth = int(status_counter.get(401, 0)) + int(status_counter.get(403, 0))
    if auth >= max(3, int(total * 0.8)):
        return (
            f"【撞墙判据·认证层】本批 {auth}/{total} 个响应是 401/403：墙在认证/授权层，"
            "换 value、加字典、换参数一律无效。下一步只做这四类之一：换凭据类型"
            "（cookie / JWT / Basic / API-Key / 表单会话）、换身份（注册新号 / 提权 / 换角色字段）、"
            "换端点族（/api/v1 → /api/internal → /admin）、换传参位置（header / cookie / query / body）。"
            "不要再用 burst 猜值。"
        )
    n404 = int(status_counter.get(404, 0))
    if n404 >= max(3, int(total * 0.9)):
        return (
            f"【撞墙判据·发现手段空手】本批 {n404}/{total} 个响应是 404：再换 value 也是空手。"
            "下一步换发现手段：fuzz 换档（common→small→加 ext=bak,zip）→ 拉 JS bundle 用 greps 抽 API 面 "
            "→ robots.txt / sitemap / 列表页 href / 源码注释 / 备份文件。"
        )
    return ""


def _burst_group_key(body: str, vals: list[str]) -> str:
    """O2'：把正文里的 payload 回显换成 <V>、时变片段换成 <N>，得到分组键。

    实测（audit-behavior §2）：13 组碎成 4 组的主因是错误页把 payload 原样回显，
    每个值一个组，模型得自己从 13 个垃圾组里找规律。归一化后同一「结果类型」才合组。
    """
    text = body[:BURST_READ_BYTES]
    for v in sorted((v for v in vals if v), key=len, reverse=True):  # 长的先替换，防子串吞掉长值
        text = text.replace(v, "<V>")
    for pattern in _BURST_VOLATILE:
        text = pattern.sub("<N>", text)
    return text

BURST_SPEC = {
    "type": "function",
    "function": {
        "name": "http_burst",
        "description": (
            "并发/批量发多个 HTTP 请求（竞态 or 枚举）。values 为空时同一请求并发 count 次"
            "（竞态条件、一次性状态、验证码限流等）；values 非空时每个值一个请求，{v} 原样替换"
            "url 与 body（批量枚举 IDOR/参数，需要 URL 编码请在 values 里先编好）。"
            "按响应正文分组返回：status 直方图、每组代表正文前 200 字符与成员序号、每请求耗时 ms。"
            "一次调用顶多 24 个请求；同参重跑会被账本判重，想合法重试就改 nonce。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "完整 URL；批量模式可用 {v} 占位"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"], "default": "POST"},
                "body": {"type": "string", "description": "请求体；批量模式可用 {v} 占位"},
                "headers": {"type": "object", "description": "附加请求头"},
                "count": {"type": "integer", "description": "竞态模式并发数（2..24，默认 8）；与 values 互斥"},
                "values": {"type": "array", "items": {"type": "string"}, "description": "批量模式的值列表（最多 24；超出请拆成多批调用，每批换一个 nonce）；与 count 互斥"},
                "nonce": {"type": "string", "description": "去重键：同一批想重跑就换这个值"},
            },
            "required": ["url"],
        },
    },
}

# 站点结构工具：一次调用拿到「有哪些链接 / 表单 / 字段 / 接口线索」，或探测常见 API·文档端点。
# 设计依据（AIxCC 决赛队伍 Theori）：窄工具比通用 grep 少走死路；工具集决定了 agent 的可行路径密度。
MAP_SPEC = {
    "type": "function",
    "function": {
        "name": "http_map",
        "description": (
            "把靶子结构化成「站点地图」，一次调用代替反复 grep 找链接。"
            "mode=crawl（默认）：抓一个页面，返回同源链接、表单（action/method/字段名+类型+枚举选项）、"
            "以及 JS 里出现的接口路径；用于发现「工单/详情/管理/接口」等入口。"
            "mode=probe：按常见路径清单（openapi.json/swagger/docs/api/mcp/sse/health/admin…）逐个探测，"
            "返回状态码表——AI 靶子常在这些地方暴露工具面。"
            "crawl 之后再对具体端点用 http_request（grep/offset）细读；不要把本工具当浏览器，它不执行 JS。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要成图的页面 URL（probe 模式只取它的 origin）"},
                "mode": {
                    "type": "string",
                    "enum": ["crawl", "probe"],
                    "default": "crawl",
                    "description": "crawl=解析页面结构与接口线索；probe=探常见端点",
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
            "目标若返回结构化工具调用（tool_calls/function_call），工具会列在 [tool_calls] 段里——"
            "那是判断「注入是否进入动作层」的直接证据。"
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
                "extra": {
                    "type": "object",
                    "description": (
                        "并入请求体的额外字段（json/form 模式）：很多 AI 靶子的注入面不只在 message——"
                        "lang/locale/role/context/tier 这类字段、甚至伪造 history 都在这里传，"
                        "例如 {\"lang\":\"fr\"} 或 {\"role\":\"admin\",\"history\":[...]}。"
                        "同名字段会覆盖默认值；text 模式不支持。"
                    ),
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

# 全量流量档案检索：补 http_request 只回 5000 字符窗口、diff 只能和最近一条比的缺口。
# 一条工具三种用法（search/get/diff），10 步预算下模型学得会。
TRAFFIC_SPEC = {
    "type": "function",
    "function": {
        "name": "traffic",
        "description": (
            "查本轮全量 HTTP 流量档案（http_request/http_burst/http_map 发出的每个请求与响应，含完整正文）。"
            "op=search：按 query（大小写不敏感匹配 路径/URL/请求头/响应头/正文）、status、method、source 过滤，返回最近命中列表；"
            "op=get：按 id 取某次交换的请求头+响应头+正文窗口（offset/limit 切片，响应窗 5000 字符）；"
            "op=diff：对 id=a 与 id=b 两条响应正文做行级差异（added_lines/removed_lines）。"
            "用于回答「哪些请求带过某个 cookie/参数」「某端点两种身份返回差在哪」这类跨请求问题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "op": {"type": "string", "enum": ["search", "get", "diff"], "default": "search",
                       "description": "search=过滤列表；get=按 id 看详情；diff=两条正文差异"},
                "query": {"type": "string", "description": "search：匹配关键词（路径/URL/头/正文，大小写不敏感）"},
                "status": {"type": "string", "description": "search：状态码过滤，逗号分隔，如 \"200,302,401\""},
                "method": {"type": "string", "description": "search：HTTP 方法过滤"},
                "source": {"type": "string", "description": "search：来源工具过滤 http_request/http_burst/http_map"},
                "id": {"type": "integer", "description": "get：要查看的交换 id"},
                "a": {"type": "integer", "description": "diff：左操作数交换 id"},
                "b": {"type": "integer", "description": "diff：右操作数交换 id"},
                "offset": {"type": "integer", "default": 0, "description": "get：正文窗口起始偏移（字符）"},
                "limit": {"type": "integer", "default": 10, "description": "search：最多返回条数；get：正文窗口长度"},
            },
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


def _dns_unreachable_advice(url: str, err: str) -> str:
    """DNS 解析失败 = 本机没有平台内网隧道（VPN 题）。给模型的确定性结论，别换工具反复重试。

    实测教训（P0 校准 2026-11-01）：XBOW-001 是 *.internal 内网实例，agent 依次试了
    http_request → http_map → curl → python socket，烧掉 0.44M token / 296s 才确认 DNS 不可达。
    这里直接把结论写进错误文本，等价于 O-D 的「撞墙判据」——工具层判据比提示词可靠。
    """
    low = (err or "").lower()
    dns_fail = any(k in low for k in (
        "getaddrinfo failed", "name or service not known", "temporary failure in name resolution",
        "nodename nor servname", "errno 11001", "no address associated with hostname",
    ))
    if not dns_fail:
        return ""
    host = urlparse(url).hostname or ""
    hint = "，域名形如 *.internal（平台内网实例）" if host.endswith(".internal") else ""
    return ("\n（诊断）DNS 解析失败 = 本机没有平台内网隧道" + hint +
            "。这类实例用 http_request / http_map / fuzz / curl 都会同样失败——换工具重试没有意义："
            "直接 report 交付「VPN/内网不可达」卡点，把预算留给其他题。 别用 shell 的 getent/nslookup/curl/ipconfig/wg 去复核——shell 里没有 netcheck 命令，这些在本机同样无效；要判定就调 netcheck 工具一次，然后收口。")


class LocalTools:
    def __init__(self, store, challenge_id: str, *, allow_shell: bool = False, verbose: bool = False, cache: dict | None = None, jar: dict | None = None, chats: dict | None = None, allow_chat: bool = True, traffic_enabled: bool = True):
        self.store = store
        self.challenge_id = challenge_id
        self.allow_shell = allow_shell
        self.allow_chat = allow_chat
        self.verbose = verbose
        # 流量库：best-effort 初始化，失败即 None（disabled），绝不影响解题。
        # 测试里的 _Store() 假 store 既无 run_dir 也非路径——Path() 会抛 TypeError，这里一并吞掉。
        self._traffic = None
        if traffic_enabled:
            try:
                run_dir = getattr(self.store, "run_dir", None)
                if run_dir is None:
                    run_dir = Path(self.store)
                inst = traffic_resolve(run_dir, challenge_id)
                self._traffic = inst if inst.enabled else None
            except Exception:
                self._traffic = None
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
        # ffuf 可执行文件：None=运行时用 shutil.which 探测；测试可注入 argv 前缀（假 ffuf）
        self._ffuf_bin = None
        self._sqlmap_py = None  # 测试可指向假 sqlmap；None = 走 CTFY_SQLMAP / /opt/sqlmap/sqlmap.py

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
                if isinstance(inner, (dict, list, str)):
                    self._walk(inner, depth + 1)
        elif isinstance(value, list):
            for inner in value:
                self._walk(inner, depth + 1)
        elif isinstance(value, str):
            self._allow_url(value)

    BARE_ENDPOINT = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*):(\d{1,5})$")
    BARE_HOST = re.compile(
        r"^(?:\d{1,3}(?:\.\d{1,3}){3}|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})$")

    def _allow_url(self, text: str) -> None:
        """从字符串里提取可访问端点：URL、裸 host:port、裸主机名都要认。

        平台常见形状（DASCTF）：access_url = "xxx.tcp-ctf2.dasctf.com:9999"，
        没有 scheme，urlparse 会当成路径而拿不到 hostname，必须单独识别。
        注意只认「像端点」的字符串，否则题面描述里的普通单词会被误登记成主机。
        """
        text = str(text or "").strip()
        if not text:
            return
        match = self.BARE_ENDPOINT.match(text)
        if match:
            self.allow_instance(match.group(1), int(match.group(2)))
            return
        if "://" in text:
            try:
                parsed = urlparse(text)
                if parsed.hostname:
                    self.allow_instance(parsed.hostname, parsed.port)
            except Exception:
                pass
            return
        if self.BARE_HOST.match(text):
            self.allow_instance(text, None)

    @staticmethod
    def _host_root(host: str) -> str:
        """去掉最左边一段，得到「根域」：a.node4.ctfy.run → node4.ctfy.run。"""
        parts = (host or "").split(".")
        return ".".join(parts[1:]) if len(parts) > 2 else (host or "")

    def _same_platform_host(self, host: str) -> bool:
        """同一平台下发的主机（换节点/换子域名）放行。

        平台会把实例从一个子域名换到同域的另一个（实测 sa5bkcyyu6xqmg6kt.node4.ctfy.run
        被当成「非本题实例」拒了 17 次）。判据：
          * 目标是 IP 时**只允许精确匹配**（下面会先做精确检查，走到这里说明不等）
          * 同主机不减免（端口必须精确登记）
          * 根域必须 ≥3 段（node4.ctfy.run 可以，example.com 不行）
          * 目标的根域 = 某个已登记主机/端点的根域
        这样只放行「同一平台节点下的兄弟实例」，第三方域名仍会被拒。
        """
        if re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", host or ""):
            # 关键：IP 不能走「去首段」逻辑 —— 10.0.0.5 的 root 会变成 0.0.5，
            # 从而把 99.0.0.5 这类完全不同的 IP 也放行（白名单绕过）。
            return False
        # [接手 2026-11-10] 同主机不享受「兄弟域名」减免：否则 tls.example.com 登记 :443 后，
        # http://tls.example.com（默认 80）也会被放行 —— 端口白名单形同虚设（smoke case_hardening 实测）。
        # 减免只应作用于**不同**子域名（平台换节点那类），同一主机的端口必须精确登记。
        if host in self.allowed_hosts:
            return False
        for ep in self.allowed_endpoints:
            if ep.rsplit(":", 1)[0] == host:
                return False
        root = self._host_root(host)
        if not root or root == host or "." not in root:
            return False
        # [接手 2026-11-10] 根域必须 ≥3 段：否则登记 tls.example.com 就等于把整个 example.com
        # 放行（evil.example.com 也会过），仍是白名单绕过。平台节点是 xxx.node4.ctfy.run 这种形状，
        # 根域 node4.ctfy.run 有 3 段，减免照常生效。
        if root.count(".") < 2:
            return False
        roots = {self._host_root(h) for h in self.allowed_hosts}
        for ep in self.allowed_endpoints:
            roots.add(self._host_root(ep.rsplit(":", 1)[0]))
        roots.discard("")
        return root in roots

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
        if self._same_platform_host(host):
            return
        raise ToolError(
            f"合规拦截：{host} 不是平台为本题下发的实例地址。"
            f"已登记主机：{', '.join(sorted(self.allowed_hosts)) or '(无)'}；"
            f"已登记端点：{', '.join(sorted(self.allowed_endpoints)) or '(无)'}。"
            "只允许攻击平台实例，禁止访问第三方站点。"
        )

    def _check_endpoint(self, host: str, port: int | None) -> None:
        """TCP/连通性工具的目标校验：与 http_request 共用同一套实例白名单。"""
        host = self._norm_host(host)
        if not host:
            raise ToolError("host 为空")
        # A2（实测 CTFARENA-005 有 4 次把 {v} 模板写进 host 位被拦）：模板不是主机名。
        if "{" in host or "}" in host or "%s" in host:
            raise ToolError(
                f"host 里不能放模板：{host!r}。host 必须是 start_instance 返回的真实主机名；"
                "批量枚举的 {v} 占位符只能放在 http_burst 的 url/body 里。"
            )
        if not self.allowed_hosts and not self.allowed_endpoints:
            raise ToolError(
                "还没有登记任何可访问实例。先用 start_instance 启动题目实例，再用它返回的 host:port。"
            )
        if host in self.allowed_hosts:
            return
        if port is not None and f"{host}:{int(port)}" in self.allowed_endpoints:
            return
        if self._same_platform_host(host):
            return
        raise ToolError(
            f"合规拦截：{host}{':' + str(port) if port else ''} 不是平台为本题下发的实例端点。"
            f"已登记主机：{', '.join(sorted(self.allowed_hosts)) or '(无)'}；"
            f"已登记端点：{', '.join(sorted(self.allowed_endpoints)) or '(无)'}。"
            "只允许攻击平台实例，禁止访问第三方站点。"
            "（host 必须是 start_instance 返回的主机名；批量枚举的 {v} 只能放在 url/body）"
        )

    # ---------- 规格 ----------
    def specs(self) -> list[dict]:
        specs = [HTTP_SPEC, MAP_SPEC, NOTE_SPEC, BURST_SPEC]
        if self._traffic is not None:
            specs.append(TRAFFIC_SPEC)
        if self.allow_chat:
            specs.append(CHAT_SPEC)
        if self.allow_shell:
            specs.append(SHELL_SPEC)
        specs.append(FUZZ_SPEC)  # ffuf 封装；按角色可见性由 crew._role_specs 的 local 名单过滤
        specs.append(AUTHDIFF_SPEC)  # P3：越权/鉴权对照探针
        specs.append(JSRECON_SPEC)   # P3：JS 静态侦察
        specs.append(SQLI_SPEC)      # P3：sqlmap 窄封装
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

    # write_file 的参数别名（模型常用这些叫法）
    _ARG_ALIASES = {
        "path": ("file", "filename", "filepath", "file_path", "fname"),
        "content": ("text", "data", "body", "value", "code"),
    }

    @classmethod
    def _apply_arg_aliases(cls, name: str, arguments: dict, sig) -> dict:
        """把模型常用的别名参数映射成工具真实参数名（目前只对 write_file）。

        实测 CTFY 历史里有 27 次「write_file() missing 2 required positional arguments」，
        都是模型把 path/content 写成了 file/text/data —— 映射一下即省掉一整轮重试。
        """
        if name != "write_file":
            return arguments
        for param, aliases in cls._ARG_ALIASES.items():
            if param in arguments or param not in sig.parameters:
                continue
            for alias in aliases:
                if alias in arguments:
                    arguments[param] = arguments.pop(alias)
                    break
        return arguments

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
        if name in _DOMAIN_NAMES:
            return self._call_domain_tool(name, arguments)
        fn = getattr(self, name)
        sig = inspect.signature(fn)
        arguments = self._apply_arg_aliases(name, arguments, sig)
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

    def write_file(self, path: str, content: str, append: bool = False) -> str:
        """B1：写文件通道（路径先做沙盒锚定，越界直接拒）。"""
        return _call_ext("write_file", {"path": self._resolve_path(path), "content": content, "append": append})

    def jwt_forge(self, alg: str = "HS256", secret: str = "", token: str = "",
                  claims: dict | None = None, header: dict | None = None,
                  remove: list[str] | None = None) -> str:
        return _call_ext("jwt_forge", {
            "alg": alg, "secret": secret, "token": token,
            "claims": claims, "header": header, "remove": remove,
        })

    # ---------- 领域扩展工具（pwn / crypto / reverse 共用地基） ----------

    def _call_domain_tool(self, name: str, arguments: dict) -> str:
        """tcp / tcp_batch / elfpeek / netcheck：先做沙盒路径锚定与实例端点合规校验。"""
        if arguments.get("path") is not None:
            arguments["path"] = self._resolve_path(str(arguments["path"]))
        host = arguments.get("host")
        if host is not None:
            port = arguments.get("port")
            numeric = str(port if port is not None else "").strip().isdigit()
            self._check_endpoint(str(host), int(port) if numeric else None)
        return _call_domain(name, arguments)

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
            # O1b：超容时优先逐非 200 条目——403/404 探测刷屏不能把 200 的真实攻击面挤出去；
            # 只剩 200 时才按插入序逐最旧的（FIFO）。
            oldest_error = next((k for k, e in self.cache.items() if (e.get("status") or 200) != 200), None)
            victim = oldest_error if oldest_error is not None else next(iter(self.cache))
            del self.cache[victim]
        total = sum(len(e.get("payload") or "") for e in self.cache.values())
        while total > CACHE_MAX_BYTES and self.cache:
            k = next(iter(self.cache))
            total -= len(self.cache[k].get("payload") or "")
            del self.cache[k]

    @staticmethod
    def _render_cache_stub(entry: dict) -> str:
        payload = entry.get("payload") or ""
        raw_total = entry.get("content_length")
        status = entry.get("status")
        if raw_total is None:
            total = f">={MAX_GREP_BYTES}" if entry.get("capped") else len(payload)
        else:
            total = int(raw_total)
        if status in (403, 404):
            note = (
                f"该 URL 本轮已读过并返回 {status}（缓存命中，未重复请求）。"
                "若刚登录或刚做过写操作，这个结果可能已过期——加 fresh=1 强制重取；"
                "要看片段请带 offset 或 grep。"
            )
        else:
            note = "该 URL 本轮已读过（缓存命中，未重复返回全文）。要看任意片段请带 offset 或 grep。"
        return json.dumps(
            {
                "status": status,
                "cached": True,
                "total_bytes": total,
                "body": payload[:500],
                "note": note,
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

    def _capture_redirect(self, req, code, headers) -> None:
        """跳转响应的 Set-Cookie 也入 jar（登录/注册常 302 种会话，不捞就丢会话）。"""
        netloc = urlparse(getattr(req, "full_url", "") or "").netloc
        if netloc:
            self._capture_cookies(netloc, headers.items())

    def _capture_redirect_and_record(self, req, code, headers) -> None:
        """http_request 专用：捞 cookie 之外，把 3xx 中间跳也落流量库（审计 A7）。"""
        self._capture_redirect(req, code, headers)
        self._traffic_record(
            source="http_request",
            method=req.get_method() if hasattr(req, "get_method") else "GET",
            url=getattr(req, "full_url", "") or "",
            status=code,
            resp_headers={str(k): str(v) for k, v in (headers.items() if headers else [])},
            resp_body="",
        )

    def _decorate_redirect(self, req) -> None:
        """跳转后的新请求补上 Cookie（urllib 复制原请求头时 jar 里还没有会话）。"""
        netloc = urlparse(getattr(req, "full_url", "") or "").netloc
        cookie = self._cookie_header(netloc) if netloc else None
        if cookie and not req.has_header("Cookie"):
            req.add_header("Cookie", cookie)

    def _cookie_header(self, netloc: str) -> str | None:
        jar = self._jar.get(netloc)
        return "; ".join(f"{k}={v}" for k, v in jar.items()) if jar else None

    def http_request(self, url: str, method: str = "GET", body: str = "", headers: dict | None = None, grep: str = "", offset: int = 0, limit: int = 0, diff: bool = False, follow_redirect: bool = True, body_b64: str = "", fresh: bool = False, greps: list[str] | None = None, files: list | None = None, form: dict | None = None, save: str = "") -> str:
        self._check_target(url)
        netloc = urlparse(url).netloc
        method = (method or "GET").upper()
        diff = as_bool(diff)
        follow = as_bool(follow_redirect)
        fresh = as_bool(fresh)
        # O1a：grep（字符串）与 greps（数组）合并成一个列表交给渲染层；
        # 只有单个 pattern 时渲染层走旧路径，输出形状与旧版逐字节一致。
        grep_arg: list[str] = []
        if grep:
            grep_arg.extend([grep] if isinstance(grep, str) else list(grep))
        if greps:
            grep_arg.extend([greps] if isinstance(greps, str) else list(greps))
        if len(grep_arg) == 1:
            grep_render: str | list[str] = grep_arg[0]
        else:
            grep_render = grep_arg
        if body and body_b64:
            raise ToolError("body 与 body_b64 互斥，只填一个")
        if files or form:
            # O-F：multipart 上传（与 body/body_b64 互斥，Content-Type 由本方法生成）
            if body or body_b64:
                raise ToolError("files/form 与 body/body_b64 互斥，只填一组")
            data, _multipart_ctype = self._build_multipart(files or [], form)
        else:
            _multipart_ctype = ""
            if body_b64:
                try:
                    data = base64.b64decode(body_b64, validate=True)
                except Exception as exc:
                    raise ToolError(f"body_b64 不是合法 base64：{exc}") from None
            else:
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
        # O-F：multipart 自动带 Content-Type（模型显式传了就不覆盖）
        if _multipart_ctype and not any(str(k).lower() == "content-type" for k in forwarded):
            forwarded["Content-Type"] = _multipart_ctype
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
            # 鉴权/自定义头也进 key（审计修复）：X-Role:admin 与无头请求若共用缓存，
            # 头绕过/鉴权题会拿到假阳/假阴的旧响应；UA/Accept 等无关头不进来，避免缓存命中率塌掉。
            hdr_sig = tuple(sorted(
                (str(k).lower(), str(v)) for k, v in forwarded.items()
                if str(k).lower() == "authorization" or str(k).lower().startswith("x-")
            ))
            cache_key = (netloc, path_q, effective_cookie, hdr_sig)
            hit = self.cache.get(cache_key)
            # diff 要「这次真实响应」当右侧：跳过缓存读（cache_key 仍留着事后落盘）。
            # follow_redirect=false 同理：要看到原始 3xx+Location，不能拿缓存的落地页顶替。
            # fresh=true（O1b）：登录/写操作后强制重取，跳过缓存读。
            # save：要原始字节落盘，缓存桩只有 utf-8 正文（二进制已损坏），必须回源。
            if hit is not None and not diff and follow and not fresh and not save:
                _hit_hdrs = hit.get("headers") or {}
                self._traffic_record(
                    source="http_request", method=method, url=url, status=hit.get("status"),
                    req_headers=forwarded, req_body=body or "", resp_headers=_hit_hdrs,
                    resp_body=hit.get("payload") or "",
                    mime=_hit_hdrs.get("content-type") or "",
                    truncated=(1 if len(hit.get("payload") or "") >= MAX_GREP_BYTES else 0),
                    cache_hit=1,
                )
                if not grep_render and not offset:
                    # 完全重复读：返回桩而不是再次塞全文进上下文（省 token 的真正来源）
                    return self._render_cache_stub(hit)
                # 带 offset/grep：直接从缓存切片/筛，省掉一次网络往返
                return self._render_http(
                    hit.get("status") or 200,
                    hit.get("headers") or {},
                    hit.get("payload") or "",
                    grep_render,
                    offset,
                    hit.get("content_length"),
                    limit,
                )

        # 恒读满 2MB：小响应读到 EOF 即止（无额外成本），大响应保留中/尾供 offset 切片。
        # capture：跳转响应上的 Set-Cookie 也要入 jar（登录/注册常 302 种会话）。
        if follow:
            opener = build_opener(GuardedRedirect(self._check_target, capture=self._capture_redirect_and_record, decorate=self._decorate_redirect))
        else:
            opener = build_opener(NoRedirect())
        status = 0
        interesting: dict = {}
        content_length = None
        payload = ""
        raw = b""  # save 参数要原始字节：二进制经 utf-8 replace 就废了
        t0 = time.perf_counter()
        try:
            with opener.open(request, timeout=20) as response:
                raw = read_limited(response, MAX_GREP_BYTES)
                payload = raw.decode("utf-8", "replace")
                status = response.status
                interesting = {
                    key: value for key, value in response.headers.items()
                    if key.lower() in _INTERESTING_HEADERS
                }
                self._capture_cookies(netloc, response.headers.items())
                content_length = response.headers.get("Content-Length")
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = read_limited(exc, MAX_GREP_BYTES)
                payload = raw.decode("utf-8", "replace")
            except Exception:
                payload = ""
            if exc.headers:
                interesting = {
                    key: value for key, value in exc.headers.items()
                    if key.lower() in _INTERESTING_HEADERS
                }
                self._capture_cookies(netloc, exc.headers.items())
                content_length = exc.headers.get("Content-Length")
        except urllib.error.URLError as exc:
            self._traffic_record(
                source="http_request", method=method, url=url, req_headers=forwarded,
                req_body=body, error=f"{type(exc).__name__}: {exc.reason}",
            )
            raise ToolError(f"请求失败 {url}: {exc.reason}{_dns_unreachable_advice(url, str(exc.reason))}") from None
        except OSError as exc:
            # 连接被重置/中断/读超时等（http.client 会直接抛 ConnectionAbortedError 之类，
            # 不走 URLError），统一转成 ToolError 回给模型，别让整轮崩掉。
            self._traffic_record(
                source="http_request", method=method, url=url, req_headers=forwarded,
                req_body=body, error=f"{type(exc).__name__}: {exc}",
            )
            raise ToolError(f"请求失败 {url}: {exc}{_dns_unreachable_advice(url, str(exc))}") from None
        # ToolError 从 GuardedRedirect 的 _check_target 抛出来，原样上抛
        elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)

        self._traffic_record(
            source="http_request", method=method, url=url, status=status,
            req_headers=forwarded,
            req_body=body if body else (data.decode("utf-8", "replace") if data else ""),
            resp_headers=interesting, resp_body=payload,
            mime=interesting.get("content-type") or "",
            ms=elapsed_ms, truncated=(1 if len(payload) >= MAX_GREP_BYTES else 0),
        )

        # 基线必须在上面的 _cache_put 之前取：否则刚抓到的这一份会变成「最近一条」，自己和自己比。
        baseline = self._diff_baseline(netloc, urlparse(url).path) if diff else None

        if method == "GET" and status == 200:
            cache_entry = {
                "status": status,
                "headers": interesting,
                "payload": payload,
                "content_length": content_length,
                "capped": len(payload) >= MAX_GREP_BYTES,
            }
            self._cache_put(cache_key, cache_entry)
        elif method == "GET" and status in (403, 404) and len(payload) <= CACHE_ERROR_MAX_BODY:
            # O1b：403/404 也缓存——重复探测不存在路径/被拒端点时直接命中桩，不再耗网络。
            # 体积限 64KB 且单列条数上限（见 _cache_put），防刷接口把 200 条目挤出去。
            self._cache_put(
                cache_key,
                {
                    "status": status,
                    "headers": interesting,
                    "payload": payload,
                    "content_length": content_length,
                    "capped": False,
                },
            )
        if save:
            return self._save_body(status, interesting, raw, save, content_length, elapsed_ms)
        if diff:
            return self._render_diff(status, interesting, payload, baseline, grep_render, offset, content_length, limit, elapsed_ms)
        return self._render_http(status, interesting, payload, grep_render, offset, content_length, limit, elapsed_ms)

    # 常见二进制魔数：落盘后直接告诉模型「这文件是什么」，省一轮 hexpeek
    MAGIC_TABLE: tuple[tuple[bytes, str], ...] = (
        (b"\x7fELF", "ELF 可执行"),
        (b"MZ", "PE 可执行"),
        (b"PK\x03\x04", "ZIP/JAR/DOCX"),
        (b"\x1f\x8b", "gzip"),
        (b"BZh", "bzip2"),
        (b"\xfd7zXZ", "xz"),
        (b"\x89PNG", "PNG 图片"),
        (b"\xff\xd8\xff", "JPEG 图片"),
        (b"GIF8", "GIF 图片"),
        (b"%PDF", "PDF"),
        (b"\xd4\xc3\xb2\xa1", "PCAP（小端）"),
        (b"\xa1\xb2\xc3\xd4", "PCAP（大端）"),
        (b"\x0a\x0d\x0d\x0a", "PCAPNG"),
        (b"SQLite format 3", "SQLite"),
        (b"\x28\xb5\x2f\xfd", "zstd"),
        (b"\x37\x7a\xbc\xaf", "7z"),
        (b"Rar!", "RAR"),
    )

    @classmethod
    def _magic_kind(cls, data: bytes) -> str:
        """按魔数猜类型：猜不出给空串（不瞎猜）。"""
        for magic, label in cls.MAGIC_TABLE:
            if data.startswith(magic):
                return label
        head = data[:512]
        try:
            text = head.decode("utf-8")
        except UnicodeDecodeError:
            return "未知二进制"
        if text and all(ch in "\t\n\r" or ord(ch) >= 32 for ch in text):
            return "文本"
        return "未知二进制"

    def _save_body(self, status: int, headers: dict, raw: bytes, save: str,
                   content_length: str | None, elapsed_ms: float | None = None) -> str:
        """把响应原始字节写进沙盒文件，只回摘要（不在上下文里塞二进制）。"""
        target = Path(save)
        if target.exists() and target.is_dir():
            raise ToolError(f"save 目标已存在且是目录：{save}")
        if len(raw) > MAX_GREP_BYTES:
            raw = raw[:MAX_GREP_BYTES]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        out = {
            "status": status,
            "headers": headers,
            "saved": str(target),
            "total_bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "magic": self._magic_kind(raw),
            "head_hex": raw[:48].hex(),
            "note": (
                "已按原始字节落盘（不经 utf-8 解码，二进制安全）。下一步用 hexpeek/elfpeek/unpack_archive "
                "读这个文件，不要把二进制塞回上下文；文本型附件也可以直接用 shell 的 python 分析。"
            ),
        }
        if content_length is not None:
            out["declared_bytes"] = int(content_length)
        if elapsed_ms is not None:
            out["elapsed_ms"] = elapsed_ms
        return json.dumps(out, ensure_ascii=False)

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
            entry = self.cache.get(key) or {}
            # O1b：403/404 也进缓存了，但 diff 的基线只认 200 的真实页面，
            # 否则「404 变 403」这类差异会被当成页面内容变化刷满差异行。
            if (entry.get("status") or 200) != 200:
                continue
            payload = entry.get("payload")
            if isinstance(payload, str):
                return payload
        return None

    @staticmethod
    def _render_diff(status: int, headers: dict, payload: str, baseline: str | None,
                     grep: str, offset: int, content_length: str | None, limit: int,
                     elapsed_ms: float | None = None) -> str:
        """diff=true 的输出：有基线只回行级差异；没基线退回普通窗口（不假装比过）。"""
        if baseline is None:
            out = json.loads(LocalTools._render_http(status, headers, payload, grep, offset, content_length, limit, elapsed_ms))
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
        if elapsed_ms is not None:
            out["elapsed_ms"] = elapsed_ms
        if content_length is not None:
            out["total_bytes"] = int(content_length)
        if not shown:
            out["note"] = "与基线逐行相同（这次响应没有变化）。"
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def _render_http(status: int, headers: dict, payload: str, grep: str | list[str], offset: int = 0, content_length: str | None = None, limit: int = 0, elapsed_ms: float | None = None) -> str:
        offset = max(0, int(offset or 0))
        wsize = HTTP_WINDOW
        if limit:
            # 模型主动要更小的窗口时尊重它；夹在 [200, 5000] 防 limit=1 这类退化请求
            wsize = max(200, min(int(limit), HTTP_WINDOW))
        total = len(payload)
        capped = total >= MAX_GREP_BYTES  # 读到本地 2MB 上限（未必是文件末尾）

        patterns, dropped = _normalize_grep(grep)
        if patterns:
            # 按「匹配片段 ±80 字符」抽取，而非按行——压缩过的 JS 常是单行，按行会把整包返回。
            # 边扫边截到 MAX_GREP_MATCHES：绝不全量物化（宽正则打 2MB 会 OOM，审计 B11）。
            # O1a：offset 与 grep 组合 = 先切窗口再搜，报的仍是全文绝对偏移。
            window = payload[offset : offset + wsize] if offset > 0 else payload
            base = offset if offset > 0 else 0
            if len(patterns) == 1:
                # 单 pattern 走旧路径：输出形状逐字节保持不变（快照兼容）
                single = patterns[0]
                if len(single) > MAX_GREP_PATTERN_LEN:
                    raise ToolError(f"grep 正则过长（{len(single)}>{MAX_GREP_PATTERN_LEN} 字符），请精简")
                try:
                    pattern = re.compile(single)
                except re.error as exc:
                    raise ToolError(f"grep 正则无效: {exc}") from None
                shown: list[str] = []
                truncated = False
                for m in pattern.finditer(window):
                    if len(shown) >= MAX_GREP_MATCHES:
                        truncated = True
                        break
                    start, end = base + m.start(), base + m.end()
                    shown.append(f"[{start}] {payload[max(0, start - 80): end + 80]}")
                out = {
                    "status": status,
                    "headers": headers,
                    "grep": single,
                    "matched": len(shown),
                    "truncated": truncated,
                    "body": "\n".join(shown),
                }
                if elapsed_ms is not None:
                    out["elapsed_ms"] = elapsed_ms
                if content_length is not None:
                    out["total_bytes"] = int(content_length)
                elif capped:
                    out["total_bytes"] = f">={MAX_GREP_BYTES}"
                return json.dumps(out, ensure_ascii=False)

            # 多 pattern（O1a）：逐正则搜、按偏移升序合并、同偏移保留先出现者；
            # 每 pattern ≤MAX_GREP_PER_PATTERN、全局 ≤MAX_GREP_MATCHES，超限报告被截断的正则。
            errors: list[dict] = []
            compiled: list[tuple[str, re.Pattern]] = []
            for pat in patterns:
                if len(pat) > MAX_GREP_PATTERN_LEN:
                    errors.append({"pattern": pat, "error": f"正则过长（>{MAX_GREP_PATTERN_LEN} 字符）"})
                    continue
                try:
                    compiled.append((pat, re.compile(pat)))
                except re.error as exc:
                    errors.append({"pattern": pat, "error": f"bad regex: {exc}"})
            hits: list[tuple[int, int, str]] = []
            per_count: dict[str, int] = {}
            per_trunc: dict[str, int] = {}
            seen_start: set[int] = set()
            for pat, regex in compiled:
                kept = 0
                found = 0
                for m in regex.finditer(window):
                    found += 1
                    if kept >= MAX_GREP_PER_PATTERN:
                        per_trunc[pat] = found
                        break
                    start, end = base + m.start(), base + m.end()
                    if start in seen_start:  # 同偏移重叠：首个 pattern 保留，其余不重复计
                        continue
                    seen_start.add(start)
                    hits.append((start, end, pat))
                    kept += 1
                per_count[pat] = kept
            hits.sort(key=lambda item: item[0])
            shown = []
            truncated = False
            for start, end, _pat in hits:
                if len(shown) >= MAX_GREP_MATCHES:
                    truncated = True
                    break
                shown.append(f"[{start}] {payload[max(0, start - 80): end + 80]}")
            out = {
                "status": status,
                "headers": headers,
                "grep": patterns,
                "matched": len(shown),
                "truncated": truncated or bool(per_trunc),
                "grep_matched": [{"pattern": pat, "count": per_count.get(pat, 0)} for pat, _ in compiled],
                "body": "\n".join(shown),
            }
            if dropped:
                out["grep_dropped"] = dropped  # 超过 6 个被丢弃的个数
            if per_trunc:
                out["grep_truncated"] = per_trunc  # 每个被截断正则的至少命中数
            if errors:
                out["grep_errors"] = errors  # 非法/过长正则只影响自身
            if elapsed_ms is not None:
                out["elapsed_ms"] = elapsed_ms
            if content_length is not None:
                out["total_bytes"] = int(content_length)
            elif capped:
                out["total_bytes"] = f">={MAX_GREP_BYTES}"
            return json.dumps(out, ensure_ascii=False)

        window = payload[offset : offset + wsize]
        # 元数据放在 body 之前：即使结果被 solver 截断，翻页锚点也不会丢
        out = {"status": status, "headers": headers}
        if elapsed_ms is not None:
            out["elapsed_ms"] = elapsed_ms
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

    # ---------- 流量档案（检索） ----------

    def _traffic_record(self, **kwargs) -> None:
        """best-effort 落一条交换；流量库关闭或异常都静默，绝不影响解题。"""
        if self._traffic is None:
            return
        try:
            self._traffic.record(**kwargs)
        except Exception:
            pass

    def traffic(self, op: str = "search", query: str = "", status: str = "", method: str = "",
                source: str = "", id: int = 0, a: int = 0, b: int = 0, offset: int = 0,
                limit: int = 10) -> str:
        """查流量档案（search/get/diff 三态，见 TRAFFIC_SPEC）。"""
        if self._traffic is None:
            return json.dumps({"error": "流量库未启用（traffic_enabled=false）"}, ensure_ascii=False)

        def _i(v, name: str, default: int) -> int:
            # 非法整数转成 ToolError 而非 ValueError（审计 A10：否则白烧一步「执行异常」）
            if v in (None, "", False):
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                raise ToolError(f"traffic 参数 {name} 必须是整数，收到 {v!r}") from None

        op = (op or "search").strip().lower()
        if op == "get":
            out = self._traffic.get(_i(id, "id", 0), offset=_i(offset, "offset", 0),
                                    limit=_i(limit, "limit", 0))
        elif op == "diff":
            out = self._traffic.diff(_i(a, "a", 0), _i(b, "b", 0))
        else:
            out = self._traffic.search(query=query, status=status, method=method, source=source,
                                       limit=_i(limit, "limit", 10))
        return json.dumps(out, ensure_ascii=False)

    # ---------- 目录/参数发现（ffuf 封装） ----------
    # 实现在 tools_webscan.py（纯 stdlib、可注入假 ffuf 单测）；这里只做契约适配：
    # 注入目标白名单校验与流量落库回调，命令行开关一律由模块内部硬编码。

    def _build_multipart(self, files: list, form: dict | None) -> tuple[bytes, str]:
        """O-F：拼 multipart/form-data 请求体，返回 (bytes, Content-Type)。

        files 每项支持三种内容来源：content_b64 / content（文本）/ path（沙箱内相对路径）。
        path 会做越界校验——上传物必须已经在沙箱里，不允许读沙箱外文件。
        """
        boundary = "----ctfyagent" + uuid.uuid4().hex
        parts: list[bytes] = []
        for key, value in (form or {}).items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8")
            )
        base = self._sandbox_dir().resolve()
        for i, spec in enumerate(files or []):
            if not isinstance(spec, dict):
                raise ToolError(f"files[{i}] 必须是对象：{{field, filename, content_b64|content|path}}")
            field = str(spec.get("field") or "file")
            filename = str(spec.get("filename") or f"upload{i}.bin")
            if spec.get("content_b64"):
                try:
                    content = base64.b64decode(str(spec["content_b64"]), validate=True)
                except Exception as exc:
                    raise ToolError(f"files[{i}].content_b64 不是合法 base64：{exc}") from None
            elif spec.get("path"):
                p = (base / str(spec["path"])).resolve()
                if base not in p.parents and p != base:
                    raise ToolError(f"files[{i}].path 越界（只允许沙箱内文件）：{spec['path']}")
                if not p.is_file():
                    raise ToolError(f"files[{i}].path 不是文件：{spec['path']}")
                content = p.read_bytes()
            elif spec.get("content") is not None:
                content = str(spec["content"]).encode("utf-8")
            else:
                raise ToolError(f"files[{i}] 需要 content_b64 / content / path 之一")
            ctype = str(spec.get("content_type") or "application/octet-stream")
            head = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"'
                f"\r\nContent-Type: {ctype}\r\n\r\n"
            )
            parts.append(head.encode("utf-8") + content + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _fuzz_record(self, url: str, status) -> None:
        """命中落流量库（source=fuzz）；失败不影响扫描结果。"""
        self._traffic_record(source="fuzz", method="GET", url=url, status=status)

    def fuzz(self, mode: str = "dir", base: str = "", url: str = "", wordlist: str = "common",
             words: list | None = None, ext: str = "",
             match_codes: str = FUZZ_MATCH_CODES_DEFAULT,
             filter_size: int = 0, filter_codes: str = "", filter_regex: str = "",
             max_results: int = 40, timeout_s: int = 45) -> str:
        """用内置字典做路径/参数发现（见 tools_webscan.FUZZ_SPEC）。"""
        return _webscan_fuzz(
            mode=mode, base=base, url=url, wordlist=wordlist, words=words, ext=ext,
            match_codes=match_codes, filter_size=filter_size,
            filter_codes=filter_codes, filter_regex=filter_regex,
            max_results=max_results, timeout_s=timeout_s,
            check_target=self._check_target,
            record=self._fuzz_record,
            work_dir=self._sandbox_dir(),
            ffuf_bin=self._ffuf_bin,
        )

    # ---------- 越权对照 / JS 侦察（P3） ----------
    # 实现在 tools_websec.py（纯 stdlib、可单测）；这里只做契约适配与抓取/白名单注入。

    def authdiff(self, url: str = "", method: str = "GET", body: str = "",
                 headers: dict | None = None, variants: str = "anon,bypass") -> str:
        """越权/鉴权对照探针（见 tools_websec.AUTHDIFF_SPEC）。"""
        netloc = urlparse(url).netloc if url else ""
        return _websec_authdiff(
            url=url, method=method, body=body, headers=headers, variants=variants,
            cookie_present=bool(netloc and self._cookie_header(netloc)),
            check_target=self._check_target, fetch=self._sec_fetch,
        )

    def jsrecon(self, url: str = "") -> str:
        """JS 静态侦察（见 tools_websec.JSRECON_SPEC）。"""
        def _fetch(u, **kwargs):  # noqa: ANN001 - 回调适配，签名与 run_jsrecon 一致
            return self._sec_fetch(u, source="jsrecon", **kwargs)

        return _websec_jsrecon(url=url, check_target=self._check_target, fetch=_fetch)

    def _sqli_record(self, **kwargs) -> None:
        self._traffic_record(source="sqli", **kwargs)

    def sqli(self, url: str = "", data: str = "", param: str = "", technique: str = "BEUSTQ",
             level: int = 1, risk: int = 1, timeout_s: int = 60) -> str:
        """SQL 注入自动化（见 tools_websql.SQLI_SPEC）。"""
        return _websql_sqli(
            url=url, data=data, param=param, technique=technique, level=level, risk=risk,
            timeout_s=timeout_s, check_target=self._check_target,
            record=self._sqli_record, work_dir=self._sandbox_dir(), sqlmap_py=self._sqlmap_py,
        )

    # ---------- 并发/批量请求（竞态 & 枚举） ----------

    def http_burst(self, url: str, method: str = "POST", body: str = "", headers: dict | None = None,
                   count: int = 8, values: list[str] | None = None, nonce: str = "") -> str:
        """并发/批量发多个请求（纯 stdlib 线程，无三方库）。

        - values 为空：竞态模式，同一请求并发 count 次；
        - values 非空：批量模式，每个值一个请求，{v} 原样替换 url 与 body。

        线程先各自 connect() 再一起过 Barrier 释放，尽量让请求「同时」打在服务端，
        以触发竞态窗口；有一路建连失败就 abort 屏障、整轮作废并明说（避免 BrokenBarrier
        掩掉真实错误）。cookie 在主线程先快照、join 后再统一收 Set-Cookie，规避并发写丢键。
        """
        self._check_target(url)
        netloc = urlparse(url).netloc
        method = (method or "POST").upper()
        if method == "HEAD":
            raise ToolError("http_burst 不支持 HEAD（无响应体，分组无意义）")
        if method != "GET":
            # 与 http_request 对齐：非 GET 会改服务端状态，清同 host 旧缓存防脏读
            self._invalidate_host(netloc)

        if values is not None and not isinstance(values, list):
            raise ToolError("values 必须是数组（list），例如 [\"alice\", \"bob\"]")
        vals = [str(v) for v in (values or [])]
        if vals:
            if len(vals) > MAX_BURST:
                raise ToolError(f"values 最多 {MAX_BURST} 个，收到 {len(vals)}")
            targets = []
            for v in vals:
                u = url.replace("{v}", v)
                b = body.replace("{v}", v)
                self._check_target(u)   # 每个替换后的 URL 也要过白名单
                targets.append((u, b))
            mode = "batch"
        else:
            try:
                n = max(2, min(int(count or MAX_BURST), MAX_BURST))
            except (TypeError, ValueError):
                raise ToolError(f"count 必须是 2..{MAX_BURST} 的整数，收到 {count!r}") from None
            targets = [(url, body)] * n
            mode = "race"

        # cookie 在主线程先快照：并发写 jar 会丢键（后写覆盖先写）。
        # 自动 cookie 只发给「与模板同 netloc」的目标：batch 的 {v} 若落在主机部分，
        # 替换后的另一白名单 host 不该收到本 host 的会话 Cookie（防跨主机泄漏）。
        hdrs = {str(k): str(v) for k, v in (headers or {}).items()}
        auto_cookie = self._cookie_header(netloc)
        if auto_cookie and any(k.lower() == "cookie" for k in hdrs):
            auto_cookie = None   # 模型显式传了 Cookie，尊重它，别叠加 jar
        if not any(k.lower() == "accept-encoding" for k in hdrs):
            hdrs["Accept-Encoding"] = "identity"
        if not any(k.lower() == "user-agent" for k in hdrs):
            hdrs["User-Agent"] = "ctfy-agent/0.2"

        results: list[dict | None] = [None] * len(targets)
        barrier = threading.Barrier(len(targets))

        def worker(i: int, u: str, b: str) -> None:
            conn = None
            try:
                parsed = urlparse(u)
                if parsed.scheme == "https":
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                    conn = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=BURST_TIMEOUT, context=ctx)
                else:
                    conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=BURST_TIMEOUT)
                conn.connect()
            except Exception as exc:  # noqa: BLE001
                results[i] = {"i": i, "error": f"建连失败 {type(exc).__name__}: {exc}"}
                try:
                    barrier.abort()   # 有同伴没连上，让其余线程也别傻等屏障
                except Exception:  # noqa: BLE001
                    pass
                return
            try:
                barrier.wait(timeout=BURST_TIMEOUT)
            except threading.BrokenBarrierError:
                results[i] = {"i": i, "error": "本轮作废：有同伴建连失败（重试或调低 count）"}
                conn.close()
                return
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            req_hdrs = dict(hdrs)
            if auto_cookie and parsed.netloc == netloc:
                req_hdrs["Cookie"] = auto_cookie
            t0 = time.perf_counter()
            try:
                conn.request(method, path, body=b.encode("utf-8") if b else None, headers=req_hdrs)
                resp = conn.getresponse()
                raw = resp.read(BURST_STORE_BYTES)
                results[i] = {
                    "i": i,
                    "status": resp.status,
                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                    "body": raw.decode("utf-8", "replace"),
                    "set_cookie": resp.headers.get_all("Set-Cookie") or [],
                    "mime": resp.getheader("Content-Type") or "",
                }
            except Exception as exc:  # noqa: BLE001
                results[i] = {"i": i, "error": f"{type(exc).__name__}: {exc}"}
            finally:
                conn.close()

        threads = [(i, threading.Thread(target=worker, args=(i, u, b), daemon=True)) for i, (u, b) in enumerate(targets)]
        for _i, t in threads:
            t.start()
        deadline = time.time() + BURST_TOTAL_TIMEOUT
        for _i, t in threads:
            t.join(max(0.1, deadline - time.time()))
        for idx in range(len(results)):
            if results[idx] is None:
                results[idx] = {"i": idx, "error": "未在时限内返回"}
        for r in results:
            for sc in (r or {}).get("set_cookie") or []:
                self._capture_cookies(netloc, [("Set-Cookie", sc)])

        # 全部结果落流量库（主线程写，规避 SQLite 并发写；失败也记，死路证据）。
        # 补上实际发送的 Cookie（worker 里加了但没回传，审计 A8），与线上请求头一致。
        rec_hdrs = dict(hdrs)
        if auto_cookie:
            rec_hdrs["Cookie"] = auto_cookie
        for idx, (u, b) in enumerate(targets):
            r = results[idx] or {}
            if "error" in r:
                self._traffic_record(source="http_burst", method=method, url=u,
                                     req_headers=rec_hdrs, req_body=b, error=r["error"])
            else:
                sc = r.get("set_cookie") or []
                body_text = r.get("body") or ""
                self._traffic_record(
                    source="http_burst", method=method, url=u, status=r.get("status"),
                    req_headers=rec_hdrs, req_body=b,
                    resp_headers={"Set-Cookie": "; ".join(sc)} if sc else {},
                    resp_body=body_text, mime=r.get("mime") or "", ms=r.get("ms"),
                    truncated=(1 if len(body_text) >= BURST_STORE_BYTES else 0),
                )

        # 按响应正文分组（正文即指纹：错误提示 vs 成功页的分界通常在前几行）
        # O2'：batch 模式先归一化（payload 回显 → <V>，时变片段 → <N>）再分组，
        # 避免「同一结果类型被 payload 回显拆成十几组」；race 模式无 values，走原路径。
        ok = failed = 0
        status_counter: dict[int, int] = {}
        times: list[float] = []
        groups: dict[object, dict] = {}
        raw_bodies: set[str] = set()
        for r in results:
            r = r or {}
            if "error" in r:
                failed += 1
                continue
            ok += 1
            status_counter[r["status"]] = status_counter.get(r["status"], 0) + 1
            times.append(r["ms"])
            raw_key = r["body"][:BURST_READ_BYTES]
            raw_bodies.add(raw_key)
            if mode == "batch":
                # 状态码进键：同正文不同状态（如 200 空页 vs 403 空页）不是同一种结果
                key = (r["status"], _burst_group_key(r["body"], vals))
                g = groups.setdefault(
                    key,
                    {"count": 0, "i": [], "status": r["status"], "head": r["body"][:120], "_raw": set()},
                )
                g["_raw"].add(raw_key)
            else:
                g = groups.setdefault(raw_key, {"count": 0, "i": [], "status": r["status"], "head": r["body"][:120]})
            g["count"] += 1
            g["i"].append(r["i"])
        for g in groups.values():
            raw = g.pop("_raw", None)
            if raw is not None:
                # 合并前该组由几个原始正文组成；>1 说明归一化确实合掉了碎片
                g["raw_count"] = len(raw)

        groups_sorted = sorted(groups.values(), key=lambda g: -g["count"])
        shown = groups_sorted[:MAX_BURST_GROUPS]
        if len(groups_sorted) > MAX_BURST_GROUPS:
            # 折叠长尾：只展开头部分组，其余按 status 计数并汇总成员，避免 JSON 被 solver 截半
            tail = groups_sorted[MAX_BURST_GROUPS:]
            collapsed_status: dict = {}
            collapsed_i: list[int] = []
            for g in tail:
                collapsed_status[g["status"]] = collapsed_status.get(g["status"], 0) + g["count"]
                collapsed_i.extend(g["i"])
            shown.append({
                "count": sum(g["count"] for g in tail),
                "status": collapsed_status,
                "i": collapsed_i,
                "head": "",
                "collapsed": len(tail),
            })
        summary: dict = {
            "mode": mode,
            "requested": len(targets),
            "ok": ok,
            "failed": failed,
            "status": status_counter,
            "distinct_bodies": len(groups),
            "groups": shown,
        }
        if mode == "batch" and len(raw_bodies) != len(groups):
            # 归一化合掉了碎片时给出原始正文组数，让模型知道「多少个 payload 只产生几种结果」
            summary["raw_distinct_bodies"] = len(raw_bodies)
        # 撞墙判据：整批几乎全 401/403 或全 404 时直接给换法（提示词管不住「一次打 24 个值」）
        advice = _wall_advice(status_counter, ok)
        if advice:
            summary["advice"] = advice
        if times:
            summary["ms"] = {"min": min(times), "max": max(times), "spread": round(max(times) - min(times), 1)}
        if mode == "batch":
            for g in shown:
                g["v"] = [vals[j] for j in g["i"]]
        if failed:
            summary["errors"] = [r for r in results if r and "error" in r]
        return json.dumps(summary, ensure_ascii=False)

    # ---------- http_map：站点结构 / 端点探测（窄工具，替代反复 grep 与猜路径） ----------

    def _map_fetch(self, url: str, timeout: float = MAP_TIMEOUT, *, method: str = "GET",
                   body: str = "", extra_headers: dict | None = None, use_jar: bool = True,
                   source: str = "http_map") -> tuple[int, str, str]:
        """抓一页（带 cookie jar、重定向 Set-Cookie 捕获），返回 (status, content_type, text)。

        P3 起兼作 authdiff/jsrecon 的通用抓取器：method/body/extra_headers/use_jar 可覆盖，
        source 决定流量库里的来源标识；默认行为与 http_map 完全一致。
        """
        self._check_target(url)
        netloc = urlparse(url).netloc
        headers = {"Accept-Encoding": "identity", "User-Agent": "ctfy-agent/0.2"}
        for key, value in (extra_headers or {}).items():
            headers[str(key)] = str(value)
        if use_jar:
            cookie = self._cookie_header(netloc)
            if cookie and not any(str(k).lower() == "cookie" for k in headers):
                headers["Cookie"] = cookie
        data = body.encode("utf-8") if body else None
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        # 审查 A1：use_jar=False（authdiff 的匿名变体）必须与 jar 完全隔离——跳转后不补
        # Cookie（decorate 置空）、响应的 Set-Cookie 也不回写 jar（capture/抓取置空）。
        # 否则「去掉 Cookie 重放」在带跳转的端点上会被重新认证，匿名可读判定失真。
        opener = build_opener(
            GuardedRedirect(
                self._check_target,
                capture=self._capture_redirect if use_jar else None,
                decorate=self._decorate_redirect if use_jar else None,
            )
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = read_limited(response, MAX_GREP_BYTES)
                if use_jar:
                    self._capture_cookies(netloc, response.headers.items())
                status = response.status
                ctype = response.headers.get("Content-Type") or ""
                text = raw.decode("utf-8", "replace")
                self._traffic_record(source=source, method=method, url=url, status=status,
                                     req_headers=headers, req_body=body,
                                     resp_headers={"Content-Type": ctype} if ctype else {},
                                     resp_body=text, mime=ctype)
                return (status, ctype, text)
        except urllib.error.HTTPError as exc:
            try:
                raw = read_limited(exc, MAX_GREP_BYTES)
            except Exception:  # noqa: BLE001
                raw = b""
            if use_jar and exc.headers:
                self._capture_cookies(netloc, exc.headers.items())
            ctype = (exc.headers.get("Content-Type") if exc.headers else "") or ""
            text = raw.decode("utf-8", "replace")
            self._traffic_record(source=source, method=method, url=url, status=exc.code,
                                 req_headers=headers, req_body=body,
                                 resp_headers={"Content-Type": ctype} if ctype else {},
                                 resp_body=text, mime=ctype)
            return exc.code, ctype, text
        except urllib.error.URLError as exc:
            self._traffic_record(source=source, method=method, url=url, req_headers=headers,
                                 error=f"{type(exc).__name__}: {exc.reason}")
            raise ToolError(f"请求失败 {url}: {exc.reason}{_dns_unreachable_advice(url, str(exc.reason))}") from None
        except OSError as exc:
            self._traffic_record(source=source, method=method, url=url, req_headers=headers,
                                 error=f"{type(exc).__name__}: {exc}")
            raise ToolError(f"请求失败 {url}: {exc}{_dns_unreachable_advice(url, str(exc))}") from None

    def _sec_fetch(self, url: str, *, method: str = "GET", body: str = "",
                   extra_headers: dict | None = None, use_jar: bool = True,
                   source: str = "authdiff") -> tuple[int, str, str]:
        """authdiff / jsrecon 的抓取回调：复用 _map_fetch 的白名单、重定向护栏与落库。"""
        return self._map_fetch(url, method=method, body=body, extra_headers=extra_headers,
                               use_jar=use_jar, source=source)

    @staticmethod
    def _map_path(target: str, base_url: str) -> str:
        """把链接/action 归一成同源 path（带 query）；跨域/伪协议返回空串。"""
        target = (target or "").strip()
        if not target or target.startswith(("#", "javascript:", "mailto:", "tel:", "data:")):
            return ""
        absolute = urljoin(base_url, target)
        parts = urlparse(absolute)
        if parts.netloc and parts.netloc != urlparse(base_url).netloc:
            return ""
        path = parts.path or "/"
        return path + (f"?{parts.query}" if parts.query else "")

    @staticmethod
    def _fingerprint_component(path: str, text: str) -> tuple[str, str, str] | None:
        """按特征路径 + 正文确定性子串定型 AI 栈组件；命中返回 (组件, 依据, 下一步)。"""
        lowered = (text or "")[:4000].lower()
        for rule_path, name, markers, hint in _FINGERPRINT_RULES:
            if path != rule_path:
                continue
            marker = next((m for m in markers if m.lower() in lowered), None)
            if marker is None:
                continue
            version = ""
            match = _FINGERPRINT_VERSION_RE.search(text or "")
            if match:
                version = f"，版本 {match.group(1)}"
            return name, f"{path} 命中特征 {marker}{version}", hint
        return None

    def http_map(self, url: str, mode: str = "crawl") -> str:
        """站点结构成图（crawl）或常见端点探测（probe，带 AI 栈组件指纹）。"""
        self._check_target(url)
        mode = (mode or "crawl").strip().lower()
        if mode not in ("crawl", "probe"):
            raise ToolError(f"mode 只能是 crawl 或 probe，收到 {mode!r}")

        if mode == "probe":
            parts = urlparse(url)
            origin = f"{parts.scheme}://{parts.netloc}"
            hits: list[dict] = []
            components: list[str] = []
            missed = 0
            started = time.perf_counter()
            for path in PROBE_PATHS[:MAP_PROBE_LIMIT]:
                if time.perf_counter() - started > MAP_PROBE_BUDGET:
                    break
                if components and len(hits) >= 8:
                    # 已定型组件：攒够 8 条即停。没定型（如 catch-all 兜底）不设早停，
                    # 否则身份路径的假 200 会提前填满上限、探不到 openapi/docs/mcp 工具面。
                    break
                try:
                    status, ctype, text = self._map_fetch(origin + path, timeout=MAP_PROBE_TIMEOUT)
                except ToolError:
                    continue
                if status in (200, 201, 202, 204, 301, 302, 401, 403, 405):
                    short = ctype.split(";")[0].strip()
                    entry: dict = {
                        "path": path,
                        "status": status,
                        "type": short[:40],
                        "bytes": len(text),
                        "hint": ("命中，直接读" if status in (200, 201) else
                                 "存在但需鉴权/方法不对" if status in (401, 403, 405) else "跳转"),
                    }
                    fp = self._fingerprint_component(path, text)
                    if fp:
                        entry["component"] = fp[0]
                        entry["why"] = fp[1][:110]
                        entry["next"] = fp[2]
                        if fp[0] not in components:
                            components.append(fp[0])
                    hits.append(entry)
                else:
                    missed += 1
            payload: dict = {
                "mode": "probe",
                "origin": origin,
                "hits": hits,
                "not_found": missed,
            }
            if components:
                payload["components"] = components
                payload["next"] = (f"组件定型：{'、'.join(components)}——直接按命中 hit 里的 next 打法走"
                                   "（框架实现漏洞：插件安装/上传穿越/URL 拉取/模板渲染）。"
                                   "flag 通常在服务端文件里，不在模型回答里；别跑通用扫描器。")
            elif hits:
                payload["next"] = ("对命中的 path 用 http_request 读原文；AI 靶子的工具面常在 openapi/docs/mcp 里。"
                                   "没定型组件时重点看 安装/上传/URL 拉取 三类入口。")
            else:
                payload["next"] = "常见端点都不存在：回到 crawl 从页面链接/表单里找入口，别继续猜路径。"
            return json.dumps(payload, ensure_ascii=False)

        status, ctype, text = self._map_fetch(url)
        parser = _MapParser()
        try:
            parser.feed(text)
        except Exception:  # noqa: BLE001  解析失败不该让整步崩，退回纯文本线索
            pass

        links: list[str] = []
        for item in parser.links:
            path = self._map_path(item.get("href", ""), url)
            if path and path not in links:
                links.append(path)
        # 把更可能是入口的链接排到前面（工单/文档/管理/接口/用户）
        links.sort(key=lambda p: (0 if any(word in p.lower() for word in _LINK_PRIORITY) else 1, len(p)))

        forms: list[dict] = []
        seen_forms: set[tuple] = set()
        for form in parser.forms[: MAP_MAX_FORMS * 2]:
            action = self._map_path(form.get("action", ""), url) or urlparse(url).path or "/"
            fields = []
            for field in form.get("fields", [])[:MAP_MAX_FIELDS]:
                entry = {"name": field["name"], "type": (field.get("type") or "text")[:16]}
                if field.get("options"):
                    entry["options"] = field["options"][:6]
                fields.append(entry)
            key = (action, form.get("method", "GET"), tuple(f["name"] for f in fields))
            if key in seen_forms:
                continue
            seen_forms.add(key)
            forms.append({"action": action, "method": form.get("method", "GET"), "fields": fields})
            if len(forms) >= MAP_MAX_FORMS:
                break

        # JS 里出现的接口路径：SFT 题里工具面常直接写在前端代码里
        api_hints: list[str] = []
        for match in re.finditer(r"""(?:fetch|url|endpoint|api|action)\s*[:=(]\s*["']([^"']{2,80})["']""",
                                 text, re.I):
            path = self._map_path(match.group(1), url)
            if path and any(word in path.lower() for word in ("api", "v1", "v2", "graphql", "mcp", "tool", "chat")):
                if path not in api_hints:
                    api_hints.append(path)
            if len(api_hints) >= MAP_MAX_API_HINTS:
                break

        payload: dict = {
            "status": status,
            "mode": "crawl",
            "page": urlparse(url).path or "/",
            "content_type": ctype.split(";")[0].strip()[:40],
            "links": links[:MAP_MAX_LINKS],
            "forms": forms,
            "api_hints": api_hints,
            "counts": {"links": len(links), "forms": len(forms), "api_hints": len(api_hints)},
        }
        # 页面级强特征：首页专有标记 → 组件定型（与 probe 同一知识源，避免只探首页漏掉 SPA 壳）
        lowered = text[:4000].lower()
        for name, marker in _PAGE_MARKERS:
            if marker in lowered:
                payload["component"] = name
                payload["next"] = (f"页面即 {name} 前端：转 http_map(mode='probe') 拿版本与 API 面，"
                                   "再按该组件的结构漏洞打（插件安装/上传穿越/URL 拉取）。")
                break
        if "next" not in payload:
            payload["next"] = ("对关键链接逐个 http_request 细读；AI 靶子优先看 kb/docs 与工单/order 详情。"
                               if links or forms else
                               "页面没有可解析结构（可能是纯 JS 渲染）：改用 http_map(mode='probe') 探端点。")
        return json.dumps(payload, ensure_ascii=False)

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

    CHAT_TOOL_NAME_KEYS = ("name", "tool", "tool_name", "function", "action", "recipient")
    CHAT_TOOL_ARG_KEYS = ("arguments", "args", "input", "parameters", "parameter", "payload", "query")
    CHAT_TOOL_MAX = 6

    @staticmethod
    def _extract_tool_calls(value, depth: int = 0, out: list[str] | None = None) -> list[str]:
        """从靶子 JSON 里抽出「它真的调了哪些工具」：name + arguments 成对渲染。

        很多 agent 靶子把工具调用放在 tool_calls / function_call / actions 里，
        回复正文却只有「好的，我来查一下」——只看正文会误判注入没生效。
        找的是「同时有名字字段和参数字段」的 dict（兼容 OpenAI 的 function{name,arguments}）。
        """
        if out is None:
            out = []
        if depth > 5 or len(out) >= LocalTools.CHAT_TOOL_MAX:
            return out
        if isinstance(value, dict):
            name = ""
            for key in LocalTools.CHAT_TOOL_NAME_KEYS:
                cand = value.get(key)
                if isinstance(cand, str) and cand.strip():
                    name = cand.strip()
                    break
                if isinstance(cand, dict):
                    inner = cand.get("name")
                    if isinstance(inner, str) and inner.strip():
                        name = inner.strip()
                        break
            args = None
            for key in LocalTools.CHAT_TOOL_ARG_KEYS:
                if key in value:
                    args = value[key]
                    break
            if name and args is not None:
                if isinstance(args, str):
                    rendered = args
                else:
                    try:
                        rendered = json.dumps(args, ensure_ascii=False)
                    except (TypeError, ValueError):
                        rendered = str(args)
                item = f"{name}({rendered[:180]})"
                if item not in out:
                    out.append(item)
            for inner in value.values():
                LocalTools._extract_tool_calls(inner, depth + 1, out)
                if len(out) >= LocalTools.CHAT_TOOL_MAX:
                    break
        elif isinstance(value, list):
            for inner in value:
                LocalTools._extract_tool_calls(inner, depth + 1, out)
                if len(out) >= LocalTools.CHAT_TOOL_MAX:
                    break
        return out

    @classmethod
    def _extract_tool_calls_from_sse(cls, payload: str) -> list[str]:
        """SSE 流里的工具调用：逐条 data: 解 JSON 后复用上面的抽取（去重保序）。"""
        out: list[str] = []
        for line in (payload or "").splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                obj = json.loads(chunk)
            except ValueError:
                continue
            for item in cls._extract_tool_calls(obj):
                if item not in out:
                    out.append(item)
            if len(out) >= cls.CHAT_TOOL_MAX:
                break
        return out[: cls.CHAT_TOOL_MAX]

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
             history: int = 0, mode: str = "json", extra: dict | None = None) -> str:
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
        extra_fields = dict(extra or {}) if isinstance(extra, dict) else {}
        if mode == "text":
            if extra_fields:
                raise ToolError("text 模式不支持 extra（纯文本没有字段位）；改用 mode=json 或 mode=form")
            data = text.encode("utf-8")
            content_type = "text/plain; charset=utf-8"
        elif mode == "form":
            fields = {"message": text, **extra_fields}
            data = urlencode(fields).encode("utf-8")
            content_type = "application/x-www-form-urlencoded"
        else:
            mode = "json"
            body = {"message": text, "session": session, "history": pairs[-12:]}
            body.update(extra_fields)  # extra 最后覆盖：伪造 history / 加 lang、role 都走这里
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
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

        opener = build_opener(GuardedRedirect(self._check_target, capture=self._capture_redirect, decorate=self._decorate_redirect))
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
        obj = None
        streaming = "event-stream" in ctype or payload.lstrip().startswith("data:")
        if streaming:
            reply = self._parse_sse(payload)
        else:
            try:
                obj = json.loads(payload)
            except ValueError:
                obj = None
        if not reply and obj is not None:
            reply = self._extract_reply(obj) or ""
        if not reply:
            reply = payload.strip()
        reply = reply[:CHAT_MAX_REPLY]
        calls = self._extract_tool_calls(obj) if obj is not None else (
            self._extract_tool_calls_from_sse(payload) if streaming else [])

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
        if extra_fields:
            head += " | extra=" + ",".join(sorted(str(k) for k in extra_fields))[:120]
        if status >= 400:
            head += "（端点或参数可能不对：先 GET 首页用 http_request + grep 确认聊天路径）"
        lines = [head, "[reply]", reply if reply else "(空回复)"]
        if calls:
            lines.append("[tool_calls] 目标**真的调用了工具**（注入已进入动作层，不只是措辞层）：")
            lines.extend(f"  - {item[:220]}" for item in calls)
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
        if calls:
            lines.append("[note] 它已在执行工具：下一步逼它把该工具的**返回值原文**贴出来，"
                         "或改参数/换 ID 继续调用；只问「能不能/有没有权限」会停在措辞层")
        else:
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
    def _rewrite_quotes(cls, command: str) -> str:
        """单引号 → 双引号（仅在不产生歧义时）。

        cmd 不认单引号，但盲目替换会破坏含 % ! ^ 的内容（cmd 会做变量展开/转义），
        所以只在「整体没有双引号、单引号成对、内容不含这些特殊字符」时才改写，
        其余情况保持原样交给守卫拒绝（并提示改用 write_file + python 脚本）。
        """
        if '"' in command or command.count("'") % 2 != 0:
            return command
        chunks = re.findall(r"'([^']*)'", command)
        if not chunks:
            return command
        if any(("!" in c) or ("%" in c) or ("^" in c) for c in chunks):
            return command
        return re.sub(r"'([^']*)'", r'"\1"', command)

    @staticmethod
    def _outside_dquotes(command: str) -> str:
        """返回「双引号之外」的字符串（双引号内的内容抹成空格）。

        cmd 里双引号内的单引号/<< 都是字面量：
          python -c "print('x')"   完全合法
          python -c "print(1 << 2)" 完全合法
        原实现直接 `"'" in command` / `"<<" in command` 判断，把这两种常见写法误拒了。
        双引号数量为奇数时无法可靠判断，保守起见整串返回（维持原来的严格行为）。
        """
        if command.count('"') % 2 != 0:
            return command
        out: list[str] = []
        inside = False
        for ch in command:
            if ch == '"':
                inside = not inside
                out.append(" ")
                continue
            out.append(" " if inside else ch)
        return "".join(out)

    @classmethod
    def _windows_shell_guard(cls, command: str) -> str | None:
        # 已被 _windows_rewrite 换成等价物的命令不再列入（ls/cat/pwd/which/cp/mv），
        # 只保留「有叫法差异但无语义等价物」或「改写不安全」的命令。
        unix_only = ("grep", "head", "tail", "wc", "sed", "awk", "xxd", "sh", "bash")
        if _shims_available():
            # 前五个由 tools/shims 提供 Windows 实现，不再拦截；sed/awk 太复杂不做 shim
            unix_only = ("sed", "awk", "sh", "bash")
        cmds = cls._windows_command_words(command)
        outside = cls._outside_dquotes(command)
        notes: list[str] = []
        missing = sorted({c for c in cmds if c in unix_only})
        if missing:
            notes.append(f"Windows shell 无 {'/'.join(missing)}；文本处理改用 http_request 的 grep/offset")
        if "python3" in cmds:
            notes.append("Windows 上解释器叫 `python`（不是 python3）")
        if "<<" in outside:
            notes.append("Windows cmd 不支持 heredoc(<<)，改 write_file 写脚本再跑，或 python -c")
        if "'" in outside:
            notes.append(
                "Windows cmd 不认单引号：把外层单引号换成双引号即可；"
                "若代码里本来就要用双引号（无法安全自动改写），改用 write_file 写 .py 再 `python x.py` 最稳")
        return "；".join(notes) if notes else None

    # B2：Windows cmd 里「叫法不同」但不是「能力缺失」的命令 → 安全等价物。
    # 依据 docs/audit-agent-2026-10-30.md §4：v0 批 265 次 shell 调用被守卫直接拒收，
    # 每次拒收都逼模型再花一整轮（≈12k tok）重写命令；直接改写是零成本的。
    _WINDOWS_ALIASES = {
        "python3": "python", "python3.11": "python", "python3.12": "python", "python3.13": "python",
        "ls": "dir", "cat": "type", "pwd": "cd", "which": "where",
        "cp": "copy", "mv": "move", "clear": "cls",
    }

    @staticmethod
    def _rewrite_rm(rest: str) -> str:
        """把 rm 的常用写法改写成 cmd 等价物。

        [接手 2026-11-10] 原来 `rm` 被一律改成 `del`，于是 `rm -rf out/` → `del -rf out/`：
        cmd 的 del 不认 `-rf`，命令必然失败，模型还得再花一轮。按语义分流：
          rm -rf/-r 目录 → rmdir /s /q；rm -f 文件 → del /f /q；其余 → del /q。
        目标为空时不猜，交回守卫/报错。
        """
        args = (rest or "").strip()
        targets = args
        flags = ""
        m = re.match(r"((?:-[A-Za-z]+|--[a-z-]+)(?:\s+(?:-[A-Za-z]+|--[a-z-]+))*)\s*(.*)$", args)
        if m:
            flags, targets = m.group(1), m.group(2)
        targets = targets.strip()
        if not targets:
            return "rm"  # 没有目标：别瞎猜，让守卫去提示
        recursive = bool(re.search(r"(?:^|\s)(?:-[A-Za-z]*[rR][A-Za-z]*|--recursive)(?:\s|$)", flags))
        force = bool(re.search(r"(?:^|\s)(?:-{1,2}[A-Za-z]*f[A-Za-z]*|--force)(?:\s|$)", flags))
        if recursive:
            return f"rmdir /s /q {targets}"
        return ("del /f /q " if force else "del /q ") + targets

    @staticmethod
    def _rewrite_ls(rest: str) -> str:
        """ls → dir，并把 -l/-a/-R 这类 Unix 开关换成 dir 的开关（`dir -la` 是无效开关）。"""
        args = (rest or "").strip()
        m = re.match(r"((?:-[A-Za-z]+)(?:\s+-[A-Za-z]+)*)\s*(.*)$", args)
        flags, target = (m.group(1), m.group(2)) if m else ("", args)
        target = target.strip()
        if re.search(r"[rR]", flags):
            cmd = "dir /s /b"
        elif flags:
            cmd = "dir /a"
        else:
            cmd = "dir"
        return f"{cmd} {target}".strip()

    @classmethod
    def _windows_rewrite(cls, command: str) -> str:
        """把命令词位置上的别名改写成 cmd 等价物；参数/正文一律不动。"""
        parts = re.split(r"(\|\||&&|\||;|\r|\n)", command or "")
        changed = False
        out: list[str] = []
        for idx, part in enumerate(parts):
            if idx % 2 == 1:  # 分隔符原样保留
                out.append(part)
                continue
            match = re.match(r"(\s*)(\"[^\"]*\"|'[^']*'|\S+)([\s\S]*)$", part)
            if not match:
                out.append(part)
                continue
            lead, word, rest = match.group(1), match.group(2), match.group(3)
            name = word.strip("\"'").lower()
            if name == "rm":  # 递归/强制语义不同，单独走 _rewrite_rm
                out.append(f"{lead}{cls._rewrite_rm(rest)}")
                changed = True
                continue
            if name == "ls":  # -l/-a/-R 在 dir 上写法不同
                out.append(f"{lead}{cls._rewrite_ls(rest)}")
                changed = True
                continue
            alias = cls._WINDOWS_ALIASES.get(name)
            if alias:
                out.append(f"{lead}{alias}{rest}")
                changed = True
            else:
                out.append(part)
        return "".join(out) if changed else command

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
            rewritten = self._windows_rewrite(command)   # B2：先做安全别名改写（python3→python、ls→dir…）
            rewritten = self._rewrite_quotes(rewritten)  # 单引号 → 双引号（安全时）
            if rewritten != command:
                command = rewritten
            hint = self._windows_shell_guard(command)
            if hint:
                raise ToolError(hint)

        cwd = self._sandbox_dir()
        cwd.mkdir(parents=True, exist_ok=True)

        # 让 shell 里的 `python` 指向当前解释器：宿主 PATH 上的 python 可能只是
        # 应用商店占位符（实测会报 "Python was not found"），模型写脚本时就会踩空。
        env = dict(os.environ)
        interpreter_dir = str(Path(sys.executable).parent)
        search = interpreter_dir
        if _shims_available():
            search = str(_SHIM_DIR) + os.pathsep + search
        env["PATH"] = search + os.pathsep + env.get("PATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("CTFY_PYTHON", sys.executable)
        # [接手 2026-11-10] 本地扩展依赖库（tools/pylibs，由 tools/fetch_pylibs.py 装）：
        # 镜像只装了 capstone，没有 pycryptodome/gmpy2/sympy/pwntools/z3，于是本地跑
        # crypto/pwn 时模型写的脚本 import 全挂（runs/plat 实测 CRYPTO 0/5、PWN 0/4 都卡在这）。
        # 只对 shell 子进程生效：不动本进程、不改镜像；容器里没有该目录时行为与旧版一致。
        _pylibs = _PYLIBS_DIR
        if _pylibs.is_dir():
            _existing = env.get("PYTHONPATH") or ""
            env["PYTHONPATH"] = str(_pylibs) + (os.pathsep + _existing if _existing else "")
            env.setdefault("CTFY_PYLIBS", str(_pylibs))
            # win_amd64 的轮子把依赖 DLL 放在 <pkg>.libs/ 里（gmpy2 依赖 libgmp/libmpfr…）。
            # Windows 的 DLL 搜索路径包含 PATH，所以这些目录要挂进去，否则 import gmpy2 会
            # 报 "DLL load failed while importing gmpy2"（实测）。纯 Python 包不受影响。
            _dll_dirs = [str(p) for p in _pylibs.glob("*.libs") if p.is_dir()]
            if _dll_dirs:
                env["PATH"] = os.pathsep.join(_dll_dirs) + os.pathsep + env.get("PATH", "")

        try:
            # P0-2：有界收割 + 超时杀整棵进程树（旧写法是 capture_output 全量进内存后切片）
            result = run_bounded(command, cwd=str(cwd), env=env, timeout=limit)
        except OSError as exc:
            raise ToolError(f"命令无法启动（{type(exc).__name__}: {exc}）: {command}") from None
        if result["status"] == "TIMEOUT":
            tail = (result["stdout"] or result["stderr"] or "")[-1500:]
            raise ToolError(
                f"命令超时（{limit}s，已杀掉整棵进程树）: {command}"
                + (f"\n[超时前最后的输出]\n{tail}" if tail else "")
            )
        output = {
            "exit_code": result["exit_code"],
            "stdout": (result["stdout"] or "")[-MAX_BODY:],
            "stderr": (result["stderr"] or "")[-2000:],
        }
        if result.get("bytes_total", 0) > MAX_BODY + 2000:
            output["note"] = f"输出共 {result['bytes_total']} 字节，这里只保留首尾；需要全量请重定向到文件再分段读。"
        # [接手 2026-11-10] 缺依赖时给出确定性事实，别让模型反复试 import：
        # 镜像里没有 pycryptodome/gmpy2/sympy/pwntools，本地可以把它们外挂在 tools/pylibs。
        _err = output.get("stderr") or ""
        if "ModuleNotFoundError" in _err or "No module named" in _err:
            if _PYLIBS_DIR.is_dir():
                _tops = sorted(p.name for p in _PYLIBS_DIR.iterdir()
                               if p.is_dir() and not p.name.endswith((".dist-info", ".data", "__pycache__")))
                output["note"] = (
                    "本机已外挂扩展库目录（PYTHONPATH 已包含 tools/pylibs）："
                    + ("、".join(_tops[:14]) if _tops else "（空）")
                    + "。缺的模块若不在其中，就别再试 import——用纯 stdlib 重写那一小段。"
                )
            else:
                output["note"] = (
                    "本机没有扩展库目录（容器镜像同样只有 stdlib）：缺的第三方库不要再试，"
                    "用纯 stdlib 实现；本地想装可跑 `python tools/fetch_pylibs.py`（需联网）。"
                )
        return json.dumps(output, ensure_ascii=False)
