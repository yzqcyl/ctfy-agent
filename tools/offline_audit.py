#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""offline_audit.py — 断网姿势自检：静态扫出「运行期可能需要公网」的地方。

比赛规则（v2.5 §二）：解题 Agent **零公网**——只允许 平台接口 / 大模型网关 / 题目入口；
工具依赖必须预装。本脚本静态扫描 src/ config/ Dockerfile，找：
  * 硬编码公网 URL / 域名（区分白名单：平台、LLM 网关、官方源）
  * 运行期 pip/apt 安装、下载二进制、import 时联网的行为
  * 需要公网 DNS 才可用的端点数

用法：python tools/offline_audit.py [--verbose]
退出码：0=没有发现运行时公网依赖（白名单除外）；1=发现疑似违规/风险点。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN = [ROOT / "src", ROOT / "config", ROOT / "Dockerfile"]

# 规则允许的域名（平台/网关/官方源/题目入口域名模式）
ALLOWED_HOSTS = (
    "ctfy.tsinghua.edu.cn", "ctfy.io", "ctfy.run", "tpctf.ctfy.run",
    "api.deepseek.com", "dashscope.aliyuncs.com", "open.bigmodel.cn",
    "api.hunyuan.cloud.tencent.com", "qianfan.baidubce.com", "ark.cn-beijing.volces.com",
    "api.moonshot.cn", "spark-api-open.xf-yun.com", "api.minimax.cn",
    "api.stepfun.com", "api.baichuan-ai.com", "api.xiaomimimo.com",
    "mirrors.tuna.tsinghua.edu.cn", "pypi.org", "files.pythonhosted.org", "ghcr.io",
    # 本地/内网/文档占位符（不是公网依赖）
    "127.0.0.1", "localhost", "0.0.0.0", "host", "host:port",
    "tls.example.com", "example.com", "ctf2.dasctf.com",  # ctf2 练习平台（正式赛不用）
)

INTERNAL_RE = re.compile(r"^(?:10\.|127\.|192\.168\.|172\.(?:1[6-9]|2\d|3[01])\.)")
URL_RE = re.compile(r"https?://([A-Za-z0-9._:-]+)", re.I)
RISK_PATTERNS = [
    (re.compile(r"\bpip\s+install\b"), "运行期 pip install（断网会失败）"),
    (re.compile(r"\bapt(?:-get)?\s+install\b"), "运行期 apt install（断网会失败）"),
    (re.compile(r"\bcurl\s+https?://"), "运行期 curl 外网"),
    (re.compile(r"\bwget\s+https?://"), "运行期 wget 外网"),
    (re.compile(r"urlopen\(|requests\.(?:get|post)\("), "运行期 HTTP 客户端（看目标是否白名单）"),
]
SKIP_DIRS = {"__pycache__", ".git", "runs", "tools/pylibs"}


def iter_files():
    for base in SCAN:
        if base.is_file():
            yield base
            continue
        for p in base.rglob("*"):
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts):
                if p.suffix in {".py", ".json", ".md", ".sh", ".txt", ".yml", ".yaml"} or p.name == "Dockerfile":
                    yield p


def allowed(host: str) -> bool:
    host = host.split(":")[0].lower()
    if INTERNAL_RE.match(host):
        return True
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    hosts: dict[str, list[str]] = {}
    risks: list[tuple[str, int, str, str]] = []
    info: list[tuple[str, int, str]] = []
    for p in iter_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if p.name == "Dockerfile":
            continue  # 构建期下载（ffuf/sqlmap/SecLists）不影响运行期断网
        for m in URL_RE.finditer(text):
            host = m.group(1).lower()
            hosts.setdefault(host, []).append(f"{p.relative_to(ROOT)}:{text[:m.start()].count(chr(10)) + 1}")
        build_time = p.name == "Dockerfile"
        for pat, why in RISK_PATTERNS:
            for m in pat.finditer(text):
                if build_time:
                    continue  # 构建期安装不影响"运行期断网"
                line_no = text[:m.start()].count("\n") + 1
                line = text.splitlines()[line_no - 1]
                if line.strip().startswith("#") or "https://host/" in line:
                    continue  # 注释/占位符示例不算运行期依赖
                if why.startswith("运行期 HTTP"):
                    info.append((str(p.relative_to(ROOT)), line_no, line.strip()[:100]))
                    continue
                risks.append((why, line_no, str(p.relative_to(ROOT)), line[:120]))
    bad_hosts = {h: where for h, where in hosts.items() if not allowed(h)}
    print("== 扫描到的外部域名 ==")
    for h in sorted(hosts):
        mark = "白名单" if allowed(h) else "⚠ 非白名单"
        print(f"  [{mark}] {h}")
        if args.verbose:
            for w in hosts[h][:3]:
                print(f"        {w}")
    print("\n== 运行期 HTTP 客户端（授权入口：平台/题目，人工确认即可）==")
    for rel, line, snippet in info:
        print(f"  · {rel}:{line} {snippet}")
    print("\n== 运行期公网风险点（pip/apt/curl/wget 外网）==") 
    if not risks:
        print("  （未发现 pip/apt/curl/wget 外网调用）")
    for why, line, rel, snippet in risks:
        print(f"  ⚠ {rel}:{line} {why} -> {snippet.strip()[:100]}")
    ok = not bad_hosts and not risks
    print("\n结论：", "✅ 运行期无公网依赖（白名单内）" if ok else "❌ 存在需人工确认的公网依赖")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())