# PWN 团队资产（进镜像）

来源：2026-11-16 clean-room（pwn）与 2026-11-17 Hint Me Maybe（rev）彩排复盘——
两轮共烧 ≈690 万 token 仍未出 flag，但沉淀出三个「每轮都要重写」的确定性环节。

| 文件 | 干什么 | 怎么用 |
|---|---|---|
| `leak_helpers.py` | 泄漏链辅助：`read8()` 短读直接报错不静默、`ElfOffsets`/`LibcOffsets` 查符号与 GOT 偏移、`base_from()` 反推基址 | `python3 leak_helpers.py <challenge> <libc.so.6>` 打印关键偏移；或 `from leak_helpers import ...` |
| `asan_shadow.py` | ASan shadow 数学：`shadow(addr)=(addr>>3)+0x7fff8000`、`unpoison_plan()`、poison 分类 | `python3 asan_shadow.py` 自检；把题目 read8/write8 原语接进 `unpoison_plan` |
| `pwn_stack_probe.py` | 「解封 shadow → 写真实栈 → 读回验证」模板，含每步断言 | 接题目读写原语后 `StackProbe(read8=..., write8=...).probe(addr, 64)` |

## 在 agent 里怎么找到本目录

- 工具：`pwnkit(op="assets")` → 打印绝对路径 + 文件清单 + 用法；
- 代码：`from ctfy_agent.pwn_assets import asan_shadow`（镜像里包已安装）；
- shell：`python3 <pwnkit 给的目录>/pwn_stack_probe.py` 可直接自检。

## 边界

- 只做**数学与计划**，不含题目协议：读写原语必须由本题的 exploit 提供；
- `pwn_stack_probe.py` 用模拟内存自检通过 ≠ 真实题通过，接上原语后仍要逐步断言；
- 这些资产是「减少重复劳动」，不是「自动解题」——路线判断仍在模型侧。