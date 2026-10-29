#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web 对照探测（P3）：越权三态对照 + JS 静态侦察。

设计原则（与 tools_webscan 同）：
  1. 纯 stdlib，不引第三方（case_stdlib_only 会 AST 扫 src/）；
  2. 只吃参数、吐字符串；目标校验 / 抓取 / 落库由 LocalTools 以回调注入，便于单测；
  3. 无危险开关：变体集合、请求头、路径变形全部在模块内白名单化，模型只能「选集合」；
  4. 输出 ≤6000 且合法 JSON（超限先裁列表，不靠 _clip 硬切）。

为什么需要这两个工具：
  - authdiff：实测瓶颈在「flag 发现 / access-control 面」。手工判定「这个端点是否忘鉴权」
    至少要发 2~4 次请求再肉眼比对，10 步预算里太贵，做成一次调用。
  - jsrecon：SPA 题把 bundle 落盘再 grep 要烧 3~4 步；http_map 只抓静态链接/表单，
    抓不到 JS 里的 API 面。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.parse import urljoin, urlparse

TOOL_TEXT_MAX = 6000
WEB_SEC_ADDED_LEN = 40

# ---------- authdiff ----------
AUTHDIFF_MAX_HEADERS = 6
AUTHDIFF_MAX_ROWS = 14        # 审查 B3：anon+bypass+path 全开 = 1+1+4+7 = 13 行，上限要盖住
AUTHDIFF_BUDGET = 45.0        # 审查 B1：总墙钟预算（秒）；tarpit 目标不该拖死单步
# 常见「信任客户端头」的绕过头：只发这 4 个，不开放任意头名（避免把工具变成扫描器）
AUTHDIFF_BYPASS_HEADERS = (
    ("X-Forwarded-For", "127.0.0.1"),
    ("X-Original-URL", "/"),
    ("X-Rewrite-URL", "/"),
    ("X-Admin", "true"),
)
AUTHDIFF_VARIANTS = ("anon", "bypass", "path")
_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

# ---------- jsrecon ----------
JSRECON_MAX_BUNDLES = 6
JSRECON_MAX_BUNDLE_CHARS = 512 * 1024     # 单个 bundle 只留前 512KB
JSRECON_MAX_JSON_CHARS = 6 * 1024 * 1024  # 所有 JS 合计上限（防内存爆）
JSRECON_MAX_PER_KIND = 12
JSRECON_BUDGET = 60.0         # 审查 B1：页面 + bundle 抓取的总墙钟预算（秒）
JSRECON_NOISE_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
                     ".woff", ".woff2", ".ttf", ".eot", ".map", ".webp", ".mp4", ".txt")

AUTHDIFF_SPEC = {
    "type": "function",
    "function": {
        "name": "authdiff",
        "description": (
            "越权/鉴权对照探针：把同一个请求按「原样（带会话）／去掉 Cookie／加代理绕过头／"
            "路径变形」四组变体各发一次，比对状态码、长度、内容哈希，直接给出「匿名可读="
            "疑似未鉴权」这类结论。变体集合只能从 anon/bypass/path 里选，不接受任意命令行。"
            "所有请求都记入流量库（source=authdiff），可用 traffic 做 diff。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要对照的完整 URL（必填）"},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"],
                           "default": "GET", "description": "请求方法"},
                "body": {"type": "string", "description": "POST 等方法的请求体"},
                "headers": {"type": "object", "description": "额外请求头（≤6 个）"},
                "variants": {"type": "string", "default": "anon,bypass",
                             "description": "变体集合：anon=去 Cookie；bypass=代理绕过头；path=路径变形"},
            },
        },
    },
}

JSRECON_SPEC = {
    "type": "function",
    "function": {
        "name": "jsrecon",
        "description": (
            "JS 静态侦察：拉页面引用的前端 bundle 与内联脚本，抽取 API 路径、参数名、前端路由、"
            "硬编码密钥线索、sourceMap 与可疑注释。用于「SPA 空壳、接口只在 JS 里」的题，"
            "省掉手工落盘 + grep 的步骤。抓取记录进流量库（source=jsrecon）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "页面 URL（必填，同实例域）"},
            },
        },
    },
}


def _tool_error() -> type[BaseException]:
    """惰性取框架的 ToolError，避免与 tools_local 形成循环导入。"""
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
    return text[: limit - WEB_SEC_ADDED_LEN] + f"\n...[已截断，原长 {len(text)} 字符]"


def _h8(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()[:8]


def _clean_headers(headers) -> dict[str, str]:
    """模型给的头：只留单行、无控制字符的键值（防注入 CRLF 拆请求）。"""
    if not headers:
        return {}
    if not isinstance(headers, dict):
        _fail("headers 必须是对象")
    if len(headers) > AUTHDIFF_MAX_HEADERS:
        _fail(f"headers 最多 {AUTHDIFF_MAX_HEADERS} 个")
    out: dict[str, str] = {}
    for key, value in headers.items():
        k, v = str(key), str(value)
        if not k or any(c in k + v for c in "\r\n"):
            _fail(f"header 非法（空名或含换行）：{k!r}")
        out[k] = v
    return out


def _parse_variants(variants: str) -> list[str]:
    picked: list[str] = []
    for token in str(variants or "").split(","):
        item = token.strip().lower()
        if not item:
            continue
        if item not in AUTHDIFF_VARIANTS:
            _fail(f"未知变体 {item!r}，可选：{', '.join(AUTHDIFF_VARIANTS)}")
        if item not in picked:
            picked.append(item)
    return picked or ["anon", "bypass"]


def _path_variants(url: str) -> list[tuple[str, str]]:
    """常见路径归一化绕过变形（与基线不同的才返回）。"""
    parsed = urlparse(url)
    path = parsed.path or "/"
    qs = parsed.query
    candidates = [
        ("trailing-slash", path + ("/" if not path.endswith("/") else "")),
        ("double-slash", "//" + path.lstrip("/")),
        ("dot-segment", "/./" + path.lstrip("/")),
        ("semicolon", path.rstrip("/") + "/..;/"),
        ("upper", path.upper()),
        ("encoded-dot", "/%2e" + path),
        ("trailing-dot", path.rstrip("/") + "/."),
    ]
    out: list[tuple[str, str]] = []
    seen = {path}
    for label, vpath in candidates:
        if vpath in seen or vpath == path:
            continue
        seen.add(vpath)
        out.append((label, parsed._replace(path=vpath, query=qs).geturl()))
    return out


def _probe(fetch, url: str, *, label: str, method: str, body: str,
           extra_headers: dict | None, use_jar: bool) -> dict:
    """发一个变体，返回可比较的三元组；失败不抛出，标记 error 继续。"""
    row: dict = {"label": label, "url": url}
    try:
        status, ctype, text = fetch(url, method=method, body=body,
                                    extra_headers=extra_headers, use_jar=use_jar)
    except Exception as exc:  # noqa: BLE001 - 单个变体失败不该让整次对照作废
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row
    row.update({
        "status": status,
        "len": len(text or ""),
        "h": _h8(text or ""),
        "type": (ctype or "").split(";")[0].strip()[:40],
    })
    return row


def _same(a: dict, b: dict) -> bool:
    return (a.get("status") == b.get("status")
            and a.get("len") == b.get("len")
            and a.get("h") == b.get("h"))


def run_authdiff(*, url: str = "", method: str = "GET", body: str = "", headers=None,
                 variants: str = "anon,bypass", cookie_present: bool = True,
                 check_target, fetch) -> str:
    """三态/多态对照，返回结论 JSON。"""
    target = (url or "").strip()
    if not target:
        _fail("authdiff 需要 url")
    check_target(target)
    method = (method or "GET").strip().upper()
    if method not in _METHODS:
        _fail(f"method 非法：{method!r}（支持 {', '.join(_METHODS)}）")
    hdrs = _clean_headers(headers)
    picked = _parse_variants(variants)

    rows: list[dict] = []
    # 审查 B1：先把全部变体排成计划，再按总墙钟预算逐个执行；基线永远第一个跑。
    started = time.perf_counter()
    plan: list[tuple[str, str, dict | None, bool]] = [("authed", target, hdrs or None, True)]
    if "anon" in picked:
        plan.append(("anon", target, hdrs or None, False))
    if "bypass" in picked:
        for name, value in AUTHDIFF_BYPASS_HEADERS:
            merged = dict(hdrs)
            merged[name] = value
            plan.append((f"bypass:{name}", target, merged, True))
    if "path" in picked:
        for plabel, vurl in _path_variants(target):
            plan.append((f"path:{plabel}", vurl, hdrs or None, True))

    stopped_early = False
    for label, purl, pheaders, pjar in plan:
        if rows and time.perf_counter() - started > AUTHDIFF_BUDGET:
            stopped_early = True
            break
        rows.append(_probe(fetch, purl, label=label, method=method, body=body,
                           extra_headers=pheaders, use_jar=pjar))
    baseline = rows[0]

    by_label = {r["label"]: r for r in rows}
    for row in rows:
        if row is not baseline and "error" not in row and "error" not in baseline:
            row["same"] = _same(row, baseline)

    verdicts: list[str] = []
    if "error" in baseline:
        verdicts.append("基线请求失败，先确认目标可达与会话有效")
    else:
        anon = by_label.get("anon")
        if anon:
            if "error" in anon:
                verdicts.append("匿名请求失败")
            elif anon.get("same"):
                if cookie_present:
                    verdicts.append("匿名可读：去掉 Cookie 后与基线完全一致 → 该端点疑似未鉴权（越权线索）")
                else:
                    verdicts.append("该端点不要求会话即可读取（当前 jar 没有该站 Cookie，"
                                    "anon 与基线必然相同，此结论只说明「不需要登录」，"
                                    "先登录拿到会话再跑一次才有越权判定意义）")
            elif anon.get("status") == baseline.get("status"):
                verdicts.append("匿名可达但内容不同 → 可能只有部分字段受控，值得按字段 diff")
            else:
                verdicts.append("鉴权生效：匿名请求被拒（状态码不同）")
        changed = [r["label"] for r in rows
                   if r["label"].startswith("bypass:") and not r.get("same") and "error" not in r]
        if changed:
            verdicts.append(f"绕过头生效：{', '.join(changed)} 改变了结果 → 服务端信任客户端代理头")
        path_changed = [r["label"] for r in rows
                        if r["label"].startswith("path:") and not r.get("same") and "error" not in r]
        if path_changed:
            verdicts.append(f"路径变形生效：{', '.join(path_changed)} → 归一化/解析差异可绕过路由规则")
        if not verdicts:
            verdicts.append("各变体无差异：该端点表现一致，换下一个输入点")

    if len(rows) > AUTHDIFF_MAX_ROWS:
        rows = rows[:AUTHDIFF_MAX_ROWS]
        verdicts.append(f"变体过多已截断到 {AUTHDIFF_MAX_ROWS} 条")
    payload = {
        "tool": "authdiff",
        "target": target,
        "method": method,
        "baseline": {k: baseline.get(k) for k in ("status", "len", "h", "type", "error") if k in baseline},
        "rows": rows,
        "verdicts": verdicts,
        "next": ("命中的绕过头/路径变形可直接用于 http_request 复验；字段级差异用 traffic op=diff 比两次响应。"
                 if any("生效" in v or "匿名可读" in v for v in verdicts)
                 else "换个端点或换 method（POST/PUT）再对照一次。"),
    }
    if stopped_early:
        payload["stopped_early"] = True
        payload["notes"] = [f"超过 {AUTHDIFF_BUDGET:.0f}s 预算，{len(rows)}/{len(plan)} 个变体已完成，结论基于已完成部分"]
    return _clip(json.dumps(payload, ensure_ascii=False))


# ---------------- jsrecon ----------------

_SCRIPT_SRC_RE = re.compile(r"""(?is)<script\b[^>]*\bsrc\s*=\s*["']([^"']+)["']""")
_INLINE_SCRIPT_RE = re.compile(r"(?is)<script\b(?![^>]*\bsrc\s*=)[^>]*>(.*?)</script>")
_SOURCEMAP_RE = re.compile(r"[#@]\s*sourceMappingURL=([^\s*\"']{3,200})")
_FETCH_CALL_RE = re.compile(
    r"""(?i)\b(fetch|axios(?:\.(?:get|post|put|delete|patch))?|\$\.(?:get|post|ajax))\s*\(\s*["'`]([^"'`]{2,200})["'`]"""
)
_STR_LIT_PATH_RE = re.compile(r"""["'`](/[A-Za-z0-9_\-./{}:$~%]{2,80})["'`]""")
_PARAMS_OBJ_RE = re.compile(r"(?is)\b(?:params|body|data|payload|query)\s*:\s*\{([^{}]{0,400})\}")
_PARAM_KEY_RE = re.compile(r"""["']?([A-Za-z_$][\w$]{1,30})["']?\s*:""")
_SEARCHPARAM_RE = re.compile(r"""(?i)searchParams\.(?:set|append)\(\s*["'`]([^"'`]{1,40})["'`]""")
_ROUTE_PATH_RE = re.compile(r"""(?i)\bpath\s*:\s*["'`](/[^"'`]{1,80})["'`]""")
_SECRET_RE = re.compile(
    r"""(?i)\b(api[_-]?key|secret|token|password|passwd|bearer|access[_-]?key)\b\s*[:=]\s*["'`]([^"'`\s]{6,120})["'`]"""
)
_COMMENT_HINT_RE = re.compile(
    r"(?i)(?://|/\*)\s*([^\n]{0,160}?\b(?:TODO|FIXME|HACK|debug|internal|admin|flag)\b[^\n]{0,120})"
)
_PRIORITY_WORDS = ("admin", "internal", "flag", "upload", "user", "auth", "login", "token", "api")


def _rank(items: list[str]) -> list[str]:
    def score(item: str) -> int:
        low = item.lower()
        return -sum(1 for word in _PRIORITY_WORDS if word in low)
    return sorted(dict.fromkeys(items), key=score)


def _is_noise_path(path: str) -> bool:
    low = path.lower().split("?")[0]
    if low in ("/", ""):
        return True
    return low.endswith(JSRECON_NOISE_EXT)


def run_jsrecon(*, url: str = "", check_target, fetch) -> str:
    """拉页面 + bundle，抽 API 面，返回结论 JSON。"""
    target = (url or "").strip()
    if not target:
        _fail("jsrecon 需要 url")
    check_target(target)
    started = time.perf_counter()  # 审查 B1：页面 + bundle 抓取的总预算

    status, ctype, html = fetch(target, method="GET", body="", extra_headers=None, use_jar=True)
    scripts = _SCRIPT_SRC_RE.findall(html or "")
    inline = _INLINE_SCRIPT_RE.findall(html or "")

    bundles: list[dict] = []
    js_chunks: list[str] = []
    budget = JSRECON_MAX_JSON_CHARS
    stopped_early = False
    for src in scripts[:JSRECON_MAX_BUNDLES]:
        if time.perf_counter() - started > JSRECON_BUDGET:
            stopped_early = True
            break
        # 审查 A2：相对 src 必须相对页面 URL 解析（urljoin），拍平到站点根会抓错文件；
        # 协议相对 //host/、绝对 http(s) 也由 urljoin 正确处理，跨域交给 check_target 拦。
        abs_url = urljoin(target, src)
        try:
            check_target(abs_url)
        except Exception:  # noqa: BLE001 - 跨域/非法 bundle 直接跳过
            continue
        try:
            bstatus, bctype, text = fetch(abs_url, method="GET", body="",
                                          extra_headers=None, use_jar=True)
        except Exception as exc:  # noqa: BLE001
            bundles.append({"url": abs_url, "error": f"{type(exc).__name__}: {exc}"})
            continue
        text = text or ""
        truncated = len(text) > JSRECON_MAX_BUNDLE_CHARS
        kept = text[:JSRECON_MAX_BUNDLE_CHARS]
        if budget > 0:
            kept = kept[:budget]
            budget -= len(kept)
            js_chunks.append(kept)
        bundles.append({"url": abs_url, "status": bstatus,
                        "len": len(text), "truncated": truncated})

    for chunk in inline:
        if budget <= 0:
            break
        kept = (chunk or "")[:budget]
        budget -= len(kept)
        js_chunks.append(kept)

    js = "\n".join(js_chunks)
    api_paths = [p for p in _STR_LIT_PATH_RE.findall(js) if not _is_noise_path(p)]
    calls = [f"{fn}({arg})" for fn, arg in _FETCH_CALL_RE.findall(js)][:JSRECON_MAX_PER_KIND * 2]
    params: list[str] = []
    for block in _PARAMS_OBJ_RE.findall(js):
        params.extend(_PARAM_KEY_RE.findall(block))
    params.extend(_SEARCHPARAM_RE.findall(js))
    routes = _ROUTE_PATH_RE.findall(js)
    secrets = [f"{k}={v[:60]}" for k, v in _SECRET_RE.findall(js)]
    maps = _SOURCEMAP_RE.findall(js)
    comments = [c.strip() for c in _COMMENT_HINT_RE.findall(js)]

    payload = {
        "tool": "jsrecon",
        "page": {"status": status, "bytes": len(html or ""),
                 "type": (ctype or "").split(";")[0].strip()[:40],
                 "scripts": len(scripts), "inline": len(inline)},
        "bundles": bundles,
        "api": _rank(api_paths)[:JSRECON_MAX_PER_KIND],
        "api_total": len(set(api_paths)),
        "calls": calls[:JSRECON_MAX_PER_KIND],
        "params": _rank(params)[:JSRECON_MAX_PER_KIND],
        "routes": _rank(routes)[:JSRECON_MAX_PER_KIND],
        "secrets": secrets[:JSRECON_MAX_PER_KIND],
        "sourcemaps": maps[:4],
        "comments": comments[:6],
    }
    if not api_paths and not calls:
        payload["note"] = ("没抽到 API 线索：可能是纯静态站或 bundle 被混淆/加密；"
                           "先用 http_map crawl 看链接与表单，或对可疑 path 直接 http_request。")
    elif payload["sourcemaps"]:
        payload["note"] = "发现 sourceMap，可拉 .map 还原源码后按行读（比猜路径快）。"
    else:
        payload["note"] = "对高置信 API 路径用 http_request 逐个读；参数名可直接用于 fuzz mode=param 的自定义词表。"

    if stopped_early:
        payload["stopped_early"] = True
        payload["note"] = f"超过 {JSRECON_BUDGET:.0f}s 抓取预算提前停止；" + payload.get("note", "")

    # 超限：按优先级从低到高裁列表，保证返回合法 JSON（bundles 是兜底，裁到空则必然合法）
    text = json.dumps(payload, ensure_ascii=False)
    for key in ("comments", "sourcemaps", "routes", "params", "calls", "api", "bundles"):
        while len(text) > TOOL_TEXT_MAX and payload.get(key):
            payload[key] = payload[key][:-1]
            text = json.dumps(payload, ensure_ascii=False)
    return _clip(text)
