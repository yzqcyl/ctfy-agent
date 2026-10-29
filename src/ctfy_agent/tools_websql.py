#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SQL 注入自动化（P3 Phase 2）：sqlmap 的窄封装。

为什么需要：时间盲注/布尔盲注手工做极痛（要反复发请求、量时间差），10 步预算里基本做不完。
封装要点（与 fuzz 同一套护栏思路）：
  1. 命令行全硬编码：只开放 url/data/param/technique/level/risk/timeout，
     绝不暴露 --os-shell / --file-read / --file-write / --proxy / --tor 这类能升级影响的开关；
  2. 输出只回结论（是否可注入 / 注入点 / DBMS / 一段 Payload），不回显 dump 全量；
  3. sqlmap 本体装在镜像 /opt/sqlmap（构建期装），本地开发没有就干净报错，不抛栈；
  4. 命中落流量库（source=sqli），便于 traffic 检索与 diff。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

TOOL_TEXT_MAX = 6000
SQLI_TEXT_SLACK = 40
SQLI_TIMEOUT_DEFAULT = 60
SQLI_TIMEOUT_MAX = 120
SQLI_LEVEL_MAX = 3
SQLI_RISK_MAX = 2
SQLI_MAX_FINDINGS = 3
SQLI_PAYLOAD_MAX = 300   # 审查 C4：UNION 类 payload 常 >180 字符，截断后不可直接复用；放宽并打截断标记
SQLI_TECHNIQUES = "BEUSTQ"
SQLMAP_DEFAULT = "/opt/sqlmap/sqlmap.py"


def _tool_error() -> type[BaseException]:
    try:
        from .tools_local import ToolError  # type: ignore

        return ToolError
    except Exception:  # noqa: BLE001
        return RuntimeError


def _fail(msg: str):
    raise _tool_error()(msg)


def _clip(text: str, limit: int = TOOL_TEXT_MAX) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - SQLI_TEXT_SLACK] + f"\n...[已截断，原长 {len(text)} 字符]"


SQLI_SPEC = {
    "type": "function",
    "function": {
        "name": "sqli",
        "description": (
            "SQL 注入自动化（sqlmap 窄封装）：给定 URL（或 POST body）自动判定注入点与注入类型，"
            "覆盖布尔/报错/时间/UNION/堆叠/内联六类技术。参数面只有 url/data/param/technique/level/risk/timeout，"
            "不接受任意命令行；只回结论与一段可复用的 Payload，不做 dump。适合盲注这类手工极痛的场景。"
            "注意：单次可能发出数百个请求（--threads=1 只限并发不限总量），"
            "WAF 敏感或限速目标先收窄 technique/level。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "带参数的完整 URL（如 http://host/item?id=1，必填；可用 * 标记自定义注入点）"},
                "data": {"type": "string", "description": "POST 等方法的请求体（如 a=1&b=2）"},
                "param": {"type": "string", "description": "只测指定参数名"},
                "technique": {"type": "string", "default": "BEUSTQ",
                              "description": "技术集合：B 布尔/E 报错/U 联合/S 堆叠/T 时间/Q 内联，可只选几个（如 T）"},
                "level": {"type": "integer", "default": 1, "description": "测试广度 1-3（默认 1）"},
                "risk": {"type": "integer", "default": 1, "description": "测试风险 1-2（默认 1）"},
                "timeout_s": {"type": "integer", "default": 60,
                              "description": "整体超时秒数（默认 60，上限 120）"},
            },
        },
    },
}


def _resolve_sqlmap(sqlmap_py: str | None) -> str:
    candidate = sqlmap_py or os.environ.get("CTFY_SQLMAP") or SQLMAP_DEFAULT
    path = Path(str(candidate))
    if not path.is_file():
        _fail(f"镜像内没有 sqlmap（找的是 {path}）。本地开发环境属正常；"
              "改用 http_request 手工做布尔/时间对照（改一个字符看回显差异）")
    return str(path)


def _clean_technique(technique: str) -> str:
    raw = "".join(str(technique or "").split()).upper()
    if not raw:
        return SQLI_TECHNIQUES
    bad = [c for c in raw if c not in SQLI_TECHNIQUES]
    if bad:
        _fail(f"technique 只能由 {SQLI_TECHNIQUES} 组成，收到非法字符：{''.join(bad)}")
    out = "".join(dict.fromkeys(raw))  # 保序去重
    return out


def _clamp_int(value, *, low: int, high: int, name: str) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        _fail(f"{name} 必须是整数，收到 {value!r}")
    return max(low, min(high, num))


_PARAM_RE = re.compile(r"^Parameter:\s*(.+?)\s*$")
_TYPE_RE = re.compile(r"^Type:\s*(.+?)\s*$")
_TITLE_RE = re.compile(r"^Title:\s*(.+?)\s*$")
_PAYLOAD_RE = re.compile(r"^Payload:\s*(.+?)\s*$")
_DBMS_RE = re.compile(r"back-end DBMS:\s*(.+?)\s*$")


def _parse_sqlmap(stdout: str) -> dict:
    """从 sqlmap 文本输出里抽结论：注入点分组 + DBMS + 是否可注入。"""
    findings: list[dict] = []
    current: dict | None = None
    dbms = ""
    injectable = False
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "identified the following injection point" in line:
            injectable = True
            continue
        match = _PARAM_RE.match(line)
        if match:
            current = {"parameter": match.group(1)[:120]}
            findings.append(current)
            continue
        if current is not None:
            for key, regex in (("type", _TYPE_RE), ("title", _TITLE_RE), ("payload", _PAYLOAD_RE)):
                hit = regex.match(line)
                if hit:
                    val = hit.group(1)
                    current[key] = val[:SQLI_PAYLOAD_MAX] + ("…" if len(val) > SQLI_PAYLOAD_MAX else "")
                    break
        hit = _DBMS_RE.search(line)
        if hit:
            dbms = hit.group(1)[:80]
            injectable = True
        # 审查 A3：不设负向重置——sqlmap 对多参数目标会逐参打印
        # "GET parameter 'x' does not seem to be injectable"，若据此把整体翻成 False，
        # 「参数 A 可注入 + 参数 B 不可注入」会被误判成整体不可注入。不可注入就是默认值。
    return {"injectable": injectable or bool(findings), "dbms": dbms,
            "findings": findings[:SQLI_MAX_FINDINGS]}


def run_sqli(*, url: str = "", data: str = "", param: str = "", technique: str = SQLI_TECHNIQUES,
             level=1, risk=1, timeout_s=SQLI_TIMEOUT_DEFAULT, check_target,
             record=None, work_dir: Path | None = None, sqlmap_py: str | None = None) -> str:
    target = (url or "").strip()
    if not target:
        _fail("sqli 需要 url（带参数的完整地址，如 http://host/item?id=1）")
    check_target(target)
    if any(c in target for c in (" ", "\t", "\r", "\n")):
        _fail("url 里不能有空白字符")

    # 便宜校验先做：参数面错了就不该去解析/启动二进制
    tech = _clean_technique(technique)
    lvl = _clamp_int(level, low=1, high=SQLI_LEVEL_MAX, name="level")
    rsk = _clamp_int(risk, low=1, high=SQLI_RISK_MAX, name="risk")
    budget = _clamp_int(timeout_s, low=5, high=SQLI_TIMEOUT_MAX, name="timeout_s")
    sqlmap = _resolve_sqlmap(sqlmap_py)
    base = Path(work_dir) if work_dir else Path("/tmp")
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        base = Path("/tmp")
    out_dir = base / ".sqlmap_out"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        out_dir = Path("/tmp") / ".sqlmap_out"
        out_dir.mkdir(parents=True, exist_ok=True)

    argv = [
        sys.executable, sqlmap, "-u", target,
        "--batch", "--disable-coloring", "--flush-session",
        f"--level={lvl}", f"--risk={rsk}", f"--technique={tech}",
        # 不跟随重定向（审计修复）：sqlmap 无逐跳回调，跳转目标不经 _check_target 白名单复查，
        # 是全链路唯一「重定向逃逸白名单」的出口；跳转目标改由模型用 http_request 显式访问。
        "--timeout=10", "--retries=0", "--threads=1", "--max-redirects=0",
        "--output-dir", str(out_dir),
    ]
    if data:
        argv += ["--data", str(data)]
    if param:
        argv += ["-p", str(param)]

    started = time.perf_counter()
    stopped_early = False
    stdout = ""
    stderr = ""
    rc = 0
    try:
        proc = subprocess.run(
            argv, cwd=str(base), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=budget,
        )
        stdout, stderr, rc = proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired as exc:
        stopped_early = True
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", "replace")
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", "replace")
        rc = -1
    except OSError as exc:
        _fail(f"sqlmap 启动失败：{exc}")

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    parsed = _parse_sqlmap(stdout)
    payload = {
        "tool": "sqli",
        "target": target,
        "params": {"technique": tech, "level": lvl, "risk": rsk},
        "injectable": bool(parsed["injectable"]),
        "dbms": parsed["dbms"],
        "findings": parsed["findings"],
        "elapsed_ms": elapsed_ms,
    }
    if stopped_early:
        payload["stopped_early"] = True
        payload["notes"] = [f"超过 {budget}s 已终止，结论可能不完整（先看 findings 是否已有命中）"]
    elif rc != 0:
        payload["exit_code"] = rc
    if parsed["injectable"]:
        payload["next"] = ("把 findings[].payload 里的注入写法拿去 http_request 复验（原样替换参数值），"
                           "再用 http_burst 批量取数据；不要直接 dump 全库，先定位 flag 所在表/列。")
    else:
        payload["next"] = ("没测出注入：换 technique=T（时间盲注）或加 level=2；"
                           "也确认目标真的拼了 SQL（报错回显/字段数变化），别在 ORM 参数化上白烧步数。")
    if stderr.strip():
        payload["stderr_tail"] = stderr.strip().splitlines()[-3:]

    summary = _clip(json.dumps(payload, ensure_ascii=False))
    if record is not None:
        try:
            record(url=target, method="POST" if data else "GET",
                   resp_body=json.dumps(parsed, ensure_ascii=False),
                   mime="application/json")
        except Exception:  # noqa: BLE001 - 落库失败不该把工具结果搞没
            pass
    return summary
