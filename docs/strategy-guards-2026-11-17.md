# 执行纪律护栏（2026-11-17，反思 550 万 token 未出 flag 后）

## 背景：为什么加

clean-room(pwn) 约 360 万 + Hint Me Maybe(rev) 约 270 万 token，**0 个 flag**；而人类队伍解出数
是 136 / 128。transcripts 复盘出的失败模式：

1. **分析优先**：rev 3 轮没有任何一次 gdb/ltrace 动态观测，全靠 objdump 静态推断；而事后
   2 分钟 gdb 就拿到了真实调用参数（`FT_Set_Var_Design_Coordinates(24, nibble<<16)`、
   `FT_Load_Glyph(gid=1, flags=0x8008)`、读回 16 值写回下一轮）。
2. **未验证结论跨轮传播**：报告里"确认/判据"没有命令+输出支撑，前后轮互相矛盾
   （x-grid vs y-delta），下一轮仍当事实用。
3. **不用 oracle 做搜索**：本地 `./checker` 就是毫秒级 oracle，可批量跑 10^3~10^5 次；
   agent 却用 LLM 步数替代搜索（rev3 的候选脚本还因 TypeError 没跑起来）。
4. **阶段无门禁**：pwn 的 C 段一次没开始，预算 99% 耗尽后按"纪律"交卡点。

## 实现的四条硬规则

`src/ctfy_agent/solver.py`（`run_role`）+ `src/ctfy_agent/crew.py`（`_brief`）：

1. **实验优先**：`_classify_call` 把工具调用分为 experiment（python/gdb/nc/curl/./bin、tcp/http）、
   read（ls/cat/grep/objdump/readelf/elfpeek…）、other。连续 2 步只有 read → 注入"停止阅读、
   做实验/搜索"提示，并强制 `tool_choice="required"`（逼模型下一次必须调工具）。
2. **分析桶 ≤30%**：纯分析（无工具调用）token 占比超 30% → 同样注入提示 + 强制下一次带工具。
3. **事实可复现**：整轮 0 次实验 → 报告自动加前缀 `[无实验输出·未验证]`（已有的
   `[无逐字证据·未验证]` 机制保留）；协调者 brief 写入「命令+原始输出+exit code」纪律，
   未验证报告只能当线索。
4. **搜索委托 + 阶段门禁**：brief 增加「本地有 oracle 就写批量脚本后台跑」「每段以 oracle
   输出为唯一完成证据」。

新增测试 `tests/smoke_strategy.py`（分类器/上下文/纪律文本），一键回归 24/24 PASS。

## 实测：rev 第 4 轮（800k/45min，同样题目、同样预算）

| 指标 | rev2（无护栏） | rev3（无护栏） | **rev4（有护栏）** |
|---|---|---|---|
| shell 调用 | 87 | 121 | 118 |
| 其中实验类 | ≈30 | ≈60 | **≈38** |
| 其中静态阅读类 | 2* | 6 | **0** |
| gdb 相关动作/提及 | 21 | 7 | **101** |
| 策略护栏触发 | 0 | 0 | 1（静态连击→强制实验） |
| tokens / calls | 501k/92 | 795k/129 | 802k/142 |
| 结果 | 未解出 | 未解出 | 未解出 |

*rev2 的 read 少是因为它大部分 shell 是 gdb/脚本；口径参考。

结论：**护栏按设计生效**——静态阅读被压到 0，动态观测（gdb）从个位数涨到 101 次提及；
agent 从"读代码推断"切换成"跑实验验证"。但 rev 仍未解出：最后卡在「checker 的 EXPECTED
硬编码表 + 不动点求解」，agent 在预算耗尽前正开始搭"进程内 FreeType harness"。

## 教训（对我自己）

- 同类策略连续跑 4 轮才读思考过程是流程错误；正确顺序是 **1 轮 → 诊断 → 改策略 → 续跑**。
- "再加 800k" 不是策略；每次续跑必须带来新工具/新观测/新纪律，否则只是烧钱。
- 这类题需要的是"决定性观测 + 批量搜索"，LLM 的职责是设计实验和解释结果，不是替代搜索。
