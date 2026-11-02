#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提示词装配自检：改 config/agent.json 的 rev/pwn system 之后跑这个。

设计意图
--------
提示词是"活的"：模型实际收到的 system = 正文 + 若干条按**关键词子串**条件追加的规则
（crew.py::_role_system）。所以改正文可能**静默**改掉追加结果，而单测不会红。

本脚本把这件事变成可判定的 7 项检查，任何一项失败就退出码 1：

  1. config/agent.json 是合法 JSON
  2. roles.rev.system / roles.pwn.system 存在且非空
  3. 正文**不含** "PLAN"（含了 COMPANION_RULE 就不追加，PLAN/REFLECTION 格式消失）
  4. 正文**含** "evidence" 与 "report"（缺了会触发那两条规则重复追加）
  5. 正文长度在合理区间（防止无意中删掉一大段或堆到模型跳读）
  6. 提到的 view= 值都在 elfpeek 的 enum 内；容器里没有的工具名不能以肯定语气出现
  7. 拼装后的实际 system 里，PLAN:/REFLECTION:/不可达纪律 三条都还在

用法
----
    python tools/check_prompt_assembly.py            # 检查（默认配置）
    python tools/check_prompt_assembly.py --show     # 连拼装结果一起打印
    python tools/check_prompt_assembly.py --json     # 机器可读

退出码：0 全绿；1 有失败；2 环境问题（找不到 config 或 src）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "agent.json"

ROLES = ("rev", "pwn")
LEN_RANGE = (1900, 2600)

# 容器里确实没有的东西：出现在提示词里时，必须是"没有/无/别用"这类否定语境，
# 否则就是在骗模型（这正是 capstone 那类事故的成因）。
ABSENT_TOOLS = (
    "gdb", "objdump", "readelf", "radare2", "ROPgadget", "angr", "z3",
    "pyelftools", "binwalk", "PIL", "nmap", "nc", "xxd", "binutils",
)
NEGATIONS = ("没有", "无", "不含", "别用", "不要用", "缺", "未装", "没装", "不带")


def _fail(msg: str) -> tuple[bool, str]:
    return False, msg


def _ok(msg: str) -> tuple[bool, str]:
    return True, msg


def load_config(path: Path | None = None) -> dict:
    with (path or CONFIG).open(encoding="utf-8") as fh:
        return json.load(fh)


def elfpeek_enum() -> list[str]:
    """从代码里取 elfpeek 的 view 枚举（不在脚本里抄一份，避免两处漂移）。"""
    src = ROOT / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from ctfy_agent.tools_domain_ext import ELFPEEK_SPEC  # noqa: WPS433

        params = ELFPEEK_SPEC["function"]["parameters"]["properties"]
        return list(params["view"]["enum"])
    except Exception:
        return []


def assemble(role: str, text: str) -> str:
    """复刻 crew._role_system 的追加逻辑（只看 override 那一支）。"""
    out = text
    if "PLAN" not in out:
        out += (
            "\n输出格式：每次回复先在文本里写两行标签，再调用工具——\n"
            "PLAN: 当前计划，一句话；计划没变就原样复制上一轮那句，不要改措辞。\n"
            "REFLECTION: 上一步结果说明什么、这一步为什么这么做，一句话。"
        )
    if "evidence" not in out.lower():
        out += (
            "\n结论纪律：交付结论时，evidence 字段必须从某一次工具输出里**原样复制**一段连续字符"
            "（不要改写、概括或翻译）；声称「发现/拿到/绕过」却不给逐字证据的结论会被打回重交。"
        )
    if "report" not in out:
        out += (
            "\n交付规则：结论必须用 report 交出去（调用即结束回合，协调者才看得到）；"
            "note 只用来记中间线索，不要拿它交付结论。"
        )
    out += (
        "\n不可达纪律：目标 DNS 解析失败 / 连接被拒 / 超时时，最多换一种接入方式再试一次，"
        "仍失败就**立刻交付卡点**（写清 host:port 与错误原文，不要空转）；"
        "严禁转而浏览本地文件系统、源码目录或系统路径去找线索——那不是本题解法，只会浪费预算。"
    )
    return out


def check_role(role: str, cfg: dict, enum: list[str]) -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    spec = (cfg.get("roles") or {}).get(role) or {}
    text = spec.get("system") or ""

    # 2. 存在且非空
    rows.append(("2 正文存在且非空", *(_ok("%d 字符" % len(text)) if text.strip() else _fail("缺失或为空"))))
    if not text.strip():
        return rows

    # 3. 不含 PLAN
    has_plan = "PLAN" in text
    rows.append(
        (
            "3 正文不含 PLAN",
            *( _fail("出现了 PLAN —— COMPANION_RULE 将不再追加，计划跟踪会静默失效")
               if has_plan else _ok("未出现，COMPANION_RULE 正常追加") ),
        )
    )

    # 4. 含 evidence / report
    has_ev = "evidence" in text.lower()
    has_rep = "report" in text
    rows.append(
        (
            "4 含 evidence 与 report",
            *(_ok("两者都在") if (has_ev and has_rep) else _fail(
                "缺 %s —— 会触发对应规则重复追加" % "/".join(
                    [n for n, v in (("evidence", has_ev), ("report", has_rep)) if not v]))),
        )
    )

    # 5. 长度
    lo, hi = LEN_RANGE
    rows.append(
        (
            "5 长度在 %d–%d" % (lo, hi),
            *(_ok("%d 字符" % len(text)) if lo <= len(text) <= hi else _fail(
                "%d 字符，超出区间（%s）" % (len(text), "太短，疑似误删" if len(text) < lo else "太长，模型会跳读"))),
        )
    )

    # 6a. view= 取值必须在 enum 内
    used = set(re.findall(r"view\s*=\s*([a-z_]+)", text))
    if enum:
        bad = sorted(v for v in used if v not in enum)
        rows.append(
            (
                "6a view= 取值合法",
                *(_ok("用到 %s" % (", ".join(sorted(used)) or "无")) if not bad
                  else _fail("不存在的视图：%s（合法值：%s）" % (", ".join(bad), ", ".join(enum)))),
            )
        )
    else:
        rows.append(("6a view= 取值合法", False, "取不到 elfpeek enum，跳过（环境问题）"))

    # 6b. 容器里没有的工具名不能以肯定语气出现
    # 用词边界匹配：否则 "nc" 会命中 "instance"/"start_instance"，全是误报。
    offenders = []
    for line in text.split("\n"):
        for tool in ABSENT_TOOLS:
            pat = r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tool)
            for m in re.finditer(pat, line, flags=re.IGNORECASE):
                # 工具名前面 30 个字符内有否定词，视为合法（如"容器无 objdump/gdb/ROPgadget"）
                window = line[max(0, m.start() - 30): m.start()]
                if any(neg in window for neg in NEGATIONS):
                    continue
                # "nc 类" 是在说题型（netcat 型交互题），不是让人去调 nc 二进制
                if line[m.end(): m.end() + 2].strip().startswith("类"):
                    continue
                offenders.append("%s（%s）" % (tool, line.strip()[:40]))
                break
    rows.append(
        (
            "6b 无幻觉工具",
            *(_ok("无") if not offenders else _fail(
                "以肯定语气提到容器里不存在的工具：%s" % "; ".join(offenders[:4]))),
        )
    )

    # 7. 拼装后三条关键规则仍在
    full = assemble(role, text)
    missing = [k for k, probe in (
        ("PLAN:", "PLAN:"),
        ("REFLECTION:", "REFLECTION:"),
        ("不可达纪律", "不可达纪律"),
    ) if probe not in full]
    rows.append(
        (
            "7 拼装后规则完整",
            *(_ok("三条都在，最终 %d 字符" % len(full)) if not missing
              else _fail("拼装后缺：%s" % ", ".join(missing))),
        )
    )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="提示词装配自检")
    ap.add_argument("--show", action="store_true", help="打印拼装后的完整 system")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument(
        "--config",
        metavar="PATH",
        help="检查指定 JSON（默认 config/agent.json）——落地前先验候选件用",
    )
    args = ap.parse_args()

    target = Path(args.config).expanduser() if args.config else CONFIG
    if not target.exists():
        print("找不到 %s" % target, file=sys.stderr)
        return 2
    try:
        cfg = load_config(target)
    except Exception as exc:  # noqa: BLE001
        print("1 %s 是合法 JSON" % target.name, file=sys.stderr)
        print("  ✗ %s" % exc, file=sys.stderr)
        return 1

    enum = elfpeek_enum()
    report: dict[str, list] = {}
    failed = 0

    for role in ROLES:
        report[role] = [
            {"check": "1 %s 是合法 JSON" % target.name, "ok": True, "detail": "解析通过"},
        ]
        for name, ok, detail in check_role(role, cfg, enum):
            report[role].append({"check": name, "ok": ok, "detail": detail})
            if not ok:
                failed += 1

    if args.json:
        print(json.dumps({"ok": failed == 0, "roles": report}, ensure_ascii=False, indent=2))
    else:
        for role in ROLES:
            print("\n[%s]" % role)
            for row in report[role]:
                mark = "✓" if row["ok"] else "✗"
                print("  %s %-22s %s" % (mark, row["check"], row["detail"]))
        if args.show:
            for role in ROLES:
                text = ((cfg.get("roles") or {}).get(role) or {}).get("system") or ""
                if text:
                    print("\n" + "=" * 70 + "\n拼装后 system（%s，%d 字符）\n" % (role, len(assemble(role, text))) + "=" * 70)
                    print(assemble(role, text))
        print()
        print("结果：%s" % ("全部通过 ✓" if failed == 0 else "%d 项失败 ✗" % failed))

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
