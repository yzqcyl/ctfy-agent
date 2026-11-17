# -*- coding: utf-8 -*-
"""PWN 团队资产（随包发布，镜像内可直接引用）。

这些脚本来自 2026-11-16/17 两轮真难题彩排（clean-room pwn、Hint Me Maybe rev）里
每次都要重写的部分，抽成资产后模型不必在 800k 预算里重造：

* ``leak_helpers.py``     泄漏链辅助：``read8`` 满 8 字节断言、ELF/GOT/RELRO 偏移、基址反推；
* ``asan_shadow.py``      ASan shadow 数学：``shadow_addr`` / ``unpoison_plan`` / 分类；
* ``pwn_stack_probe.py``  「解封 shadow → 写真实栈 → 读回验证」模板（接题目自己的原语）。

路径：``pwnkit(op=assets)`` 会打印本目录与文件清单（GUI/容器都适用）。
单独自检：``python3 <本目录>/asan_shadow.py``、``python3 <本目录>/pwn_stack_probe.py``。
"""

__all__ = ["leak_helpers", "asan_shadow", "pwn_stack_probe"]