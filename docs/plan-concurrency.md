# 集群-B 方案（同题多次独立尝试 + 极简共享黑板）

> 状态：方案已完善（含 2026-10-28 检查补齐 G1–G4）。
> 本文是设计规格；离线夹具见 `runs/_clusterb_fixtures.py`（spec-by-example，可运行）。
> 纪律：本文及夹具均为纯新增，未触碰 `crew.py`/`ledger.py`/`state.py`/`budget.py` 等共享模块。

## 0. 结论

集群-B = 对**可重试的未解题**做 k∈[1,3] 次独立尝试，尝试间在**策略/温度/首动作**上强制分叉，
通过**极简事实黑板**（只共享事实+死路+候选值，不共享轨迹、不做多 agent 辩论）压低相关失败。
外部证据已核实：重复采样覆盖对数线性增长、且在可验证域收益直接转化
（[Large Language Monkeys](https://arxiv.org/abs/2407.21787)：SWE-bench Lite 1→250 采样 15.9%→56%）；
而多智能体"辩论/聚合"增益极小（[MAST](https://arxiv.org/abs/2503.13657)、[MAD 重评估](https://arxiv.org/abs/2402.18272)）。

## 1. 硬约束（均有一手或代码证据）

| 约束 | 值 | 证据 |
|---|---|---|
| 同题=同实例（有状态） | 多数题 1 实例/队 | [platform.py:22](src/ctfy_agent/platform.py#L22) stop/renew/reset 以 instance_id 为键 |
| 并发 ≤3（agent 自主） | — | QA#26 原文 |
| 单模型注入 | LLM_MODEL 单一 | `agent_main.py` pop 角色 model |
| 单题 token 累计 300k，跨进程继承 | — | [config.py:39](src/ctfy_agent/config.py#L39)、[solver.py:1062](src/ctfy_agent/solver.py#L1062) `set_tokens` 落盘 |
| **单题 LLM 调用累计 20 次** | — | [config.py:40](src/ctfy_agent/config.py#L40)、[crew.py:518](src/ctfy_agent/crew.py#L518)/[solver.py:733](src/ctfy_agent/solver.py#L733) 传入 `Budget(max_calls=…)` |
| instance 操作 5 次/题 | start/reset 都计 | [config.py:34](src/ctfy_agent/config.py#L34)、[solver.py:574](src/ctfy_agent/solver.py#L574) |
| oracle 探针 50 次/题（verify 白试） | — | [config.py:35](src/ctfy_agent/config.py#L35)、[state.py:361](src/ctfy_agent/state.py#L361) |
| 提交次数 limited | `submit_limit` 默认 3 | [config.py:32](src/ctfy_agent/config.py#L32) |
| 单次上下文滑窗 16k | — | [config.py:41](src/ctfy_agent/config.py#L41) |
| 窗口 10h/天；动态计分+总解题时间 tiebreak | — | QA#29/#34 |

## 2. 三阶段编排（G5 补齐分数动机）

| 阶段 | 范围 | 动机 | 预算 |
|---|---|---|---|
| P1 广扫 | 全部题每题目 1 次 | **抢动态分**（早解出=分高，[QA#29] 随解出人数递减） | 单题默认 cap |
| P2 深挖（集群-B 本体） | 仅 P1 未解且「可重试」的题 | **补漏**（把"差一步"的题捞起来） | 剩余池按"差一步度"排序分配 |
| P3 收口 | 窗口 <15% | 保住已有战果 | 保留 ≥5% 给 verify/submit |

## 3. 重试资格判定（G1 补齐：两段式归因）

代码现有 reason 是机制性的（`budget_exhausted`/`no_more_actions`/`no_progress`/`step_limit`/`llm_error`/`flag_accepted`/`exploit_verified`/`partial_solve`/`no_candidate`/`rescued`/`reported`），
**不含语义归因**。需加一个轻量「失败语义归因」前置（读尾步动作 + 机制 reason → 三分类）：

| 语义分类 | 判据（机制 reason + 尾步信号） | 是否重试 |
|---|---|---|
| `near_miss`（差一步） | `budget_exhausted`/`step_limit` 且尾步是逼近目标的动作（如 8 路径全 200、差 verify_exploit） | ✅ 最高优先 |
| `retryable`（打转/缺打法） | `no_progress`/`no_more_actions`/`llm_error` | ✅ 次优先（换策略） |
| `hopeless`（环境/能力） | 归因到 VPN/Linux-only/Windows 引号/DoS 纯 recon 无手段 | ❌ 不重试或降级 1 次 |

- 已 submitted/`my_solved_ids` 命中 → 跳过（启动前预检，成本≈0，缓存 10s）。
- 归因本身免费（读本地 trace，不调 LLM）；不确定时保守归 `retryable`（多花 1 次试探）。

## 4. 两级记账（G2 补齐：token + calls 双维度）

现有 `Budget` 同时管 `max_tokens` 和 `max_calls`（[budget.py](src/ctfy_agent/budget.py)），
必须把这两个**题级累计**拆成「题级池 + 尝试级切片」：

| 级 | token 维度 | calls 维度 |
|---|---|---|
| 题级池 | `challenge_tokens`（默认 300k） | `challenge_calls`（默认 20） |
| 尝试级切片 | `attempt_tokens = pool/剩余k`（留 10–15% 收口） | `attempt_calls = pool/剩余k`（下限 4，保证 attempt 可独立跑一个完整回合） |
| 回收 | 未用完回流题级池 | 同左 |

**要点**：切 calls 比切 token 更苛刻——attempt#1 常耗 10–15 次调用，故 k=3 时 calls 必须放宽或
把「一次尝试的最小有效 calls」提到 6–8，否则后续 attempt 会被 `max_calls` 先杀（而非 token）。

## 5. 实例重置策略（G3 补齐）

| 场景 | 动作 |
|---|---|
| attempt 间默认 | **不 reset**（复用实例残留，省 instance_ops） |
| attempt#1 明显污染（写文件/改配置/冲击服务） | 才 `reset_instance`（记入 instance_ops，与 k 双扣） |
| 有状态题 | 尝试**永远串行**（共享实例并行会互相污染致假失败） |

实例操作预算分配：`instance_ops(5) ≥ start(1) + Σ reset(仅在污染时) + 提交前复核(1)`，
超限由现有 [solver.py:574](src/ctfy_agent/solver.py#L574) 拦截降级为「保持现状继续」。

## 6. 差异化机制（防相关失败）

| 维度 | attempt#1 | attempt#2 | attempt#3 |
|---|---|---|---|
| 打法卡 | `method_cards` 首卡 | 次优卡 + 换工具面（http_request↔shell/curl） | 反推视角（flag 位置/端点枚举/编码形态 ⑥⑦⑧ 起手） |
| 温度 | 0.2 | 0.5 | 0.8（仅"决策步"；tool_calls 参数仍结构化，[G7]） |
| 首动作 | 题面默认入口 | 侦察面（/docs、robots、错误页） | 构造面（畸形输入/边界） |
| 注入上下文 | 仅题面 | 题面+黑板摘要 | 同左+显式"已证伪清单" |

红线：黑板只注入「事实+死路+候选值」三段，**不注入上次完整轨迹**（防锚定）。

## 7. 黑板（G6 补齐：注入模板）

```
[前次尝试已确认事实] …（≤800 字符，去重）
[前次尝试的死路（勿重复）] …（≤400 字符，带原因）
[待验候选值] …（≤300 字符，标来源）
```
- 写入：每次尝试结束合并事实/死路/候选（复用 [ctxcompress.py](src/ctfy_agent/ctxcompress.py) FactsBoard 机制）。
- 继承事实、**不继承判决**：失败尝试的"方向证伪"不得写 ledger 禁用态（[ledger.py:347](src/ctfy_agent/ledger.py#L347) 已知坑）。

## 8. 并发模型

| 题型 | 并发 |
|---|---|
| 有实例（web/pwn/misc 交互） | 串行（实例配额=1 + 状态污染） |
| 无状态（crypto/rev 附件） | 并行 ≤3 |
| 同一题多次尝试 | 有状态题永远串行；无状态题可并行 |

开关正交：`AGENT_CONCURRENCY`（默认1，多题并行）与 `AGENT_ATTEMPTS`（默认1，同题深挖）。

## 9. 结果仲裁（G4 补齐：verify≠submit）

| 机制 | 设计 |
|---|---|
| verify_answer（白试，oracle_limit 50，共享） | 不互斥；k 次尝试共享题级 `oracle_calls` 计数 |
| verify_exploit（通过即平台记 solve） | 与 verify_answer 同属 oracle 组（[crew.py:65](src/ctfy_agent/crew.py#L65)） |
| **submit_flag（写记录，submit_limit 3）** | **题级互斥锁**：一次只允许一个尝试提交；先到先赢 |
| 取消 | 串行：成功后不再启动下一 attempt（编排器 break）；并行：广播 cancel 事件 |
| 状态 | store `attempts[]` 记 `{id,strategy,cap,outcome,tokens,calls,seconds}`；题级 status 仅在真解/耗尽时终态化 |

## 10. k 的动态决定

```
k = clamp(1..3,
          floor(剩余时间 / 剩余未解题 / 单次预估时长),
          floor(剩余题级 calls / 单次最小有效 calls))
```
现实算术：10h 窗口 / 单次 ~1400s ≈ 25 次尝试；14 题广扫后剩 ~11 次 → **现实 k≈2**（少数 3）。

## 11. 改动面（接口级，本方案只许不含此处的实现）

| 位置 | 性质 | 内容 |
|---|---|---|
| 新 `retry.py`（编排器） | 新文件 | 三阶段、两段式归因、两级记账切片、黑板合并/注入、仲裁 |
| `solver.py` | 重构 | 单次求解抽为 `attempt()`；`solve()` 升编排器；attempt 失败不污染 ledger 方向状态 |
| `crew.py` | 小改 | playbook 注入加 seed/偏好参数 |
| `state.py` | 小改 | `attempts[]` + 题级池 |
| `budget.py` | 小改 | 两级记账（token+calls） |
| 开关 | 新 env | `AGENT_ATTEMPTS`(1–3,默认1)、`AGENT_ATTEMPT_MIN_CALLS`、`AGENT_CONCURRENCY`(默认1) |

**向后兼容**：`AGENT_ATTEMPTS=1` 时行为与现状完全一致（默认值）。

## 12. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 有状态实例被并行污染 | 默认串行，并行白名单仅无状态题 |
| attest 预算/token+calls 双杀第 2 次 | 两级记账（§4）；calls 切片下限抬高 |
| 重复提交耗 submit_limit | 提交互斥锁 + 启动前 `my_solved_ids` 预检 |
| 上下文被上次轨迹锚定 | 三段摘要（§7），不注入轨迹 |
| 失败尝试污染方向禁用 | 继承事实不继承判决 |
| temperature 不稳（tool_calls） | 高温仅决策步；端点不支持则退化策略+首动作两维 |
| 时间全砸难题 | 三阶段 + 差一步度排序 + 15% 收口 |
| instance_ops 被 reset 耗尽 | 默认不 reset，仅污染时 reset（§5） |

## 13. 验收（先测再上线）

| 步骤 | 内容 |
|---|---|
| 离线夹具 | `runs/_clusterb_fixtures.py`：必过 / 差一步 / 环境噪 三题，验证编排+归因+切片+黑板 |
| 真机 A/B | 008（差一步）k=1 vs k=3 各 2 次；007（能力缺口）作对照（预期无改善） |
| 指标 | 解出率、墙钟、token+calls/解出题、死路重复率 |
| 采用门槛 | k=3 至少多解 1 题、token/解出题增幅 <2×、死路重复率显著下降；否则只保留 P1+P3 |
| 排期 | 真机 A/B 放 11-14 平台测试当天（顺带验证实例并发/温度参数） |

## 14. 与集群-A 关系

共用调度器+黑板；B 挖深度、A 拖广度。按已核实证据 **B > A**；A 作无状态题堆积时的补充。

## 15. 已核实内部事实索引（供实现对照）

`config.py:32/34/35/39/40/41`（submit 3 / instance_ops 5 / oracle 50 / token 300k / calls 20 / ctx 16k）、
`platform.py:20,22`（verify 白试 / reset 按 instance_id）、`crew.py:65,518`（oracle 组 / Budget max_calls）、
`solver.py:574,1062`（instance_ops 拦截 / token 落盘）、`state.py:156,361`（oracle_calls / bump_oracle）、
`ledger.py:347`（多轮诱导证伪坑）。