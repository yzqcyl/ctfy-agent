# -*- coding: utf-8 -*-
"""并发上限：由硬件信息自动推导本机最大并发，并与平台上限取较小值。

- 平台上限：平台规则 QA#26 规定 agent 自主并发 ≤ 3（crew.py 集群-A 同口径）。
- 硬件上限：按逻辑核数 + 可用内存估算（每个并发解题约占 0.7GB 余量），
  再兜一个 16 的硬顶，避免误设过大。
纯新增，不 import 平台/解题代码。
"""
from __future__ import annotations

import os

PLATFORM_MAX_CONCURRENCY = 3   # 平台规则 QA#26：自主并发 ≤ 3
MEM_PER_SOLVE_GB = 0.7         # 单个并发解题的内存余量估算
HARD_CAP = 16                  # 兜底硬上限


def _memory_gb() -> tuple[float, float]:
    """(总内存, 可用内存) GB；读不到返回 (0, 0)。"""
    try:
        import ctypes

        class _MemStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = _MemStatus()
        st.dwLength = ctypes.sizeof(_MemStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return 0.0, 0.0
        return st.ullTotalPhys / 1e9, st.ullAvailPhys / 1e9
    except Exception:  # noqa: BLE001 - 非 Windows / 受限环境：只用 CPU 核数
        return 0.0, 0.0


def hardware_info() -> dict:
    cpu = int(os.cpu_count() or 4)
    total_gb, avail_gb = _memory_gb()
    if avail_gb > 0:
        mem_limit = max(1, int(avail_gb / MEM_PER_SOLVE_GB))
    else:
        mem_limit = cpu
    hw_max = max(1, min(cpu, mem_limit, HARD_CAP))
    return {
        "cpu": cpu,
        "total_gb": round(total_gb, 1),
        "avail_gb": round(avail_gb, 1),
        "mem_limit": mem_limit,
        "max_concurrency": hw_max,
    }


def effective_max(mode: str = "platform") -> dict:
    """返回 {"max": 上限, "reasons": [说明], "hardware": {...}}。"""
    hw = hardware_info()
    caps = [hw["max_concurrency"]]
    if hw["avail_gb"] > 0:
        reasons = [f"本机 {hw['cpu']} 核 / 可用 {hw['avail_gb']:.1f}GB（硬件上限 {hw['max_concurrency']}）"]
    else:
        reasons = [f"本机 {hw['cpu']} 核（硬件上限 {hw['max_concurrency']}）"]
    if mode == "platform":
        caps.append(PLATFORM_MAX_CONCURRENCY)
        reasons.append(f"平台上限 {PLATFORM_MAX_CONCURRENCY}")
    return {"max": max(1, min(caps)), "reasons": reasons, "hardware": hw}