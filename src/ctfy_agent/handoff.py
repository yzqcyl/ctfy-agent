#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单题交接卡（P2-2）：一轮跑完就把"下一棒能直接接上"的信息落成 markdown。

为什么值得单独一个产物
----------------------
失败题的默认结局是被下一轮/下一个人从零重来。实战里最贵的两件事都是这个造成的：
重复走已被否证的路径、以及"知道卡在哪但没说"。数据其实**已经都在内存里**——
`crew.solve` 返回的 summary 带 `facts`（已确证）、`dead_ends`（已否证）、`candidates`、
`tail_action`——只差把它们渲染成人能读、下一棒能照做的五段。

刻意只做渲染：不新增判断、不写提示词、不改编排。写盘失败也只是少一个产物，绝不影响 run。
"""

from __future__ import annotations

from pathlib import Path

MAX_ITEMS = 12          # 每段最多列几条（多了没人看，且会把文件撑得比转录还长）
MAX_CHARS = 400         # 单条上限


def _clip(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"


def _bullets(items, empty: str) -> list[str]:
    rows = [_clip(x) for x in (items or []) if str(x or "").strip()]
    if not rows:
        return [f"- {empty}"]
    return [f"- {row}" for row in rows[:MAX_ITEMS]]


def render(challenge, summary: dict) -> str:
    """把一轮结果渲染成五要素交接卡（纯函数，便于单测）。"""
    cid = getattr(challenge, "id", "") or summary.get("challenge_id", "")
    name = getattr(challenge, "name", "")
    facets = ""
    if hasattr(challenge, "facets"):
        try:
            facets = challenge.facets()
        except Exception:  # noqa: BLE001 - 渲染失败不该影响交接卡本身
            facets = ""
    reason = str(summary.get("reason") or "unknown")
    tail = str(summary.get("tail_action") or "")
    facts = summary.get("facts") or []
    dead_ends = summary.get("dead_ends") or []
    candidates = summary.get("candidates") or []
    transcript = str(summary.get("transcript") or "")
    budget = summary.get("budget") or {}
    plan = summary.get("plan") or {}

    lines: list[str] = [
        f"# 交接卡 {cid}{('（' + name + '）') if name else ''}",
        "",
        f"- 结论：{'已解出' if summary.get('submitted') else '未解出'}；停止原因 `{reason}`；"
        f"最后动作 `{tail or '无'}`",
    ]
    if facets:
        lines.append(f"- 题型线索：{facets}")
    lines += [
        f"- 用量：{budget.get('total_tokens', 0)} token / {budget.get('calls', 0)} 次模型调用"
        + (f"；计划变动 {plan.get('changes')} 次" if plan.get("changes") else ""),
        f"- 转录：{transcript}",
        "",
        "## 1. 已确证事实",
        "",
        *_bullets(facts, "（本轮没有可复用的确定结论）"),
        "",
        "## 2. 已否证路径（别重复）",
        "",
        *_bullets(dead_ends, "（没有记录到被证伪的方向）"),
        "",
        "## 3. 精确卡点",
        "",
        f"- {_clip(f'停在 `{tail or reason}`；reason={reason}')}",
        "",
        "## 4. 下一步建议",
        "",
    ]
    if summary.get("submitted"):
        lines.append("- 本题已过，无需接续；如果平台报的是部分解，重点看未通过的题号。")
    elif candidates:
        lines.append(f"- 先验证已有候选（不要重新挖）：{' / '.join(_clip(x) for x in candidates[:3])}")
    elif dead_ends:
        lines.append("- 上一轮的方向已被证伪，换一条攻击面（改看别的入口，而不是重跑同一段脚本）。")
    else:
        lines.append("- 没有可复用结论：从题面/附件重新枚举入口，并按已知题型线索优先试对应套路。")
    lines += [
        "",
        "## 5. 产物路径",
        "",
        f"- 转录（逐事件，统计以行为单位）：{transcript}",
        f"- 运行目录：{getattr(challenge, 'id', cid)} 所属 run 目录下的 transcripts/ 与 checkpoints/",
        "",
        "> 本卡由 `ctfy_agent.handoff` 自动生成，数据源是同一轮的 summary，不含模型自由文本。",
        "",
    ]
    return "\n".join(lines)


def write(run_dir, challenge, summary: dict) -> Path | None:
    """写到 `<run_dir>/handoff/<challenge_id>.md`。任何异常都吞掉并返回 None。"""
    try:
        cid = str(getattr(challenge, "id", "") or summary.get("challenge_id") or "unknown")
        safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "_" for ch in cid) or "unknown"
        target_dir = Path(run_dir) / "handoff"
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{safe}.md"
        path.write_text(render(challenge, summary), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 - 交接卡是附加产物，失败不能拖累 run
        return None
