# -*- coding: utf-8 -*-
"""ffuf 兼容的最小实现（纯标准库），用于本机没有 ffuf 时兜底。

只实现 ctfy_agent 自己生成的那套参数组合：
  ffuf -s -noninteractive -ac -of json -o <out> -w <wordlist> -u <url with FUZZ>
       -t 15 -timeout 10 -maxtime N -mc <codes> [-e .php,.bak] [-fs <size>]

输出与 ffuf 的 `-of json` 结构对齐：{"results": [{"input":{"FUZZ":w},"status":..,
"length":..,"words":..,"url":..}]}，所以上层解析代码一行都不用改。

安全性/行为：
  * 只用 GET，不跟随重定向（与 ffuf 默认一致）；
  * -ac 软 404 校准：先用随机路径探一次，把「同状态码且同长度」的响应视为兜底页丢弃；
  * 有 -fs 时按长度过滤；
  * 严格遵守 -maxtime。
"""
from __future__ import annotations

import json
import random
import re
import ssl
import string
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

UA = "ctfy-shim/1.0"

# ffuf 默认**不跟随重定向**（要 -r 才跟）。urllib 默认会跟，导致 301/302 被跟成 200，
# 命中判断全错 —— 必须显式禁用。
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401,ANN001
        return None




def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def _arg(argv: list[str], flag: str, default: str = "") -> str:
    if flag in argv:
        i = argv.index(flag)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def _parse_codes(spec: str) -> set[int] | None:
    spec = (spec or "").strip().lower()
    if not spec or spec == "all":
        return None
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            if a.isdigit() and b.isdigit():
                out.update(range(int(a), int(b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out or None


def _fetch(opener, url: str, timeout: float, need_body: bool = False) -> tuple[int, int, str]:
    """返回 (状态码, 响应长度, 响应体文本)。need_body=False 时不解码，省开销。"""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read(1 << 20)
            return int(resp.status), len(body), (
                body.decode("utf-8", "replace") if need_body else "")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(1 << 20)
        except Exception:  # noqa: BLE001
            body = b""
        return int(exc.code), len(body), (
            body.decode("utf-8", "replace") if need_body else "")
    except Exception:  # noqa: BLE001
        return 0, 0, ""


def main(argv: list[str]) -> int:
    _force_utf8()
    target = _arg(argv, "-u")
    wordlist = _arg(argv, "-w")
    out_path = _arg(argv, "-o")
    codes_spec = _arg(argv, "-mc", "200-299,301,302,307,401,403,405,500")
    ext_spec = _arg(argv, "-e")
    threads = int(_arg(argv, "-t", "15") or 15)
    per_timeout = float(_arg(argv, "-timeout", "10") or 10)
    maxtime = float(_arg(argv, "-maxtime", "200") or 200)
    filter_size = int(_arg(argv, "-fs", "0") or 0)
    fc_spec = _arg(argv, "-fc")
    fr_spec = _arg(argv, "-fr")
    auto_cal = "-ac" in argv

    if not target or "FUZZ" not in target:
        print("ffuf-shim: 需要 -u 且 URL 含 FUZZ", file=sys.stderr)
        return 2
    if not wordlist or not out_path:
        print("ffuf-shim: 需要 -w 与 -o", file=sys.stderr)
        return 2

    try:
        with open(wordlist, encoding="utf-8", errors="replace") as fh:
            words = [ln.strip() for ln in fh
                     if ln.strip() and not ln.strip().startswith("#")]
    except OSError as exc:
        print(f"ffuf-shim: 读字典失败: {exc}", file=sys.stderr)
        return 2

    exts = [e.strip() for e in (ext_spec or "").split(",") if e.strip()]
    codes = _parse_codes(codes_spec)
    # -fc：状态码过滤（软 404 返回 200 时用它剔掉；空集=不过滤）
    filter_codes = _parse_codes(fc_spec) if (fc_spec or "").strip() else set()
    # -fr：响应体正则过滤（压掉 SPA 兜底文案噪声）
    filter_rx = None
    if (fr_spec or "").strip():
        try:
            filter_rx = re.compile(fr_spec.strip())
        except re.error as exc:
            print(f"ffuf-shim: -fr 正则非法: {exc}", file=sys.stderr)
            return 2
    need_body = filter_rx is not None

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))

    # 候选 = 原词 + 原词+扩展名（与 ffuf -e 的行为一致）
    candidates: list[tuple[str, str]] = []
    for w in words:
        candidates.append((w, target.replace("FUZZ", w)))
        for e in exts:
            candidates.append((w, target.replace("FUZZ", w + e)))

    baseline: tuple[int, int] | None = None
    if auto_cal:
        rnd = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
        probe_url = target.replace("FUZZ", rnd)
        st, ln, _ = _fetch(opener, probe_url, per_timeout)
        baseline = (st, ln)

    deadline = time.time() + maxtime
    results: list[dict] = []

    def probe(item: tuple[str, str]) -> dict | None:
        if time.time() > deadline:
            return None
        word, url = item
        status, length, body = _fetch(opener, url, per_timeout, need_body=need_body)
        if status == 0:
            return None
        if codes is not None and status not in codes:
            return None
        if filter_codes and status in filter_codes:
            return None
        if filter_size and length == filter_size:
            return None
        if filter_rx is not None and filter_rx.search(body):
            return None
        if baseline and (status, length) == baseline:
            return None  # 软 404 兜底页
        return {"input": {"FUZZ": word}, "status": status, "length": length,
                "words": 0, "url": url}

    with ThreadPoolExecutor(max_workers=max(1, min(threads, 30))) as pool:
        for res in pool.map(probe, candidates):
            if res:
                results.append(res)
            if time.time() > deadline:
                break

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"results": results}, fh, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))