#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_traffic_redact.py — 流量库敏感信息脱敏的回归（离线、不连平台）。

背景（docs/audit-code-2026-11-12.md S1）：流量库把请求/响应头原样落盘到
`runs/traffic/<id>.sqlite`（gitignored，但明文留在本地磁盘）。`http_request` 会把模型给的
Authorization/Cookie 原样转发，于是靶机凭据/会话令牌会明文留存，复盘、交接、误打包时是实质泄露面。

离线钉住：
  1) 认证类头（Authorization/Cookie/Set-Cookie/X-Api-Key/token/password…）值被替换成
     `<redacted sha256:xxxxxxxx len=N>`，指纹 = SHA256(原值) 前 8 位（可对账"是不是同一个令牌"）；
  2) 普通头（Host/Content-Type/User-Agent…）一字不动，dict 键名与顺序保留；
  3) URL 与 query 列里凭据类参数被脱敏，普通参数不动；
  4) 正文 body 保持原文（已知取舍：正文是解题证据）；
  5) 检索仍可用（按 path/普通头检索命中）。

运行: python tests/smoke_traffic_redact.py
"""
from __future__ import annotations

import hashlib
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import traffic as tmod  # noqa: E402

FAILS: list[str] = []
TMP = ROOT / "runs" / f"_t_traffic_redact_{__import__('os').getpid()}"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def fp(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]


def main() -> int:
    try:
        store = tmod.resolve(TMP, "web-redact")
        token = "Bearer eyJhbGciOiJIUzI1NiJ9.SECRET.SIG"
        cookie = "session=abc123def456; csrf=zzz"
        api_key = "sk-live-1234567890"
        query_token = "tok_QUERY_SECRET_999"

        store.record(
            source="http_request", method="GET",
            url=f"http://127.0.0.1:8080/admin?token={query_token}&page=2&api_key={api_key}",
            status=200,
            req_headers={
                "Authorization": token,
                "Cookie": cookie,
                "Host": "127.0.0.1:8080",
                "User-Agent": "ctfy-test/1.0",
                "X-Api-Key": api_key,
            },
            req_body="user=a&pass=b",
            resp_headers={
                "Set-Cookie": "sid=deadbeefcafe; HttpOnly",
                "Content-Type": "application/json",
            },
            resp_body='{"ok":true}',
        )

        with store._lock:
            row = store._conn.execute(
                "SELECT url, path, query, req_headers, resp_headers FROM exchanges"
            ).fetchone()
        url, path, query, req_headers, resp_headers = row

        # 1) 认证类头 → 指纹；普通头不动
        check("Authorization 已脱敏", "<redacted sha256:" in req_headers and token not in req_headers,
              req_headers)
        check("Cookie 已脱敏", cookie not in req_headers, req_headers)
        check("X-Api-Key 已脱敏", api_key not in req_headers, req_headers)
        check("Set-Cookie 已脱敏", "deadbeefcafe" not in resp_headers, resp_headers)
        check("普通请求头原样", '"Host": "127.0.0.1:8080"' in req_headers
              and "ctfy-test/1.0" in req_headers, req_headers)
        check("普通响应头原样", "application/json" in resp_headers, resp_headers)

        # 2) 指纹可对账 + 长度保留（Authorization 老格式 "Bearer <token>" 建议看 sha256 前 8 位）
        check("Authorization 指纹 = sha256 前 8 位", fp(token) in req_headers, fp(token))
        check("长度信息保留", f"len={len(token)}" in req_headers, req_headers)

        # 3) URL/query 脱敏，普通参数保留
        check("query 里 token 已脱敏", query_token not in query and query_token not in url, query)
        check("query 里 api_key 已脱敏", api_key not in query, query)
        check("普通 query 参数保留", "page=2" in query, query)
        check("path 不受影响", path == "/admin", path)

        # 4) 正文不动
        with store._lock:
            blob_rows = store._conn.execute(
                "SELECT resp_body_blob, req_body_blob FROM exchanges"
            ).fetchone()
        resp_body = store._read_blob(blob_rows[0])
        req_body = store._read_blob(blob_rows[1])
        check("响应正文保持原文", resp_body == '{"ok":true}', repr(resp_body))
        check("请求正文保持原文", req_body == "user=a&pass=b", repr(req_body))

        # 5) 检索仍可用（按 path 与普通头）
        res = store.search("admin")
        check("按 path 检索仍命中",
              any("admin" in str(h.get("path") or "") for h in (res.get("shown") or [])),
              str(res)[:200])
        res2 = store.search("ctfy-test")
        check("按普通头检索仍命中", int(res2.get("total") or 0) >= 1, str(res2)[:200])

        # 6) 纯函数边界：空/None 不炸，非敏感名不动
        check("redact_headers(None) → None", tmod.redact_headers(None) is None)
        check("空 dict 原样", tmod.redact_headers({}) == {})
        check("redact_query 空串", tmod.redact_query("") == "")
        check("非凭据参数不动", tmod.redact_query("a=1&b=2") == "a=1&b=2")
        check("SQL 注入式 query 不误伤", tmod.redact_query("q=1%27+OR+1%3D1") == "q=1%27+OR+1%3D1")
        store.close()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    if FAILS:
        print(f"\nsmoke_traffic_redact 失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("\nsmoke_traffic_redact 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())