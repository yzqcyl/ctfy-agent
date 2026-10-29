> 本文件是 `runs/clusterb-exp-summary.md` 的入库副本（runs/ 被 .gitignore 忽略），内容一致；源文件含同名数据。

# 集群-B 收益实验 · P1 实测报告（SAMPLE-007）

> 日期：2026-10-29 10:30–10:35（UTC+8）｜执行：Lead｜预算批准：511,000 tokens（用户已批）
> 结论一句话：**按当前接线的集群-B（k=3）在同一题上花掉 3.16 倍 token（493,615 vs 基线 156,230），仍未解出，判定「无收益」；且黑色板事实继承机制实测为空、未生效——本实验没有测到机制的核心假设。**

---

## 1. 实验设置（实际跑的，不是设计稿）

| 项 | 值 |
|---|---|
| 题 | SAMPLE-007 `TaskFlow — Knock Out the Job Worker`（web/easy，proof-of-exploitation，无 flag，判题靠 sidecar 轮询 worker 是否下线） |
| 路径 | **容器同款生产路径**：`python -m ctfy_agent ... crew` → `Crew.solve_clusterb` → 每尝试 `Crew.solve`（本次接线后新增，`src/ctfy_agent/crew.py:1012` `solve_clusterb`） |
| 模型 | `deepseek-v4.1-flash-1017`（DashScope 兼容口；`.env` 口径） |
| 题级预算 | 511,000 tokens / 80 calls（`AGENT_MAX_TOKENS_PER_CHALLENGE=511000`） |
| k | 3，切片公式 `(题级剩余)×0.88÷剩余尝试数`（`src/ctfy_agent/crew.py:1041-1056` `caps_fn`） |
| 温度 | 0.2 / 0.5 / 0.8（`STRATEGY_SPECS`，协调者+专家同档覆盖） |
| 实例 | 自动起 1 个；中间尝试保留、最后一次按默认释放（实测只起 1 次，见 §3.5） |
| 命令 | `python -u -m ctfy_agent --config runs/_clusterb_k3_config.json --run-id clusterb-p1-007b crew SAMPLE-007` |
| 产物 | `runs/clusterb-p1-007b/{state.json, console.log, transcripts/SAMPLE-007.jsonl, summary.json}` |

## 2. 主结果（state.json 实测）

| 指标 | 基线（10-28 k=1） | P1（k=3） | 变化 |
|---|---|---|---|
| 题级 token 上限 | 150,000 | 511,000 | 3.41× |
| **实际消耗 tokens** | **156,230** | **493,615** | **+216.0%** |
| LLM 调用次数 | 16 | 48 | 3× |
| 尝试数 | 1 | 3 | — |
| 收尾 reason | `budget_exhausted` | `attempt_cap`（三次皆） | — |
| 解出 | 否 | **否** | 无差别 |
| 与其标准对照 | `runs/sample-bank-final/matrix.jsonl` SAMPLE-007 行 | `runs/clusterb-p1-007b/state.json` | — |

**判定（按 `docs/experiment-clusterb-benefit.md` §4.7 停手规则）：P1 未解出、且不属于 near-miss（全程 0 次 `verify_exploit`/`verify_answer`，尾动作 `delegate`）→ 集群-B 在本设计下无收益；不进入 P2（SAMPLE-013），不默认进入归因臂。**

## 3. 逐尝试归因（转录实测，`runs/clusterb-p1-007b/transcripts/SAMPLE-007.jsonl`）

| attempt | 温度/策略 | 收尾 | tokens | calls | 工具面 | distinct URL | 段内 dup |
|---|---|---|---|---|---|---|---|
| #0 | 0.2 / 默认首卡 | attempt_cap | 151,120 | 15 | http_request×4, fuzz×1, http_burst×7, http_map×2 | 3 | 2 |
| #1 | 0.5 / 换工具面 | attempt_cap | 167,721 | 16 | http_request×14, fuzz×3, http_burst×2, shell×2 | 13 | 1 |
| #2 | 0.8 / 反推视角 | attempt_cap | 174,774 | 17 | http_request×7, shell×8, fuzz×1 | 7 | 1 |
| 合计 | — | — | 493,615 | 48 | — | 17 | 4 |

早期节奏：每次尝试 prompt 从 1,488 / 2,155 / 2,819 起，末尾涨到 10,966 / 11,691 / 11,560（单次调用上下文线性膨胀，与基线同病）。

### 3.1 机制①（温度/策略差异化）：**部分生效，但不足以改变结局**
三次尝试的路线确实分叉了（#1 把探测面从 3 个 URL 扩到 13 个，摸到 `/internal/ops`、`/api/internal/ops`、`/internal/ops/pause` 一族；#2 继续 `/ops/pause`、`/internal/pause`），但**没有任何一次走到「带 `X-Internal-Request` 头 POST pause → 复验 /status worker_alive=false」这条唯一收口动作**。

### 3.2 机制②（黑板事实继承）：**实测完全未生效——这是本次最大的发现**
转录里两次注入（`role=clusterb`）的内容体都是：

```
[前次尝试已确认事实] （无）
[前次尝试的死路（勿重复）] （无）
[待验候选值] （无）
```

原因（代码级）：`Crew.solve_clusterb` 的黑板来源是 `_ledger_facts()`（`src/ctfy_agent/crew.py:232` 附近），只读 `Ledger.observations`；而 observations 只在工具输出命中「flag 形态」时才被 `_harvest_observation` 采集（`src/ctfy_agent/ledger.py:522`）。本题是 proof-of-exploitation、**根本没有 flag**，所以每次尝试 `observations=0`（末次 ledger 行：`{"hypotheses":17,"refuted":0,"open":17,"observations":0}`）→ 黑板恒为空。
专家的**报告结论**（如「/internal/ops 存在」「需要 X-Internal-Request」）落在转录 `report` 行与 `Tried` 记录里，**没有被纳入继承源**。

→ 所以本次实验实际只测到「预算切片 + 温度差异化」，**没测到设计中的核心机制（事实/死路继承）**。不能据此断言 cluster-B 机制无效，只能断言「按当前接线的实现无效」。

### 3.3 代价：重复侦察
整份转录 `calls=68, dup=15, dup_rate=0.221`，基线同题 `calls=16, dup=0, dup_rate=0`（`runs/_clusterb_mine.py` 口径）；账本拦截 9 次（基线 4 次）。主要重复形态：每次尝试都重新 `get_challenge` / `list_challenge_attachments` / 重扫同域路径 —— 正是 §3.2 黑图为空的下游后果。

### 3.4 预算切片公式：**实测正确**
三次尝试实际消耗 151,120 / 167,721 / 174,774，均落在各自切片（149,893 / 158,840 / 169,099）+ 单次调用溢出（≤ ~7k）附近；总消耗 493,615 ≤ 511,000。切片/`attempt_cap` 分流/早停全部按设计工作（`attempt_cap` 三次都被正确判为「可继续」，没有误判成题级耗尽）。

### 3.5 实例复用：**实测生效**
`console.log` 全程只有 1 行 `[实例]`（起实例），attempt#1/#2 直接复用；跑完 `runs/_instances.py` 显示 **0 个实例**（最后一次尝试按默认释放）。这条接线省掉 2 次重起。

## 4. 前置事故（0 token 损失，已修）

首跑 `runs/clusterb-p1-007`（10:24）3 次尝试全 0 token 失败，转录错误原文：

```
HTTP 404 ... "The model `DeepSeek-V4.1-Flash-1017` does not exist or you do not have access to it." (model_not_found)
```

根因：`config/agent.json` 每个角色硬编码 `"model": "DeepSeek-V4.1-Flash-1017"`（旧 paratera 口径，大写），而 `.env DEEPSEEK_MODEL=deepseek-v4.1-flash-1017`（`.env` 注释原文：**模型名区分大小写，小写才有效**）。
- 影响面：**本地 CLI 路径**（`cli.build_config` 不覆盖角色模型）。
- **容器不受影响**：`src/ctfy_agent/agent_main.py:217` 明确 `spec.pop("model", None)`（注释「网关会按 LLM_MODEL 改写 model」）→ 镜像内一律用平台注入的 `LLM_MODEL`。
- 修法（本次实验配置）：`runs/_clusterb_k3_config.json` 把所有角色 model 改成小写可用的 `deepseek-v4.1-flash-1017`，与 10-28 基线驱动 `runs/_bench_matrix.py:114-117` 的做法一致。

## 5. 成本账

| 项 | tokens |
|---|---|
| 404 首跑（`runs/clusterb-p1-007`） | 0 |
| P1 正式跑（`runs/clusterb-p1-007b`） | 493,615 |
| 合计 | **493,615**（批准 511,000，余 17,385） |

## 6. 结论与建议

1. **v3 不启用集群-B**：k 默认 1（配置不写 `clusterb_k`），已实测零回归（smoke 106/106、探针 A 场景、容器入口同路径）。当前接线不带来收益，却可能把 800k 切成 3×~230k。
2. **本次负面结论的适用边界**：只覆盖「预算切片 + 温度差异化 + 空黑板」；黑板继承因数据源缺陷（只收 flag 形态 observations）**从未生效**。要给出对 cluster-B 的公平判决，必须先修继承源（专家 `report` 结论 + `Tried` 方向 + `refuted` 死路）。
3. **建议的下一步（需批准，按信息量排序）**：
   - **B. 归因臂 k=1 @ 511k**（纯配置，~511k）：回答「SAMPLE-007 到底是钱不够，还是被切成 3 段害的」。若 k=1@511k 解出 → 切分是主因，cluster-B 必须依赖有效继承才有意义；若同样不解 → 预算/路线不是瓶颈，应换题或换机制，别再投钱。
   - **A. 修黑板后再跑一次 P1**（改代码 ~30 行，~500k）：把继承源换成「专家 report 结论 + 账本 tried/refuted 方向」，验证 §3.3 的重复侦察是否被吃掉。
   - C. 接受负面、把精力放回提交与真实题（不花钱）。
4. 无论选哪个，**提交相关的事（v3 重建 + 提交）与本次实验解耦**：镜像不依赖 `clusterb_k`，也不依赖本次新增的 `solve_clusterb` 默认行为。

## 7. 复现

```powershell
$env:AGENT_MAX_TOKENS_PER_CHALLENGE="511000"; $env:PYTHONPATH="E:\ctfy_project\src"
python -u -m ctfy_agent --config runs\_clusterb_k3_config.json --run-id <run-id> crew SAMPLE-007
python runs/_clusterb_summary.py runs\<run-id> --baseline-tokens 156230 --baseline-reason budget_exhausted
python runs/_clusterb_mine.py runs\sample-bank-v2-deepseek-v4.1-flash-1017-SAMPLE-007\transcripts\SAMPLE-007.jsonl runs\<run-id>\transcripts\SAMPLE-007.jsonl
```

## 8. 证据索引

- `runs/clusterb-p1-007b/state.json`（token 总量、solve_attempts 三条、status=stalled）
- `runs/clusterb-p1-007b/console.log`（逐事件：1 次实例启动、3 次 delegate、3 次预算切片耗尽回执）
- `runs/clusterb-p1-007b/transcripts/SAMPLE-007.jsonl`（146 行；2 条 `role=clusterb` 空黑板注入；末次 ledger stats observations=0）
- `runs/clusterb-p1-007b/summary.json`（`runs/_clusterb_summary.py` 结构化输出）
- `runs/clusterb-p1-007/transcripts/SAMPLE-007.jsonl`（404 事故原文）
- 基线：`runs/sample-bank-final/matrix.jsonl`（SAMPLE-007 行）、`runs/sample-bank-v2-...-SAMPLE-007/transcripts/SAMPLE-007.jsonl`
- 代码：`src/ctfy_agent/crew.py:1012`（`solve_clusterb`）、`src/ctfy_agent/crew.py:232`（`_ledger_facts`）、`src/ctfy_agent/ledger.py:522`（`_harvest_observation`）、`src/ctfy_agent/agent_main.py:217`（容器 pop 角色 model）

---

## 9. 归因臂 k=1@511k：**3 次均未跑完，结论待定（不是负面结果）**

经你批准后开跑归因臂（SAMPLE-007，k=1，cap 511k，`runs/_k1_511k_config.json`），三次都在**模型读超时**处中断：

| run | 中断点 | 已花 tokens | calls | 现象 |
|---|---|---|---|---|
| `runs/clusterb-p1-007-k1` | 第 7 次调用 | 53,210 | 6 | `FAIL TimeoutError: The read operation timed out`（当时 LLM 读超时=120s） |
| `runs/clusterb-p1-007-k1b` | 第 12 次 | 118,305 | 11 | 同上（120s） |
| `runs/clusterb-p1-007-k1c` | 第 14 次 | 127,723 | 13 | 同上；已把 LLM 读超时抬到 **300s**，实测仍是**挂满 300s** 才超时（11:30:10 最后一条转录 → 11:35:29 崩溃，差 319s） |

**归因臂合计已花 299,238 tokens（全部为基础设施中断的沉没成本），尚未得到「k=1@511k 能否解出」的答案。**

### 9.1 根因（已实测定位，非猜测）

`src/ctfy_agent/llm.py:58-101` `_post()` 只捕获 `HTTPError` / `URLError` / `JSONDecodeError`，**没有捕获 `TimeoutError`**（读响应体超时抛的就是裸 `TimeoutError`，它不是 `URLError` 的子类）→ 异常一路穿透 `crew.solve`（只 catch `LLMError`/`BudgetExceeded`）→ CLI 顶层兜底（`cli.py:704`）打印 `FAIL TimeoutError`，**整个 run 报废**。
- 时间戳证据：k1c 最后一条转录 11:30:10，state/console 落盘 11:35:29 → 差 **319s ≈ 300s 读超时 + 收尾开销**，说明是模型侧**单次请求挂起**（长生成或服务端卡住），不是本地网络瞬断。
- 对照：10-28 基线 14 题里没有一次 LLM 读超时（`runs/` 下仅两处 `TimeoutError` 命中，都是 shell 工具输出里的字符串，非致命）。

### 9.2 对提交镜像的影响（重要，且与本次实验无关也成立）

容器路径 `agent_main` 顶层 `except Exception`（`agent_main.py` 收尾块）会把这类异常兜成 `result.json` 的 `stop_reason=halted`——**协议合规（能拿分），但这道题直接放弃**。比赛里一次 120s 模型挂起 = 浪费一道题。建议 v3 一起修：`_post()` 把 `TimeoutError/socket.timeout` 归一成 `LLMError(retryable=True)` 并做有界重试（2 次，指数退避），同时把容器默认 LLM 读超时从 120s 抬到 ≥240s。

### 9.3 待你拍板（已超出"B ≈511k"的批准口径 299k，故暂停不再自动重试）

1. **修 `llm.py` 超时+重试后重跑归因臂**（≤511k，另加改动/测试）；
2. **放弃归因臂**：P1 的负面结论已经足以支撑「v3 不启用 cluster-B」，k=1@511k 只影响"要不要继续投资 cluster-B"，不影响提交决策；
3. 折中：用 **k=1@250k** 做一次便宜探针（~250k），只回答"加预算有没有方向性改善"。

---

## 10. 归因臂重跑结果（`runs/clusterb-p1-007-k1d`）：**k=1@511k 把题做出来了**

修复 `llm.py` 后（把读超时归一为可重试 `LLMError` + 1 次重试），第四次执行完成，**没有崩溃**：

| 指标 | k=1@511k（本次） | P1 k=3@511k | 基线 k=1@150k |
|---|---|---|---|
| 实际 tokens | **313,076** | 493,615 | 156,230 |
| LLM calls | **29** | 48 | 16 |
| 协调步数 | 4 | 3（每次尝试） | 4 |
| 目标达成（worker 下线） | **是** | 否 | 否 |
| 平台记账（`my_solved_ids`） | **否（见 10.2）** | 否 | 否 |
| run 退出码 | 5 = `solve`（未提交，非崩溃） | 5 | 5 |

**达成证据（转录原文，`runs/clusterb-p1-007-k1d/transcripts/SAMPLE-007.jsonl`）**：
1. 先拿到 `/status` → `{"ticks":578,"worker_alive":true}`；`POST /api/worker/stop` 无头 → `403 {"error":"forbidden: internal requests only"}`（正确定位鉴权在来源头）。
2. 带 `X-Internal-Request: true` 再 POST → **200**；随后 `/status` → `{"ticks":847,"worker_alive":false}`，协调者日志原文「**已达成题目要求的效果：worker 已下线**」，并复验两次 `worker_alive:false`。
3. `reason=coordinator_done`、`tail_action=delegate`、`no_candidate=True`（本题无 flag，本地 `submitted` 恒 False）。

### 10.1 归因结论（本次实验的最终答案）

- **预算不是瓶颈**：k=1 在 313,076 tokens（远低于 511k 上限）内完成了「侦察 → 定位内部 ops 端点 → 绕过来源头 → 暂停 worker → 复验」整条链。
- **切分尝试是伤害**：k=3 花掉 493,615 tokens（多 58%），三次尝试各自在 ~150-175k 处被切片掐断，且每次重启都丢掉了上一段的累积上下文与已定位线索，**没有任何一次走到「带内部头 POST」这一步**。
- 因此：**按当前接线（预算切片 + 温度差异化 + 空黑板）的 cluster-B 对这类「必须一条线走到底」的题是有害的**。v3 保持 `clusterb_k` 不启用（k=1）是正确的。

### 10.2 平台没记账的原因（重要，属于本地实验口径，不是比赛行为）

跑完立即查 `platform.my_solved_ids()`：共 18 道，**不含 SAMPLE-007**。原因：本题是 proof-of-exploitation，判题靠平台 sidecar 轮询「web 层仍在、worker 已下线」；而本地 `crew.solve` 收尾时按默认策略 `stop_instance`（`keep_instance_on_fail` 未设 → falsy），实例被释放 → sidecar 无法继续轮询 → 不记账。
- 对照容器：`agent_main._build_config` 强制 `keep_instance_on_success/fail = True`（`agent_main.py:221-222`，注释「容器内实例生命周期归平台：永不自己 stop_instance」）→ 容器里实例会保留，sidecar 有机会记账。**这是本地实验与容器行为的一处真实差异，值得在 11-15 前用一次「keep_instance_on_fail=True」的本地复跑验证 sidecar 记账链路**（~310k，可选）。

### 10.3 顺带验证：超时修复在真实 run 里救了场

k=1d 转录中协调者 PLAN 原文出现「**上一轮专家超时失败**，但确定性账本显示端点已定位…」——专家遇到一次 LLM 读超时，`_post` 现在把它归一成可重试 `LLMError`，`run_role` 捕获后交回结论，协调者据此继续并最终达成目标。**同一故障在修复前会直接杀死整个 run（§9 的三次崩溃）。**

## 11. 成本总账（2026-10-29 集群-B 实验）

| 项 | tokens |
|---|---|
| P1 k=3（有效实验） | 493,615 |
| 归因臂 3 次超时中断（沉没） | 299,238 |
| 归因臂 k=1@511k（有效实验） | 313,076 |
| **合计** | **1,105,929** |

批准口径：P1 511k + 归因臂 ~511k ≈ 1.02M；实际多用 ~86k（全部来自超时中断的沉没成本，已通过代码修复消除该风险）。