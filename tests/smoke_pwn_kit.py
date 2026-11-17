#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_pwn_kit.py — PWN 硬化的离线回归（pwnkit 工具 + 团队资产 + 接线）。

背景：PWN 是当前最大短板（真实外部题 0/4、BUUCTF 120 题里 3/31、L2 彩排 5 轮 0 flag）。
复盘里反复出现的不是数学问题，而是三个机械环节每次重造：libc 偏移手算、泄漏后基址推导、
ASan shadow 解封。本测试把新增的确定性能力钉住：

  A. ASan shadow 数学（src/ctfy_agent/pwn_kit.asan_shadow）
     1) 整 8 字节窗口：shadow = (addr>>3)+0x7fff8000，值 0；
     2) 非对齐窗口：低 3 位记录「前缀可用字节数」（如 addr 0x1006/4 字节 → [2,2]）；
     3) 非法输入（地址 0、size 0/超大、32/64 位置换）→ PwnKitError。
  B. libc 识别（libc_info，用仓库里真 libc.so.6 样本）
     4) 版本串含 "GNU C Library"、build-id 是 40 位 hex、system/puts 等偏移 >0；
     5) "/bin/sh" 能找到且 vaddr 落在文件范围内；
     6) 非 ELF/缺文件 → 明确错误。
  C. ret2libc 地址推导
     7) base = leaked - symbol_offset；system/binsh 地址 = base + 各自偏移（构造数据复算）；
     8) 泄漏值小于偏移 / 符号不存在 → 可读错误（不猜）。
  D. 团队资产
     9) `ctfy_agent.pwn_assets` 三个资产文件在包内、可 import、ASan 常量与 pwn_kit 一致；
    10) pwnkit(op=assets) 打印目录 + 文件清单。
  E. 工具接线
    11) pwnkit 进 DOMAIN_SPECS / MISC_EXT_SPECS，且 pwn 角色 local 名单含 pwnkit
        （config/agent.json 与 crew.DEFAULT_ROLES 兜底都含）；
    12) 工具层错误（不支持 op / 缺 path / 缺文件 / asan 非法地址）都给可读文本。

运行: python tests/smoke_pwn_kit.py   （纯离线；没有 libc 样本时 B/C 组自动跳过）
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent.pwn_kit import (  # noqa: E402
    PWN_ASSETS_DIR,
    PwnKitError,
    asan_shadow,
    assets,
    libc_info,
    ret2libc,
)

FAILS: list[str] = []
SKIPS: list[str] = []

#: 仓库里跑批留下的真 libc 样本（runs/ 被 gitignore，没有就跳过解析类用例）
LIBC_SAMPLE = ROOT / "runs" / "tpctf2026" / "attachments" / "AUTHORED-31FB487C" / "libc.so.6"


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}")
    if not cond:
        FAILS.append(name)
        if detail:
            print("       " + detail.replace("\n", "\n       "))


def skip(name: str, why: str) -> None:
    print(f"[skip] {name} —— {why}")
    SKIPS.append(name)


def expect_error(name: str, fn, *needles: str) -> None:
    try:
        fn()
    except PwnKitError as exc:
        text = str(exc)
        missing = [n for n in needles if n not in text]
        check(name, not missing, f"错误文本缺少 {missing}：{text!r}")
        return
    check(name, False, "没有抛 PwnKitError")


# ---------------------------------------------------------------- A. ASan


def case_asan() -> None:
    print("\n== A. ASan shadow 数学 ==")
    plan = asan_shadow(0x7FFFFFFFE000, 8)
    check("整 8 字节：shadow=(addr>>3)+0x7fff8000",
          plan["shadow_start"] == hex((0x7FFFFFFFE000 >> 3) + 0x7FFF8000), plan["shadow_start"])
    check("整 8 字节：要写的 shadow 值为 0", [x["shadow_value"] for x in plan["plan"]] == [0])

    partial = asan_shadow(0x1006, 4)
    check("非对齐窗口跨 2 个 shadow 字节", len(partial["plan"]) == 2, str(partial["plan"]))
    # 0x1006 起点不齐：首块必须整块放开（0），第二块只剩 2 字节可访问（0x1008..0x1009 → 2）
    check("非对齐窗口：首块整块放开、尾块按余量（0x1006..0x1009 → [0,2]）",
          [x["shadow_value"] for x in partial["plan"]] == [0, 2],
          str([x["shadow_value"] for x in partial["plan"]]))
    tail = asan_shadow(0x1000, 4)
    check("尾块不足 8 字节按余量（0x1000..0x1003 → 4）",
          [x["shadow_value"] for x in tail["plan"]] == [4],
          str([x["shadow_value"] for x in tail["plan"]]))

    expect_error("地址 0 明确报错", lambda: asan_shadow(0, 8), "不在用户态")
    expect_error("size=0 明确报错", lambda: asan_shadow(0x7FFFFFFFE000, 0), "不合法")
    expect_error("size 超上限明确报错", lambda: asan_shadow(0x7FFFFFFFE000, 0x20000), "不合法")
    plan32 = asan_shadow(0x0804A000, 8, bits=32)
    check("32 位 shadow 偏移是 0x20000000",
          plan32["shadow_offset"] == "0x20000000", plan32["shadow_offset"])


# ---------------------------------------------------------------- B/C. libc


def case_libc() -> None:
    print("\n== B/C. libc 识别 + ret2libc 推导 ==")
    if not LIBC_SAMPLE.is_file():
        skip("libc 解析组", f"仓库里没有样本 {LIBC_SAMPLE.relative_to(ROOT)}（runs/ 被 gitignore）")
        skip("ret2libc 推导组", "同上")
        return
    data = LIBC_SAMPLE.read_bytes()
    info = libc_info(data, path=str(LIBC_SAMPLE))
    check("识别为 ELF64 / x86-64", info["class"] == "ELF64" and info["machine"] == "x86-64",
          f"{info['class']} {info['machine']}")
    check("版本串含 GNU C Library", any("GNU C Library" in v for v in info["version"]),
          str(info["version"]))
    check("build-id 是 40 位 hex", len(info["build_id"]) == 40 and
          all(c in "0123456789abcdef" for c in info["build_id"]), info["build_id"])
    check("system/puts/read 偏移都 >0",
          all(k in info["symbols"] and info["symbols"][k]["value"] > 0
              for k in ("system", "puts", "read")), str(sorted(info["symbols"])))
    check("/bin/sh 有 vaddr 且在文件范围内",
          bool(info["binsh"]) and info["binsh"][0]["vaddr"] is not None
          and 0 < info["binsh"][0]["vaddr"] < len(data), str(info["binsh"][:1]))

    sys_off = info["symbols"]["system"]["value"]
    puts_off = info["symbols"]["puts"]["value"]
    fake_base = 0x7FFFF7000000
    got = ret2libc(data, "puts", fake_base + puts_off, path=str(LIBC_SAMPLE))
    check("ret2libc：base = leaked - puts 偏移", int(got["base"], 16) == fake_base, got["base"])
    check("ret2libc：system = base + system 偏移",
          int(got["system"], 16) == fake_base + sys_off, got["system"])
    check("ret2libc：/bin/sh 地址已加基址",
          "binsh" in got and int(got["binsh"], 16) == fake_base + info["binsh"][0]["vaddr"],
          str(got.get("binsh")))
    check("ret2libc：gadget 地址已加基址（若有命中）",
          all(int(v[0], 16) >= fake_base for v in (got.get("gadgets") or {}).values()),
          str(got.get("gadgets")))

    expect_error("泄漏值小于符号偏移 → 报错而不是算负数基址",
                 lambda: ret2libc(data, "puts", 0x1000), "小于符号偏移")
    expect_error("符号不存在 → 报错并给相近名",
                 lambda: ret2libc(data, "no_such_symbol_xyz", fake_base), "没有符号")
    expect_error("非 ELF 内容 → 报错", lambda: libc_info(b"not an elf at all" * 8), "不是 ELF")


# ---------------------------------------------------------------- D. 资产


def case_assets() -> None:
    print("\n== D. 团队资产 ==")
    for name in ("asan_shadow.py", "leak_helpers.py", "pwn_stack_probe.py"):
        check(f"资产存在：{name}", (PWN_ASSETS_DIR / name).is_file(), str(PWN_ASSETS_DIR))
    from ctfy_agent.pwn_assets import asan_shadow as asset_asan  # noqa: PLC0415

    check("资产 ASan 常量与 pwn_kit 一致（0x7fff8000）",
          getattr(asset_asan, "ASAN_SHADOW_OFFSET", None) == 0x7FFF8000,
          str(getattr(asset_asan, "ASAN_SHADOW_OFFSET", None)))
    check("资产提供 unpoison_plan（解封计划）", callable(getattr(asset_asan, "unpoison_plan", None)))
    info = assets()
    check("assets() 列 3 个 pwn 资产（不含 __init__）",
          [f["name"] for f in info["files"]] ==
          ["asan_shadow.py", "leak_helpers.py", "pwn_stack_probe.py"],
          str([f["name"] for f in info["files"]]))


# ---------------------------------------------------------------- E. 接线


def case_wiring() -> None:
    print("\n== E. 工具接线 ==")
    from ctfy_agent.tools_domain_ext import DOMAIN_NAMES, DomainToolError, call_domain  # noqa: PLC0415
    from ctfy_agent.tools_local import ToolError  # noqa: PLC0415
    from ctfy_agent.tools_misc_ext import MISC_EXT_SPECS  # noqa: PLC0415
    from ctfy_agent.crew import DEFAULT_ROLES  # noqa: PLC0415

    check("pwnkit 在 DOMAIN_NAMES", "pwnkit" in DOMAIN_NAMES, sorted(DOMAIN_NAMES))
    specs = {s["function"]["name"]: s for s in MISC_EXT_SPECS}
    check("pwnkit spec 聚合进 MISC_EXT_SPECS", "pwnkit" in specs)
    if "pwnkit" in specs:
        enum = specs["pwnkit"]["function"]["parameters"]["properties"]["op"]["enum"]
        check("spec 的 op 枚举齐全", set(enum) == {"libc", "ret2libc", "asan", "assets"}, str(enum))
    cfg = json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    check("config 的 pwn 角色含 pwnkit", "pwnkit" in cfg["roles"]["pwn"]["local"],
          str(cfg["roles"]["pwn"]["local"]))
    check("crew.DEFAULT_ROLES 的 pwn 兜底含 pwnkit", "pwnkit" in DEFAULT_ROLES["pwn"]["local"])
    check("pwn 提示词提到 pwnkit", "pwnkit" in cfg["roles"]["pwn"]["system"])

    out = call_domain("pwnkit", {"op": "asa" + "n", "addr": "0x7fffffffe000", "size": 8})
    check("op=asan 走工具层能输出 shadow 区间", "shadow 区间" in out, out[:200])
    out_assets = call_domain("pwnkit", {"op": "assets"})
    check("op=assets 列出 leak_helpers.py", "leak_helpers.py" in out_assets, out_assets[:200])

    for name, kwargs, needle in (
        ("不支持 op 报错", {"op": "nope"}, "不支持的 op"),
        ("缺 path 报错", {"op": "libc"}, "需要 path"),
        ("文件不存在报错", {"op": "libc", "path": "no/such/libc.so.6"}, "文件不存在"),
        ("asan 缺 addr 报错", {"op": "asan"}, "addr 不能为空"),
        ("asan 地址非法报错", {"op": "asan", "addr": "0x0"}, "不在用户态"),
    ):
        try:
            call_domain("pwnkit", kwargs)
            check(name, False, "没有抛 DomainToolError")
        except (DomainToolError, ToolError) as exc:
            check(name, needle in str(exc), f"{exc!r}")


def main() -> int:
    # config 里的 pwn 提示词长度上限：避免有人把提示词写成大段文档（吃每轮上下文）
    cfg = json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    prompt_len = len(cfg["roles"]["pwn"]["system"])
    check("pwn 提示词长度在 3000~6000 字符（3.6k 附近，不失控）",
          3000 <= prompt_len <= 6000, str(prompt_len))

    case_asan()
    case_libc()
    case_assets()
    case_wiring()

    print()
    if FAILS:
        print(f"RESULT=FAIL ({len(FAILS)} 项): " + "; ".join(FAILS))
        return 1
    print(f"RESULT=PASS（跳过 {len(SKIPS)} 组）" if SKIPS else "RESULT=PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())