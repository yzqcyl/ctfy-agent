#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""asan_shadow.py — ASan shadow 数学 + 解封计划（团队资产，pwn/clean-room 类题）。

背景（clean-room 五轮实测结论）：ASan 把"地址是否可访问"编码在 shadow 区：
    shadow(addr) = (addr >> 3) + 0x7fff8000        # x86_64 64-bit ASan 约定
    shadow byte = 0       → 该 8 字节都可访问
                  1..7    → 前 k 字节可访问
                  0xFA..  → 栈 use-after-return / 容器注解等特殊 poison
所以"写被 ASan 保护的地址（真实栈/全局）"之前，必须先把对应 shadow 字节清 0。

这个模块只做**数学与计划**，不碰题目协议；把 read8/write8 原语接进来即可：
    plan = unpoison_plan(stack_addr, 64, read8=read8)
    for shadow_addr, value in plan.writes:
        write8(shadow_addr, value)          # 用题目的任意 8 字节写原语
复现/验证见 pwn_stack_probe.py。
"""
from __future__ import annotations

import struct

ASAN_SHADOW_OFFSET = 0x7FFF8000
GRANULARITY = 8          # 1 shadow byte 管 8 字节
SHIFT = 3

# 常见 shadow 值语义（低 3 位有效；负值/>=0x80 是特殊 poison）
POISON_STACK_RETURN_ADDRESS = 0xF5      # 栈 use-after-return 的伪造帧
POISON_STACK_USE_AFTER_RETURN = 0xFA
POISON_HEAP_USE_AFTER_FREE = 0xFB
POISON_CONTAINER_ANNOTATION = 0xFC
POISON_ARRAY_COOKIE = 0xAC
POISON_INTRA_OBJECT_REDZONE = 0xBB
POISON_REDZONE = 0xFD


def shadow_addr(addr: int, offset: int = ASAN_SHADOW_OFFSET) -> int:
    """返回 addr 对应的 shadow 字节地址。"""
    return (int(addr) >> SHIFT) + offset


def shadow_span(addr: int, size: int, offset: int = ASAN_SHADOW_OFFSET) -> tuple[int, int]:
    """[addr, addr+size) 覆盖的 shadow 字节区间 [first, last]（含端点）。"""
    if size < 0:
        raise ValueError("size 不能为负")
    first = shadow_addr(addr, offset)
    last = shadow_addr(addr + max(size - 1, 0), offset)
    return first, last


def classify(byte: int) -> str:
    """按 ASan shadow 语义分类（仅供报告；判断可访问性用 accessible()）。"""
    value = byte & 0xFF
    if value == 0:
        return "accessible"
    if 0 < value < GRANULARITY:
        return f"partial({value}/8)"
    mapping = {
        POISON_STACK_USE_AFTER_RETURN: "stack-use-after-return(fake frame)",
        POISON_STACK_RETURN_ADDRESS: "stack-use-after-return",
        POISON_HEAP_USE_AFTER_FREE: "heap-use-after-free",
        POISON_CONTAINER_ANNOTATION: "container-annotation",
        POISON_ARRAY_COOKIE: "array-cookie",
        POISON_INTRA_OBJECT_REDZONE: "intra-object-redzone",
        POISON_REDZONE: "redzone",
    }
    return mapping.get(value, f"poison(0x{value:02x})")


def accessible(byte: int, offset_in_group: int) -> bool:
    """给定 shadow 字节与被检查地址在 8 字节组内的偏移，判断该字节是否可访问。"""
    value = byte & 0xFF
    if value == 0:
        return True
    if value < GRANULARITY:
        return offset_in_group < value
    return False


class UnpoisonPlan:
    """解封计划：一次 8 字节写可以把一组（8 个）shadow 字节清零。

    writes: [(shadow_addr, 8字节小端值)]；restore: 反向计划（写回原 shadow 值，可选）。
    """

    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []
        self.restore: list[tuple[int, int]] = []
        self.notes: list[str] = []

    def add_group(self, group_addr: int, original: int) -> None:
        self.writes.append((group_addr, 0))
        self.restore.append((group_addr, original))

    def __repr__(self) -> str:
        return (f"UnpoisonPlan(writes={len(self.writes)}, "
                f"first={self.writes[0][0]:#x} .. {self.writes[-1][0]:#x})"
                if self.writes else "UnpoisonPlan(empty)")


def unpoison_plan(addr: int, size: int, *, read8=None,
                  offset: int = ASAN_SHADOW_OFFSET) -> UnpoisonPlan:
    """生成把 [addr, addr+size) 的 shadow 清零的写计划。

    - read8(addr)->int：题目的任意读原语（读 8 字节）。给了就做读-改-写：只清本区间覆盖
      的 shadow 字节，保留同组里其它位置的原值（避免误伤相邻对象的 poison 状态，ASan 更容易
      接受）；没给就直接整组写 0（可能把这些 8 字节覆盖范围内的相邻对象也解封，通常也能跑）。
    """
    if size <= 0:
        raise ValueError("size 必须 > 0")
    plan = UnpoisonPlan()
    base = addr
    end = addr + size
    while base < end:
        group = shadow_addr(base, offset) & ~7       # 一个 shadow 组管 64 字节地址
        group_block_start = (group - offset) << SHIFT
        mask = 0xFFFFFFFFFFFFFFFF
        for j in range(8):
            block_start = group_block_start + j * GRANULARITY
            if block_start + GRANULARITY <= addr or block_start >= end:
                continue
            mask &= ~(0xFF << (8 * j))               # 清掉该 shadow 字节
        original = 0
        if read8 is not None:
            try:
                original = int(read8(group)) & 0xFFFFFFFFFFFFFFFF
            except Exception:  # noqa: BLE001 - 读不到就按 0 处理（整组都解封）
                original = 0
        new_value = original & mask
        if mask != 0xFFFFFFFFFFFFFFFF:
            # 没给 read8 时 original 未知，按"当前是 poison"处理，直接写 0 解封；
            # 给了 read8 就只在确实需要清字节时写（避免无谓写）。
            if read8 is None or new_value != original:
                plan.writes.append((group, new_value))
                plan.restore.append((group, original))
        base = group_block_start + 64
    if not plan.writes:
        plan.notes.append("shadow 已经是 0（区间本来就 unpoison）")
    return plan


def chunk_plan(addr: int, size: int, *, read8=None,
               offset: int = ASAN_SHADOW_OFFSET) -> UnpoisonPlan:
    """按 shadow 组（64 字节地址）逐组生成计划；语义与 unpoison_plan 相同。"""
    return unpoison_plan(addr, size, read8=read8, offset=offset)


def explain() -> str:
    return (
        "ASan shadow（x86_64 64-bit）：\n"
        f"  shadow(addr) = (addr >> 3) + {ASAN_SHADOW_OFFSET:#x}\n"
        "  shadow byte: 0=8 字节可访问；1..7=前 k 字节可访问；0xFA/0xFB/0xF5..=特殊 poison\n"
        "  解封 = 用任意写把 shadow 字节清零（一次 8 字节写可清 8 个 shadow 字节 = 64 字节地址范围）\n"
        "  注意：ASan fake stack 开启时，当前函数的局部变量在 fake stack（堆式区域），\n"
        "        写真实栈不一定影响活动帧；要改真实栈先确认目标地址/帧，再解封其 shadow。\n"
    )


def _selftest() -> int:
    # 纯数学自检（不依赖目标进程）
    cases = [
        (0x0, 0x7FFF8000),
        (0x1000, 0x7FFF8000 + (0x1000 >> 3)),
        (0x7FFF0000, 0x7FFF8000 + (0x7FFF0000 >> 3)),
        (0xFFFF8000, 0x7FFF8000 + (0xFFFF8000 >> 3)),
    ]
    for addr, expect in cases:
        got = shadow_addr(addr)
        assert got == expect, (hex(addr), hex(got), hex(expect))
    first, last = shadow_span(0x1000, 64)
    assert last - first == 7, (hex(first), hex(last))
    plan = unpoison_plan(0x7FFD0000, 64)
    assert plan.writes, "应产生写计划"
    for group, value in plan.writes:
        assert group % 8 == 0, hex(group)
        assert value == 0, hex(value)
    plan2 = unpoison_plan(0x7FFD0001, 9)
    assert plan2.writes, "非对齐区间也要有计划"
    assert classify(0) == "accessible" and classify(3) == "partial(3/8)"
    assert classify(0xFA).startswith("stack-use-after-return")
    assert accessible(0, 7) and not accessible(0xFA, 0) and accessible(2, 1)
    print("asan_shadow selftest OK；示例:", plan)
    print(explain())
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())