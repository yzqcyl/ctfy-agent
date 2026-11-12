# -*- coding: utf-8 -*-
"""提交前密钥扫描（P1-2）：只审计**将要提交的文件**，不靠 .gitignore 兜底。

为什么单独做一个脚本
--------------------
`.gitignore` 只决定"什么不会被提交"，不决定"提交的内容里有不有密钥"；
一次性人工扫描（11-03 那次）也不能防住下一次。这里把判断固化成可执行的一步：
默认扫 `git diff --cached` 的名单（= 真正要进仓库的文件），CI 里同一行命令复用。

误报的取舍（吃过亏）：第一版把 `os.environ.get("..._API_KEY")`、
`api_key=api_key` 这类**代码取值行**和文档里的反引号说明全判成密钥。
现在只认三类高置信信号：
  1. 已知前缀的密钥形态（OpenAI/GitHub/AWS/Slack/JWT/私钥头）；
  2. 带引号的字面量赋值，且值不是占位符、不是变量、不是测试桩；
  3. 私钥文件头（BEGIN ... PRIVATE KEY）。

用法：
    python tools/secret_scan.py --staged            # 只扫暂存区（提交前/CI 用）
    python tools/secret_scan.py src/ctfy_agent/x.py # 扫指定文件
    python tools/secret_scan.py --all               # 扫 git 跟踪的全部文件（体检用）
    python tools/secret_scan.py --staged --json

退出码：0=干净，1=命中（会打印 文件:行号 与命中类型，**值一律打码**）。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 1) 已知前缀形态
_HARD_PATTERNS = [
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("github_pat", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("github_fine_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws_akid", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
]

# 2) 带引号的字面量赋值：KEY/TOKEN/SECRET/PASSWORD = "...."
_ASSIGN = re.compile(
    r"(?i)\b([A-Za-z0-9_]*(?:api[_-]?key|token|secret|passwd|password)[A-Za-z0-9_]*)\b"
    r"\s*[:=]\s*"
    r"([\"'])([^\"'\n]{8,})\2"
)

# 2b) base64 包裹的密钥（2026-11-12 审计 S6 的补强）：
#     "base64 编码的 key / 无前缀随机串" 是旧规则盲区。但不能见 base64 就报——
#     仓库里大量合法 base64（测试夹具、PNG 块、签名），误报会淹没真信号。
#     只报「解码后命中已知前缀 _HARD_PATTERNS 或赋值形态 _ASSIGN」的候选。
_B64_CANDIDATE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _decode_b64(chunk: str) -> str | None:
    pad = "=" * (-len(chunk) % 4)
    try:
        raw = base64.b64decode(chunk + pad, validate=True)
    except Exception:  # noqa: BLE001 - 不是合法 base64 就跳过，不报错
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _scan_base64_tokens(line: str) -> list[tuple[str, str]]:
    """返回 [(type, masked_preview)]；命中即止（控制大文件里的解码成本）。"""
    hits: list[tuple[str, str]] = []
    for match in _B64_CANDIDATE.finditer(line):
        decoded = _decode_b64(match.group(0))
        if not decoded or len(decoded) < 8:
            continue
        for name, pattern in _HARD_PATTERNS:
            hit = pattern.search(decoded)
            if hit:
                hits.append((f"base64:{name}", "b64→" + mask(hit.group(0))))
                break
        else:
            for match2 in _ASSIGN.finditer(decoded):
                key, value = match2.group(1), match2.group(3)
                if _PLACEHOLDER.search(value) or value in _KNOWN_FAKE:
                    continue
                hits.append((f"base64:literal_assignment:{key}", "b64→" + mask(value)))
                break
        if hits:
            break
    return hits

# 值像"占位/引用/测试桩"就不算泄漏
_PLACEHOLDER = re.compile(
    r"(?i)^(\$\{|<|\{\{|%\(|os\.environ|environ\[|getenv|none|null|true|false|"
    r"x{3,}|\*{3,}|\.{3,}|dummy|example|placeholder|changeme|your[_-]?|test[_-]?(token|key|value)|"
    r"pf_test_token|abc123|localhost)"
)

# 出现在这些文件里、且值是这些字面量时跳过（测试用假凭据，改掉反而破坏用例）
_KNOWN_FAKE = {
    "pf_test_token_123456",
    "pf_demo_token",
    "dummy-token",
    "dummy-key",
    "test-token",
    "1234567890:TEST",
}

# 3) 整行跳过：代码取值 / 文档说明 / 已打码
_SKIP_LINE = re.compile(
    r"(os\.environ|getenv|mask_token|masked\(|\*\*\*|<redacted|示例|占位|placeholder|不要提交|勿提交)"
)

MAX_BYTES = 4 * 1024 * 1024
BINARY_HINT = re.compile(rb"[\x00\x01\x02\x03]")
# 测试夹具天然含假凭据（smoke_p1 的 sk-test、AI 夹具的 ASI-xxx、JWT 样例），
# 默认排除 tests/ 以免噪音淹没真信号；要连测试一起审就加 --no-exclude。
DEFAULT_EXCLUDE = ("tests/",)
ALLOW_MARK = "secret-scan: allow"


def mask(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-2:]}"


def git_files(*args: str) -> list[Path]:
    proc = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(f"git {' '.join(args)} 失败：{(proc.stderr or '').strip()}", file=sys.stderr)
        return []
    out: list[Path] = []
    for line in proc.stdout.splitlines():
        name = line.strip()
        if not name:
            continue
        path = ROOT / name
        if path.is_file():
            out.append(path)
    return out


def rel(path: Path) -> str:
    """仓库内文件用相对路径，仓库外的原样输出（不抛 ValueError）。"""
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def scan_file(path: Path) -> list[dict]:
    findings: list[dict] = []
    try:
        raw = path.read_bytes()[:MAX_BYTES]
    except OSError:
        return findings
    if BINARY_HINT.search(raw):
        return findings  # 二进制/压缩包不在这里判（人工 review 或忽略）
    text = raw.decode("utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARK in line:  # 显式豁免：确认是假值后在这一行写 "secret-scan: allow"
            continue
        if len(line) > 4000:  # 压缩后的单行 JS 之类，长度爆炸且噪音极大
            line = line[:4000]
        if _SKIP_LINE.search(line):
            continue
        for name, pattern in _HARD_PATTERNS:
            match = pattern.search(line)
            if match:
                findings.append({"file": rel(path), "line": lineno, "type": name,
                                 "preview": mask(match.group(0))})
        for match in _ASSIGN.finditer(line):
            key, value = match.group(1), match.group(3)
            if _PLACEHOLDER.search(value) or value in _KNOWN_FAKE:
                continue
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value) and value.lower() == key.lower():
                continue  # key = key 这种自赋值
            findings.append({"file": rel(path), "line": lineno,
                             "type": f"literal_assignment:{key}", "preview": mask(value)})
        if _B64_CANDIDATE.search(line):
            for kind, preview in _scan_base64_tokens(line):
                findings.append({"file": rel(path), "line": lineno, "type": kind, "preview": preview})
    return findings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="提交前密钥扫描（只审计将要提交的文件）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="扫暂存区（提交前/CI 用）")
    group.add_argument("--all", action="store_true", help="扫 git 跟踪的全部文件")
    parser.add_argument("paths", nargs="*", help="显式指定文件（与 --staged/--all 二选一）")
    parser.add_argument("--json", action="store_true", help="机读输出")
    parser.add_argument("--exclude", nargs="*", default=list(DEFAULT_EXCLUDE),
                        help=f"跳过这些前缀（默认 {list(DEFAULT_EXCLUDE)}）")
    parser.add_argument("--no-exclude", action="store_true", help="不跳过任何路径（连测试一起审）")
    args = parser.parse_args(argv)

    if args.staged:
        targets = git_files("diff", "--cached", "--name-only", "--diff-filter=ACM")
        scope = "暂存区"
    elif args.all:
        targets = git_files("ls-files")
        scope = "全部跟踪文件"
    elif args.paths:
        targets = [Path(p) if Path(p).is_absolute() else ROOT / p for p in args.paths]
        scope = f"{len(targets)} 个指定文件"
    else:
        print("请给 --staged / --all 或文件列表（默认不扫，避免误以为已经检查过）", file=sys.stderr)
        return 2

    findings: list[dict] = []
    excludes = [] if args.no_exclude else [str(x) for x in (args.exclude or []) if str(x)]
    scanned = 0
    for path in targets:
        name = rel(path)
        if any(name.replace("\\", "/").startswith(prefix.replace("\\", "/")) for prefix in excludes):
            continue
        scanned += 1
        findings.extend(scan_file(path))

    if args.json:
        print(json.dumps({"scope": scope, "scanned": scanned, "excluded": excludes,
                          "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(f"== 密钥扫描：{scope}，实扫 {scanned} 个文件（跳过 {len(excludes)} 类前缀）==")
        if not findings:
            print("干净 ✓（无高置信密钥形态）")
        else:
            for item in findings:
                print(f"  {item['file']}:{item['line']}  [{item['type']}]  {item['preview']}")
            print(f"\n命中 {len(findings)} 处：确认是误报就改写成占位符/环境变量取值，别直接提交。",
                  file=sys.stderr)
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
