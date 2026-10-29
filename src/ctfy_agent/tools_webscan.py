#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Web 扫描扩展：ffuf 封装（目录/参数发现）。

设计原则：
  1. 纯 stdlib —— 只用 subprocess/json/shutil/tempfile，不引第三方 import
     （tests/smoke.py 的 case_stdlib_only 会对 src/ 做 AST 扫描）；
  2. 只吃参数、吐字符串 —— 目标校验 / 流量落库由 LocalTools 以回调注入，
     本模块不认识框架状态，便于单测；
  3. 命令行全硬编码 —— 模型只能给「值」，永远不能给开关。ffuf 的
     -x/-request/-recursion/-o 覆写一律不暴露，杜绝把扫描器变成 SSRF/任意写通道；
  4. 输出有硬上限 —— 与 solver 的 6000 可见上限对齐，避免模型拿到被静默截断的 JSON；
  5. 软 404 交给 ffuf 的 -ac 自动校准（CTF 的 SPA 兜底会对任意路径回同一长度，
     不校准的话 1 万条字典全是「命中」，等于没跑）。

为什么不让模型直接用 shell 跑 ffuf：
  - ffuf 默认打进度表，几千行糊满 32k 上下文；
  - 模型自由拼 -x/-request 会引入真实 SSRF；
  - 软 404 校准必须默认开，靠提示词要求不可靠。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

TOOL_TEXT_MAX = 6000
FUZZ_MAX_WORDS = 200          # 自定义 words 上限（超了让模型自己收窄，别塞整本字典）
FUZZ_MAX_RESULTS = 100        # max_results 硬上限
FUZZ_TIMEOUT_DEFAULT = 45     # 默认单次预算（秒）：10 步题里一步占 45 秒是可接受的
FUZZ_TIMEOUT_MAX = 90         # 硬上限
FUZZ_MAX_TIME_SLACK = 15      # subprocess 超时 = ffuf -maxtime + slack
FUZZ_ADDED_LEN = 40           # 截断时给尾部提示留的空间
# ffuf v2.1.0 的原生默认匹配码（含 2xx 全档 + 405 方法不允许 + 500，对 CTF 发现更有信号）
FUZZ_MATCH_CODES_DEFAULT = "200-299,301,302,307,401,403,405,500"

WORDLIST_DIR = Path(os.environ.get("CTFY_WORDLIST_DIR") or "/opt/wordlists")
# 名字 -> 文件名。只认这三个 basename，且只从 WORDLIST_DIR 拼，
# 模型给的字符串永远不进路径（杜绝 ../../etc/passwd 这类穿越）。
WORDLIST_FILES = {
    "common": "common.txt",   # ~4.7k 行，最快的默认档
    "small": "small.txt",     # raft-small-words ~11k 行，覆盖更广
    "params": "params.txt",   # burp 参数名 ~6.4k 行，参数发现
}

_EXT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,60}$")

FUZZ_SPEC = {
    "type": "function",
    "function": {
        "name": "fuzz",
        "description": (
            "用内置字典做路径/参数发现（ffuf 封装，输出已裁剪；已开自动软 404 校准，"
            "所以返回的是与基线有差异的真命中，不是「所有路径都 200」）。"
            "mode=dir：在 base 下爆破隐藏目录/文件；mode=param：在 url 上爆破隐藏参数名。"
            "只访问当前实例域（与 http_request 同一白名单），不接受任意命令行开关。"
            "命中会自动写进流量库（source=fuzz），可用 traffic 工具检索。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["dir", "param"], "default": "dir",
                         "description": "dir=路径/文件发现；param=参数名发现"},
                "base": {"type": "string",
                         "description": "dir 模式必填：目标基址，如 http://host:port/"},
                "url": {"type": "string",
                        "description": "param 模式必填：完整 URL；没有 FUZZ 占位时会自动追加 ?FUZZ=1"},
                "wordlist": {"type": "string", "enum": ["common", "small", "params"],
                             "default": "common",
                             "description": "内置字典：common 最快、small 覆盖广、params 参数名"},
                "words": {"type": "array", "items": {"type": "string"},
                          "description": f"自定义候选词（≤{FUZZ_MAX_WORDS} 条），优先于 wordlist"},
                "ext": {"type": "string",
                        "description": "附加扩展名，逗号分隔不加点，如 \"php,bak,zip\"（仅 dir 模式生效）"},
                "match_codes": {"type": "string", "default": FUZZ_MATCH_CODES_DEFAULT,
                                "description": "算作命中的状态码，逗号分隔，支持范围如 200-299"},
                "filter_size": {"type": "integer", "default": 0,
                                "description": "额外过滤该响应长度（软 404）；0=只靠自动校准"},
                "max_results": {"type": "integer", "default": 40,
                                "description": f"最多返回多少条命中（上限 {FUZZ_MAX_RESULTS}）"},
                "timeout_s": {"type": "integer", "default": FUZZ_TIMEOUT_DEFAULT,
                              "description": f"本次预算秒数（上限 {FUZZ_TIMEOUT_MAX}）"},
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
    return text[: limit - FUZZ_ADDED_LEN] + f"\n...[已截断，原长 {len(text)} 字符]"


def _normalize_codes(codes: str) -> str:
    """校验并归一化 match_codes（逗号分隔，元素可为 NNN 或 NNN-MMM 范围）。"""
    out: list[str] = []
    for tok in codes.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if "-" in tok:
            lo_s, _, hi_s = tok.partition("-")
            if not (lo_s.isdigit() and hi_s.isdigit()):
                _fail(f"match_codes 非法：{codes!r}（形如 \"200-299,302\"）")
            lo, hi = int(lo_s), int(hi_s)
            if not (100 <= lo <= hi <= 599):
                _fail(f"match_codes 区间非法：{tok!r}（需 100-599 且左端≤右端）")
            out.append(f"{lo}-{hi}")
        else:
            if not tok.isdigit() or not 100 <= int(tok) <= 599:
                _fail(f"match_codes 非法：{tok!r}（形如 \"200-299,302\"）")
            out.append(str(int(tok)))
    if not out:
        _fail("match_codes 不能为空")
    return ",".join(out)


def _resolve_wordlist(wordlist: str, words, work: Path) -> tuple[Path, int, str]:
    """返回 (字典文件, 行数, 字典名)。words 模式写进 work 沙盒并返回该临时路径。"""
    if words:
        if not isinstance(words, (list, tuple)):
            _fail("words 必须是字符串数组")
        cleaned: list[str] = []
        seen: set[str] = set()
        for item in words:
            token = str(item).strip()
            if not token or "\r" in token or "\n" in token:
                continue
            if token in seen:
                continue
            seen.add(token)
            cleaned.append(token)
        if not cleaned:
            _fail("words 里没有有效候选词")
        if len(cleaned) > FUZZ_MAX_WORDS:
            _fail(f"words 最多 {FUZZ_MAX_WORDS} 条，收到 {len(cleaned)} 条（请先收窄）")
        try:
            handle = tempfile.NamedTemporaryFile(
                "w", suffix=".txt", prefix=".fuzz_words_", delete=False,
                encoding="utf-8", newline="\n", dir=str(work),
            )
        except Exception as exc:  # noqa: BLE001
            _fail(f"沙盒写临时字典失败：{exc}")
        handle.write("\n".join(cleaned) + "\n")
        handle.close()
        return Path(handle.name), len(cleaned), "custom"

    key = (wordlist or "common").strip().lower()
    if key not in WORDLIST_FILES:
        _fail(f"未知 wordlist={wordlist!r}，可选：{', '.join(sorted(WORDLIST_FILES))}")
    path = WORDLIST_DIR / WORDLIST_FILES[key]
    if not path.is_file():
        _fail(f"字典缺失：{path}（镜像应内置；本地开发机可忽略此工具）")
    try:
        lines = sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except Exception as exc:  # noqa: BLE001
        _fail(f"字典不可读：{path}: {exc}")
    if lines <= 0:
        _fail(f"字典为空：{path}")
    return path, lines, key


def run_fuzz(*, mode: str = "dir", base: str = "", url: str = "", wordlist: str = "common",
             words=None, ext: str = "", match_codes: str = FUZZ_MATCH_CODES_DEFAULT,
             filter_size: int = 0, max_results: int = 40, timeout_s: int = FUZZ_TIMEOUT_DEFAULT,
             check_target, record, work_dir, ffuf_bin=None) -> str:
    """执行一次 ffuf 扫描并返回裁剪后的 JSON 字符串。

    check_target: (url)->None，非法目标抛 ToolError（复用 http_request 的白名单）
    record:       (url, status)->None，命中落流量库
    work_dir:     可写的临时目录
    ffuf_bin:     str 路径、或 argv 前缀 list（单测注入假 ffuf 用）
    """
    mode = (mode or "dir").strip().lower()
    if mode not in ("dir", "param"):
        _fail(f"未知 mode={mode!r}，只支持 dir / param")

    # ---- 目标（先校验再拼 FUZZ，避免把 FUZZ 交给白名单解析）----
    if mode == "dir":
        seed = (base or "").strip()
        if not seed:
            _fail("dir 模式必须给 base（如 http://host:port/）")
    else:
        seed = (url or "").strip()
        if not seed:
            _fail("param 模式必须给 url")
    check_target(seed)

    if mode == "dir":
        target = seed.rstrip("/") + "/FUZZ"
    elif "FUZZ" in seed:
        target = seed
    elif "?" not in seed:
        target = seed + "?FUZZ=1"
    else:
        target = seed + "&FUZZ=1"

    # ---- 参数校验 ----
    codes = _normalize_codes((match_codes or FUZZ_MATCH_CODES_DEFAULT).strip())
    # ext 仅 dir 模式生效（param 拼的是 ?FUZZ=1，扩展名无意义）：只在 dir 下解析/校验，
    # 避免 param 模式被一个用不上的 ext 误拦（审计修复）。
    # ffuf 把每个元素原样拼到词尾（text+ext），不带点的元素会拼出 wordbak 这种垃圾请求，
    # 所以这里强制每一项都带点。
    exts: list[str] = []
    if mode == "dir":
        for token in (ext or "").split(","):
            e = token.strip().lstrip(".")
            if not e:
                continue
            if not _EXT_RE.match(e):
                _fail(f"ext 非法：{token!r}（形如 \"php,bak,zip\"，逐项不带点）")
            exts.append("." + e)

    try:
        timeout_s = int(timeout_s or FUZZ_TIMEOUT_DEFAULT)
        max_results = int(max_results or 40)
        filter_size = int(filter_size or 0)
    except (TypeError, ValueError):
        _fail("timeout_s / max_results / filter_size 必须是整数")
    timeout_s = max(5, min(timeout_s, FUZZ_TIMEOUT_MAX))
    max_results = max(1, min(max_results, FUZZ_MAX_RESULTS))

    # ---- 二进制 ----
    if ffuf_bin is None:
        found = shutil.which("ffuf")
        if not found:
            _fail("ffuf 未安装（本镜像应内置；本地开发机没有属正常）")
        prefix: list[str] = [found]
    elif isinstance(ffuf_bin, (list, tuple)):
        prefix = [str(x) for x in ffuf_bin]
    else:
        prefix = [str(ffuf_bin)]

    # ---- 工作目录先建好；自定义字典也落这里（审计 B4：不落到系统 /tmp）----
    work = Path(work_dir)
    try:
        work.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        work = Path(tempfile.gettempdir())
    wordlist_path, lines, wordlist_name = _resolve_wordlist(wordlist, words, work)

    stamp = f"{os.getpid()}_{int(time.time() * 1000)}"
    out_path = work / f".fuzz_{stamp}.json"
    argv: list[str] = [
        *prefix,
        "-s", "-noninteractive",
        "-ac",                      # 自动软 404 校准：CTF 的 SPA 兜底全靠它
        "-of", "json",
        "-o", str(out_path),
        "-w", str(wordlist_path),
        "-u", target,
        "-t", "15",
        "-timeout", "10",
        "-maxtime", str(timeout_s),
        "-mc", codes,
    ]
    if mode == "dir" and exts:
        argv += ["-e", ",".join(exts)]
    if filter_size > 0:
        argv += ["-fs", str(filter_size)]

    env = {k: v for k, v in os.environ.items()
           if k.lower() not in ("http_proxy", "https_proxy", "all_proxy", "no_proxy")}
    env["NO_COLOR"] = "1"

    t0 = time.perf_counter()
    proc_out = ""
    try:
        proc = subprocess.run(
            argv, cwd=str(work), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s + FUZZ_MAX_TIME_SLACK,
        )
        proc_out = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        rc = -1
        proc_out = f"ffuf 超时（>{timeout_s + FUZZ_MAX_TIME_SLACK}s）"
    except Exception as exc:  # noqa: BLE001
        rc = -1
        proc_out = f"{type(exc).__name__}: {exc}"
    subprocess_failed = rc != 0

    raw: dict = {}
    try:
        if out_path.is_file():
            raw = json.loads(out_path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        raw = {}
    finally:
        for p in ([out_path] + ([wordlist_path] if words else [])):
            try:
                p.unlink()
            except Exception:  # noqa: BLE001
                pass

    if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
        tail = proc_out[-400:] if proc_out else "（无输出）"
        _fail(f"ffuf 未产出可解析结果（rc={rc}）：{tail}")

    results = raw.get("results") or []
    hits: list[dict] = []
    resolved: list[str] = []   # 与 hits 一一对应：ffuf 解析后的真实命中 URL（落库用）
    for item in results[:max_results]:
        if not isinstance(item, dict):
            continue
        inp = item.get("input") or {}
        word = inp.get("FUZZ") if isinstance(inp, dict) else None
        hits.append({
            "w": word if word is not None else "",
            "status": item.get("status"),
            "len": item.get("length"),
            "words": item.get("words"),
        })
        u = item.get("url")
        resolved.append(u if isinstance(u, str) else "")   # 畸形输出不当 url 用（审计 C1）

    # 命中落流量库（source=fuzz），让 traffic 能检索/join；失败不影响本次返回。
    # 只落 shown 的命中（审计 D3）：超宽匹配可能上千行，全落会冲垮 P1 的 5000 行上限。
    # 落库用 ffuf 输出的真实 url：target.replace("FUZZ", word) 会误伤 URL 里其他位置的
    # "FUZZ"；本地假 ffuf 无 url 字段时再退回拼装（审计修复）。
    for hit, url in zip(hits, resolved):
        word = str(hit.get("w") or "")
        try:
            record(url or target.replace("FUZZ", word), hit.get("status"))
        except Exception:  # noqa: BLE001
            pass

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
    out: dict = {
        "mode": mode,
        "wordlist": wordlist_name,
        "scanned": lines,
        "matched": len(results),
        "shown": len(hits),
        "elapsed_ms": elapsed_ms,
        "hits": hits,
    }
    if filter_size > 0:
        out["filter_size"] = filter_size
    # 达到 -maxtime 上限 → 大概率没扫完（审计 D1：别让模型把部分当全集）
    if elapsed_ms >= timeout_s * 1000 - 1000:
        out["stopped_early"] = True
    if rc != 0:
        out["exit_code"] = rc

    # 审计 D2：超 6000 上限先从尾部裁 hits，保证返回合法 JSON；裁完再统一拼 note。
    text = json.dumps(out, ensure_ascii=False)
    while len(text) > TOOL_TEXT_MAX and len(hits) > 1:
        hits.pop()
        out["shown"] = len(hits)
        out["hits_truncated"] = True
        text = json.dumps(out, ensure_ascii=False)
    if len(hits) == 1 and len(text) > TOOL_TEXT_MAX:
        out["hits"] = []
        out["shown"] = 0
        out["hits_truncated"] = True
        text = json.dumps(out, ensure_ascii=False)

    more = len(results) - len(hits)
    if more > 0:
        out["more_hits"] = more

    notes: list[str] = []
    if subprocess_failed:
        notes.append(f"ffuf 退出码 {rc}")
    if out.get("stopped_early"):
        notes.append(f"达到时间上限 {timeout_s}s，可能只扫了部分字典，建议换 common 或收窄范围")
    if out.get("more_hits"):
        notes.append(f"还有 {out['more_hits']} 条命中未展示，可用 match_codes 收窄")
    if out.get("hits_truncated"):
        notes.append("命中列表被 6000 字符上限截断")
    if not hits:
        if subprocess_failed:
            notes.append(f"无命中：{proc_out[-200:]}")
        else:
            notes.append("字典内没有差异命中（已开自动软 404 校准）。"
                         "可换 wordlist=small、加 ext=\"bak,zip,php\"，"
                         "或确认目标不是 SPA 兜底（所有路径同长度）。")
    if notes:
        out["note"] = "；".join(notes)
    return _clip(json.dumps(out, ensure_ascii=False))
