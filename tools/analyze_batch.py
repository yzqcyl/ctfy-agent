#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze_batch.py — 复盘一批平台题：逐题算耗时/预算/工具失败/循环浪费/环境坑，输出 markdown。

用法：
    python tools/analyze_batch.py runs/batch-easy-2026-11-18
    python tools/analyze_batch.py runs/batch-easy-2026-11-18 --json out.json

关注的是「agent 的思考与工具链哪里卡住」，不是平台排名：
* 工具软失败 / 非零退出 / 重复同一动作（绕圈）
* Windows/Linux 习惯错配（python3、tee、/bin/sh、路径分隔符…）
* 预算撞墙（时间/步数/token）
* 最终是否解出（state.json 的 submitted_ok / flag_value）
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 环境坑模式表。
# 跨平台习惯差异（python3/tee//bin/sh）只在 Windows 宿主跑批时才算环境坑；
# Linux 容器里它们是正常命令，计进去会淹没真实问题（2026-11-19 批跑实测误报 700+ 条）。
TOOL_NOISE: list[tuple[re.Pattern[str], str]] = []
if os.name == "nt":
    TOOL_NOISE += [
        (re.compile(r"python3"), "用 python3（本机是 Windows，可能没有该命令）"),
        (re.compile(r"\btee\b"), "用 tee（Windows 无此命令）"),
        (re.compile(r"/bin/(?:sh|bash)"), "用 /bin/sh 或 /bin/bash（Windows 无）"),
    ]
TOOL_NOISE += [
    (re.compile(r"command not found|不是内部或外部命令|无法将.*项识别为"), "命令不存在"),
    (re.compile(r"Permission denied|拒绝访问"), "权限被拒"),
    (re.compile(r"UnicodeEncodeError|codec can't encode|gbk"), "控制台编码坑"),
    (re.compile(r"SSL|CERTIFICATE_VERIFY_FAILED|EOF occurred in violation"), "TLS/连接层失败"),
    (re.compile(r"timeout|timed out|超时"), "超时"),
    (re.compile(r"no such file|找不到文件|FileNotFoundError"), "文件不存在"),
]


def load_jsonl(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return out


def analyze(run_dir: Path) -> dict:
    run_dir = run_dir.resolve()
    state = {}
    st = run_dir / "state.json"
    if st.exists():
        try:
            state = json.loads(st.read_text(encoding="utf-8-sig"))
        except ValueError:
            state = {}
    challenges = state.get("challenges") or {}
    manifest = {}
    mf = run_dir / "manifest.json"
    if mf.exists():
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8-sig"))
        except ValueError:
            manifest = {}
    order = [c.get("id") for c in manifest.get("challenges") or []]
    ids = [i for i in order if i] + [i for i in challenges if i not in order]
    rows = []
    for cid in ids:
        ch = challenges.get(cid) or {}
        recs = load_jsonl(run_dir / "transcripts" / f"{cid}.jsonl")
        ats = [r.get("at") for r in recs if r.get("at")]
        tools = Counter(r.get("tool") for r in recs if r.get("tool"))
        soft = [r for r in recs if r.get("tool") and "isError" in str(r.get("result") or "")]
        exit_bad = [r for r in recs if r.get("tool") == "shell"
                    and '"exit_code": 0' not in str(r.get("result") or "")]
        # 同一动作重复
        sig = Counter()
        for r in recs:
            if r.get("tool"):
                sig[(r.get("tool"), str(r.get("args") or "")[:400])] += 1
        repeated = {k: v for k, v in sig.items() if v >= 3}
        text = "\n".join(str(r.get("result") or "") + "\n" + str(r.get("content") or "") for r in recs)
        noise = Counter()
        for rx, label in TOOL_NOISE:
            n = len(rx.findall(text))
            if n:
                noise[label] += n
        solved = bool(ch.get("submitted_ok")) or bool(ch.get("flag_value"))
        rows.append({
            "id": cid,
            "name": ch.get("name") or cid,
            "category": ch.get("category"),
            "status": ch.get("status"),
            "solved": solved,
            "flag": str(ch.get("flag_value") or "")[:100],
            "records": len(recs),
            "first_at": ats[0] if ats else "",
            "last_at": ats[-1] if ats else "",
            "steps": (ch.get("steps") or ch.get("attempts")),
            "tool_calls": dict(tools),
            "soft_fail": len(soft),
            "shell_nonzero": len(exit_bad),
            "repeated_actions": len(repeated),
            "env_issues": dict(noise),
        })
    return {"run_dir": str(run_dir), "rows": rows}


def render(res: dict) -> str:
    lines = [f"# 批次复盘：{Path(res['run_dir']).name}", ""]
    if not res["rows"]:
        lines.append("（没有转录/状态数据）")
        return "\n".join(lines)
    lines += ["| 题 | 类别 | 结果 | 记录 | 工具调用 | 软失败 | shell非零 | 重复动作 | 环境坑 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in res["rows"]:
        result = f"解出 {r['flag']}" if r["solved"] else (r["status"] or "?")
        env = "；".join(f"{k}×{v}" for k, v in (r["env_issues"] or {}).items()) or "-"
        tools = "、".join(f"{k}×{v}" for k, v in sorted((r["tool_calls"] or {}).items(),
                                                        key=lambda kv: -kv[1]))[:60]
        lines.append(f"| {r['name']} | {r['category']} | {result} | {r['records']} | "
                     f"{tools} | {r['soft_fail']} | {r['shell_nonzero']} | "
                     f"{r['repeated_actions']} | {env} |")
    # 汇总
    total = len(res["rows"])
    solved = sum(1 for r in res["rows"] if r["solved"])
    soft = sum(r["soft_fail"] for r in res["rows"])
    nz = sum(r["shell_nonzero"] for r in res["rows"])
    env = Counter()
    for r in res["rows"]:
        env.update(r["env_issues"] or {})
    lines += ["", f"**汇总**：{solved}/{total} 解出；工具软失败 {soft} 次；shell 非零退出 {nz} 次；"
                  f"环境坑命中 {dict(env) if env else '无'}"]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print("用法: python tools/analyze_batch.py runs/<run-id> [--json out.json]", file=sys.stderr)
        return 2
    run_dir = Path(argv[0])
    res = analyze(run_dir)
    md = render(res)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print(md)
    if "--json" in argv:
        out = Path(argv[argv.index("--json") + 1])
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON -> {out}")
    (run_dir / "analysis.md").write_text(md + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))