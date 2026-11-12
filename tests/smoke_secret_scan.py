#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_secret_scan.py — 密钥扫描的 base64 盲区回归（审计 S6 补强）。

背景（2026-11-12 外部审计报告 S6）：旧规则只认「已知前缀形态」与「带引号的字面量赋值」，
把密钥 base64 之后贴进脚本/配置文件就完全漏检。补强版加了一条 **低噪音** 规则：

  对长度 ≥40 的 base64 候选块解码，只有解码结果里出现
    (a) 已知前缀（sk-/ghp_/AKIA/xox/JWT/私钥头），或
    (b) 赋值形态 `KEY=值`（且值不是占位符）
  才报。**不报**普通 base64（哈希、图片块、签名、随机二进制）——那会把真信号淹没。

本测试钉住：
  1) base64(ghp_ 令牌) → 命中 base64:github_pat，且预览打码（不出现完整令牌）；
  2) base64(赋值形态 token=…) → 命中 base64:literal_assignment；
  3) base64(随机二进制) / base64(无秘密文本) / 短 base64 → 不命中；
  4) 明文前缀令牌、明文赋值仍照旧命中（回归旧规则没被改坏）；
  5) 占位符赋值与 `secret-scan: allow` 豁免行不报；
  6) `--all` 扫当前仓库干净（真实仓库不该被新规则弄脏）。

运行: python tests/smoke_secret_scan.py
"""
from __future__ import annotations

import base64
import importlib.util
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
FAILS: list[str] = []
TMP = ROOT / "runs" / f"_t_secret_scan_{__import__('os').getpid()}"


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"[ok] {name}")
    else:
        FAILS.append(name)
        print(f"[FAIL] {name}" + (f" —— {detail}" if detail else ""))


def load_scanner():
    """tools/ 不是包，按路径加载（与 CI 调用方式一致）。"""
    path = ROOT / "tools" / "secret_scan.py"
    spec = importlib.util.spec_from_file_location("secret_scan_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    scanner = load_scanner()
    TMP.mkdir(parents=True, exist_ok=True)
    try:
        # ---------------- 正例：base64 包裹的密钥 ----------------
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
        b64_token = base64.b64encode(token.encode()).decode()
        p1 = TMP / "case_b64_token.txt"
        p1.write_text(f"payload = {b64_token}\n", encoding="utf-8")
        hits = scanner.scan_file(p1)
        check("base64 包裹的 GitHub 令牌被检出",
              any(h["type"] == "base64:github_pat" for h in hits), json.dumps(hits, ensure_ascii=False))
        check("base64 命中的预览已打码（不含完整令牌）",
              all(token not in h["preview"] for h in hits), json.dumps(hits, ensure_ascii=False))

        secret_text = "api_key=sk-abcdefghijklmnopqrstuvwx"
        b64_assign = base64.b64encode(secret_text.encode()).decode()
        p2 = TMP / "case_b64_assign.txt"
        p2.write_text(b64_assign + "\n", encoding="utf-8")
        hits2 = scanner.scan_file(p2)
        check("base64 包裹的赋值形态被检出",
              any(h["type"].startswith("base64:") for h in hits2), json.dumps(hits2, ensure_ascii=False))

        # ---------------- 负例：合法 base64 不该报 ----------------
        negatives = {
            "base64_random": base64.b64encode(bytes(range(256))).decode(),
            "base64_plain": base64.b64encode(b"hello world, this is just a test fixture blob").decode(),
            "base64_short": base64.b64encode(b"ghp_short").decode(),
        }
        lines = [f"{k} = {v}" for k, v in negatives.items()]
        p3 = TMP / "case_b64_negatives.txt"
        p3.write_text("\n".join(lines) + "\n", encoding="utf-8")
        hits3 = scanner.scan_file(p3)
        check("普通/短 base64 不误报", hits3 == [], json.dumps(hits3, ensure_ascii=False))

        # ---------------- 旧规则回归 ----------------
        p4 = TMP / "case_plain.txt"
        # 第二行故意用**无前缀**的随机串：它只能靠「赋值形态」规则抓到（前缀规则不匹配）
        p4.write_text(f"token = {token}\napi_key = \"AbCdEf0123456789XyZwVu\"\n", encoding="utf-8")
        hits4 = scanner.scan_file(p4)
        types = {h["type"] for h in hits4}
        check("明文前缀令牌仍命中", "github_pat" in types, str(types))
        check("明文赋值仍命中", any(t.startswith("literal_assignment:") for t in types), str(types))

        # ---------------- 豁免与占位符 ----------------
        p5 = TMP / "case_exempt.txt"
        p5.write_text(
            f"token = {token}  # secret-scan: allow\n"
            'api_key = "${API_KEY}"\n'
            'password = "changeme_please"\n',
            encoding="utf-8",
        )
        hits5 = scanner.scan_file(p5)
        check("secret-scan: allow / 占位符 / 环境变量取值不误报", hits5 == [],
              json.dumps(hits5, ensure_ascii=False))

        # ---------------- 仓库体检：新规则不能把真仓库弄脏 ----------------
        proc = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools" / "secret_scan.py"), "--all", "--json"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        try:
            report = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError:
            report = {}
        check("--all 扫仓库干净（新规则无已知误报）",
              proc.returncode == 0 and not report.get("findings"),
              f"rc={proc.returncode} findings={str(report.get('findings'))[:300]}")
        check("--all 确实扫到了文件", int(report.get("scanned") or 0) > 100, str(report.get("scanned")))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)

    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print("RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())