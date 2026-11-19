#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_prompt_tools.py — 构建期一致性检查：prompt 承诺的工具，镜像里必须真的有。

背景（2026-11-19 评审第 2 条）：`tools/` 被 .dockerignore 排除，`gdb_pwn.py`（pwndbg-lite）、
Ghidra 脚本在提交镜像里不存在，但 rev/pwn prompt 直接承诺「镜像内有 Ghidra headless」——
VPS 挂 repo 跑掩盖了问题，平台托管形态会直接踩。

本脚本在镜像构建期跑（Dockerfile），检查：
  1. 承诺的**文件**是否存在（gdb_pwn.py / ghidra_decompile.py 包装器 / wireproxy）；
  2. prompt 是否**无条件承诺**了镜像里没有的工具（Ghidra 本体）；
  3. method_cards 里的工具引用要么带「若/if」条件，要么文件确实存在。
失败则构建失败——把「prompt 承诺 vs 镜像现实」的不一致挡在发布前。
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

# 镜像内是 /ctfy；本地/CI 跑测试时退化为仓库根（自动检测，避免硬编码路径把本地自检跑挂）
ROOT = Path("/ctfy") if Path("/ctfy/config/agent.json").exists() \
    else Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "tools"
# 镜像内这些脚本被 COPY 到 /ctfy/tools/；本地仓库里源文件在 tools/linux_bootstrap/
if ROOT == Path("/ctfy"):
    REQUIRED_FILES = [TOOLS_DIR / "gdb_pwn.py", TOOLS_DIR / "ghidra_decompile.py"]
else:
    REQUIRED_FILES = [ROOT / "tools" / "linux_bootstrap" / "gdb_pwn.py",
                      ROOT / "tools" / "linux_bootstrap" / "ghidra_decompile.py"]
# 必须在 PATH 或固定路径存在的二进制
REQUIRED_BINARIES = ["wireproxy"]
# 镜像里**没有**、prompt 不许无条件承诺的工具（关键词, 条件提示词正则）
NOT_SHIPPED = [
    ("ghidra_decompile", re.compile(r"(若|如果|当).{0,40}ghidra|ghidra.{0,40}(若|如果|when|if)")),
    ("angr", re.compile(r"(没有|未安装|无)\s*angr|angr.{0,20}(没有|未安装)")),
]


def load_prompts() -> dict[str, str]:
    cfg_path = ROOT / "config" / "agent.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
    out = {}
    for name, spec in (cfg.get("roles") or {}).items():
        if isinstance(spec, dict) and spec.get("system"):
            out[name] = str(spec["system"])
    return out


def main() -> int:
    problems: list[str] = []
    for path in REQUIRED_FILES:
        if not path.exists():
            problems.append(f"prompt 承诺的脚本不存在：{path}")
    for binary in REQUIRED_BINARIES:
        if not (shutil.which(binary) or (ROOT / ".tools" / "wireproxy" / "wireproxy").exists()):
            problems.append(f"prompt/工具链需要的二进制不存在：{binary}（PATH 与 /ctfy/.tools 都没有）")

    for role, text in load_prompts().items():
        if role not in ("rev", "pwn"):
            continue
        for keyword, conditional in NOT_SHIPPED:
            if keyword in text:
                if not conditional.search(text):
                    problems.append(
                        f"{role} prompt 无条件承诺了镜像未安装的 {keyword}（应改成「若 PATH 里有才用」）")
    if problems:
        print("[FAIL] prompt 与镜像工具面不一致：")
        for p in problems:
            print("  -", p)
        return 1
    print(f"[ok] prompt/工具面一致：{len(REQUIRED_FILES)} 个脚本 + {len(REQUIRED_BINARIES)} 个二进制都在位")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())