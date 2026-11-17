# 解题记录（2026-11-16 ~ 11-17，L2 隔离彩排阶段）

> 本文是**汇总统计**，不是单题复现。原始运行目录（历史 `runs/` 大文件）已按空间清理计划删除；
> 关键数字、结论、资产与文档路径保留在这里和 `docs/` 下对应设计文档中。

## 1. 环境与前提

- 平台：CTF 平台 MCP（比赛 `c0d8b4e3…`），测试期间**所有题目 closed / scoring closed、窗口已结束**，
  无法在真实平台跑，只能用本地 L2 隔离彩排（真 LLM + 真题附件 + 本地靶）。
- 模型：`deepseek-v4.1-flash-1017`（DashScope）；预算 800k token/题、墙钟 35~50 分钟/轮。
- 隔离彩排：root 起真靶（随机 flag、`/answers` 600），agent 以非 root `l2agent` 跑，
  oracle 只拿 flag 的 sha256；`/answers` 被拒访问 0~2 次/轮（隔离生效证据）。

## 2. 总量统计

| 题目 | 轮次 | tokens（含早期/中断轮） | 结果 | 到达阶段 |
|---|---|---|---|---|
| clean-room（pwn, hard, 136 队解） | 5 轮（4 完整 + 1 被 harness 中断） | **≈403 万**（隔离后有效 ≈360 万） | 无 flag | A 可控写、B 任意读 + PIE/libc 基址；C 卡 ASan 写栈 |
| Hint Me Maybe（rev, medium, 128 队解） | 5 轮（含早期未隔离 1 轮） | **≈288 万** | 无 flag | 判据链读懂；最终证伪：本地靶 final marker 恒 128 ≠ 需求 640，**本地不可解** |
| Cut the Clock / cve_stage / Last 3 Minutes | 0 | 0 | 未跑 | —— |
| 合计 | 10 轮 | **≈690 万** | **0 个 flag** | —— |

> 有效隔离数据（去掉早期未隔离/被打断轮）：**≈570 万 token**，env 错误 / 命令错误均为 0（少数 cmd 错误为 agent 自写脚本语法问题）。

## 3. 每题关键结论（摘要）

**clean-room（pwn）**
- 已确认：Full RELRO/Canary/NX/PIE/ASan+UBSan+FORTIFY3 + seccomp allowlist（openat/read/write 等）；
- 已打通：delete 后 `selected` 悬垂指针 → **可控 8 字节写**；KIND_BLOB view → **任意读**；
  PIE base、libc base 均算出并**交叉验证**（PIE base `0x5d47c99a5000` 与 `/proc/pid/maps` 一致）；
- 卡点：ASan fake stack 下写真实栈需先解封 shadow；GOT/BSS/结构体字段/栈 poisoning 路线均被实测否证；
- 交付资产：`leak_helpers.py`（ELF/GOT/RELATIVE/read8 短读断言）、`asan_shadow.py`（shadow 公式与解封计划）、
  `pwn_stack_probe.py`（解封→写栈→读回验证模板）。

**Hint Me Maybe（rev）**
- Ghidra headless 接入后读懂：输入 24 nibble → 24 轴 fvar 坐标；两块链式校验（"ABCDEFGHI"/"JKLMNOPQR"），
  每块 8 步读回坐标写回 + 第 9 字 final 检查（要求 marker == 640）；
- 写了进程内 harness `rev_hint_harness.py`，与 LD_PRELOAD shim 实测链路**逐位一致**；
- 用 harness 实测：`final_read` 对任意坐标恒为 **128**（随机 2 万次搜索、直接坐标 200 组、pedantic 模式均如此），
  即本地 `checker + challenge.ttf` 组合下**不存在 accepted 输入** → 结论：本地靶不可解（需真实平台窗口复核，
  可能是附件/环境不匹配），不再在本地 rev 靶上烧预算。

## 4. 同期建设的设施（解题本体）

- 隔离 L2 彩排：root wrapper（随机 flag/靶进程）+ l2agent（非 root）+ hash oracle + 沙盒资产自动注入；
- 思考护栏：烧穿第 1 次关思考+注入执行提示、第 3 次才终止（烧穿终止 8~13 次/轮 → **0**）；
- 执行纪律：实验优先（连续 2 步静态阅读强制实验）、分析桶 ≤30%、无实验输出标未验证、搜索委托/阶段门禁；
- 工具链：Ghidra headless 反编译（`ghidra_decompile`）、leak_helpers、rev_ft_helpers、rev_hint_harness、
  asan_shadow、pwn_stack_probe；cancel/预算/墙钟传播修复；VPN/wireproxy landlock 修复；
- 交付：单一交付包 `dist/tpctf-agent-upload.tar.gz`（694 文件 / 94.6MB，含离线 wheel bundle），
  解包 24/24 回归通过。

## 5. 失败教训（一句话版）

1. 不是 token 不够/环境不行：进程层浪费（烧穿废轮、65% shell 花在 ls/cat/grep）修掉后 env/cmd 错误为 0，
   但仍不出 flag → 剩下的是**实验设计/洞察**问题；
2. 同策略连续 4 轮才读思考过程是流程错误：正确顺序是 1 轮 → 诊断 → 改策略 → 再续；
3. 加预算不是策略：每次续跑必须带来新工具/新观测/新纪律；
4. 需要"决定性观测"：rev 的 gdb 2 分钟观测 > 3 轮 100 万 token 的静态推断；pwn 的 ASan shadow 计划同理；
5. 本地靶要先验证可解性（rev 就是反例：本地恒 reject，四轮全白跑）。

## 6. 数据与资产位置

- 本次摘要数字来源：各轮 `runs/_l2_sim_*/state.json` 与 `runs/_tpctf/l2_out/summary*.json`（原始大目录已清理）；
- 设计/复盘文档：`docs/l2-isolation-2026-11-16.md`、`docs/ghidra-rev-2026-11-17.md`、
  `docs/pwn-asan-assets-2026-11-17.md`、`docs/strategy-guards-2026-11-17.md`；
- 团队资产：`runs/_tpctf/l2_assets/`（随交付包发布，隔离沙盒自动注入）；
- 彩排脚本：`runs/_tpctf/`（`l2_isolated_run.sh`、`l2_sim_iso.py`、`rev_target.py`、`run_all_tests.py` 等）。
