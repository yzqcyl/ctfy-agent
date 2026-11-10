# -*- coding: utf-8 -*-
"""从一次离线解题的落盘产物（state.json + transcript jsonl）生成**可复现的解题思路**。

只保留「重要进展」：题目/目标 + 每轮的协调者思路（PLAN）+ 专家结论（report，模型自述的
完整攻击链，含精确命令与载荷）+ 最终 flag。不做原始日志搬运，让看的人能照着复现。
纯增量文件，不依赖平台，不 import 时无副作用。

提供两个入口：
  build_writeup_blocks(run_dir) -> list[tuple]   结构化块（GUI 富文本渲染用）
  build_writeup(run_dir) -> str                 纯文本（CLI / writeup.txt / report.md 用）
两种形式内容一致，只是渲染方式不同。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_URL_RE = re.compile(r"https?://[^\s\"'<>，；;]+")
_FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,}\{[^}\s:]{4,120}\}")


def _strip_label(s: str, label: str) -> str:
    s = (s or "").strip()
    for pfx in (f"{label}:", f"{label}：", f"{label} "):
        if s.startswith(pfx):
            s = s[len(pfx):].strip()
            break
    return s


def _first_url(text: str) -> str:
    m = _URL_RE.search(text or "")
    return m.group(0).rstrip(".") if m else ""


def _submit_flag(lines: list[dict]) -> str:
    for rec in lines:
        if rec.get("tool") == "submit":
            try:
                args = rec.get("args")
                if isinstance(args, str):
                    args = json.loads(args)
                ans = (args or {}).get("answer")
                if ans:
                    return str(ans)
            except Exception:  # noqa: BLE001
                continue
    return ""


def _extract(run_dir: Path, challenge_id: str | None = None) -> dict:
    """读 state + 转录，抽题目/目标/状态/flag/思路/结论。目录不完整时返回 {"message": ...}。

    challenge_id 给定时只取该题（多题共用一个 run 目录时按题生成，省时间也省 UI 噪声）。
    """
    run_dir = Path(run_dir)
    if challenge_id is not None:
        # 防御：challenge_id 会被拼进文件名（transcripts/<cid>.jsonl），不能是任意路径
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(challenge_id)):
            return {"message": "(题目 id 含非法字符，已拒绝)"}
    state_path = run_dir / "state.json"
    if not state_path.exists():
        return {"message": f"(该任务目录 {run_dir.name} 无 state.json，可能未跑完或被删除)"}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"message": "(state.json 读取失败)"}

    challenges = state.get("challenges") or {}
    if challenge_id:
        ch = challenges.get(challenge_id) or {}
        if not ch:
            return {"message": f"(该运行里没有题目 {challenge_id})"}
    else:
        chs = list(challenges.values())
        ch = chs[0] if chs else {}
    name = ch.get("name") or run_dir.name
    category = ch.get("category") or "?"
    difficulty = ch.get("difficulty") or "?"
    status = ch.get("status") or "?"
    flag = ch.get("flag_value") or ""
    tokens = (ch.get("tokens") or {}).get("total_tokens") or 0
    desc = ch.get("description") or ""
    target = _first_url(desc)

    tdir = run_dir / "transcripts"
    lines: list[dict] = []
    if tdir.is_dir():
        files = [tdir / f"{challenge_id}.jsonl"] if challenge_id else sorted(tdir.glob("*.jsonl"))
        for fp in files:
            if not fp.is_file():
                continue
            try:
                raw = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            for ln in raw.splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    lines.append(json.loads(ln))
                except Exception:  # noqa: BLE001
                    continue

    plans: list[str] = []
    reports: list[str] = []
    seen_p: set[str] = set()
    seen_r: set[str] = set()
    for rec in lines:
        # 思路只取「协调者」每轮的 PLAN（轮级策略），跳过专家逐步微计划与格式提醒
        if rec.get("role") == "coordinator" and rec.get("plan"):
            p = _strip_label(str(rec["plan"]), "PLAN")
            if p and p not in seen_p:
                seen_p.add(p)
                plans.append(p)
        if rec.get("tool") == "report" and rec.get("result"):
            rep = str(rec["result"]).strip()
            evi = rep.find("[evidence")  # 剥掉结尾注入的原始工具输出，只留干净自述
            if evi > 0:
                rep = rep[:evi].rstrip()
            if rep and rep not in seen_r:
                seen_r.add(rep)
                reports.append(rep)

    if not flag:
        flag = _submit_flag(lines) or ""
    if not flag:
        for rec in lines:
            for k in ("result", "content", "note", "report"):
                m = _FLAG_RE.search(str(rec.get(k) or ""))
                if m:
                    flag = m.group(0)
                    break
            if flag:
                break

    return {
        "name": name, "category": category, "difficulty": difficulty,
        "status": status, "flag": flag, "tokens": tokens, "target": target,
        "plans": plans, "reports": reports,
    }


def build_writeup_blocks(run_dir: Path, challenge_id: str | None = None) -> list[tuple]:
    """结构化块，供 GUI 富文本渲染。每块 (kind, ...)：
      ("meta", 标签, 值)   ("section", 标题)   ("plan", 轮次, 文本)
      ("report", 序号, 文本)   ("flag", 值)    ("plain", 文本)
    """
    d = _extract(run_dir, challenge_id)
    if "message" in d:
        return [("plain", d["message"])]

    blocks: list[tuple] = [("meta", "题目", f"{d['name']}（{d['category']} / {d['difficulty']}）")]
    if d["target"]:
        blocks.append(("meta", "目标", d["target"]))
    cost = f"{d['tokens']:,}" if isinstance(d["tokens"], int) else str(d["tokens"])
    blocks.append(("meta", "状态", f"{d['status']} · 花费 {cost} token"))

    if d["plans"]:
        blocks.append(("section", "解题思路"))
        for i, p in enumerate(d["plans"], 1):
            blocks.append(("plan", f"第{i}轮", p))
    if d["reports"]:
        blocks.append(("section", "关键进展（可复现步骤）"))
        for i, r in enumerate(d["reports"], 1):
            blocks.append(("report", f"【{i}】", r))
    if not d["plans"] and not d["reports"]:
        blocks.append(("plain", "（尚未产生结论，可能仍在解题或已中断）"))
    if d["flag"]:
        blocks.append(("section", "Flag"))
        blocks.append(("flag", d["flag"]))
    return blocks


def build_writeup(run_dir: Path, challenge_id: str | None = None) -> str:
    """纯文本写稿（与结构化块同源，内容一致）。"""
    out: list[str] = []
    for blk in build_writeup_blocks(run_dir, challenge_id):
        kind = blk[0]
        if kind == "meta":
            out.append(f"{blk[1]}  {blk[2]}")
        elif kind == "section":
            if out and out[-1] != "":
                out.append("")
            out.append(f"═══ {blk[1]} ═══")
        elif kind == "plan":
            out.append(f"{blk[1]}> {blk[2]}")
        elif kind == "report":
            out.append(f"{blk[1]} {blk[2]}")
            out.append("")
        elif kind == "flag":
            out.append(blk[1])
        elif kind == "plain":
            out.append(blk[1])
    return "\n".join(out).strip()


def write_writeup_file(run_dir: Path, challenge_id: str | None = None) -> Path:
    """把写稿落到 runs/<run-id>/writeup.txt（按题生成时叫 writeup-<cid>.txt）。"""
    suffix = f"-{challenge_id}" if challenge_id else ""
    out = Path(run_dir) / f"writeup{suffix}.txt"
    out.write_text(build_writeup(run_dir, challenge_id) + "\n", encoding="utf-8")
    return out