# 集群-B 收益实验设计（低成本分阶段）

> 目的：在花 token 之前，把「v3 到底要不要在 `config/agent.json` 里加 `clusterb_k: 3`」这件事变成可判定的实验。
> 本文所有数字都标了口径与出处；**未实测的部分明确标注为待测**。
>
> **状态更新（2026-10-29）**：① 接线已完成并通过离线验收（`Crew.solve_clusterb`，k 缺失=旧行为，见 §6.1）；② P1（SAMPLE-007，k=3，cap 511k）**实测无收益**：493,615 tokens（+216%）仍未解出，且黑板事实继承实测为空盒 → 详见 §6 与 `runs/clusterb-exp-summary.md`。③ 待决：归因臂 k=1@511k、修黑板继承源（§6.3）。

---

## 0. 结论先行（两句话）

1. **前置阻塞（已实测，已修）**：集群-B 的入口 `Solver.solve` 在**容器/生产路径上不可达**——容器走 `Crew.solve → crew._delegate → Solver.run_role`，`clusterb_k` 曾是**死开关**。**现已接线**：容器入口改走 `Crew.solve_clusterb`（§6.1），k 缺失时与旧行为逐字等价。
2. **免费先验偏冷（已实测）**：3 道 k=1 web 失败题的转录里，重复调用率仅 **0–10.5%**、账本拦截 **0 次**；失败形态是「150k 预算在 16–34 次调用内被上下文膨胀吃掉」，不是「反复烧同一招」。所以 k=3 的机制空间主要来自**上下文重置 + 黑板事实继承**，不是「防重复」。这决定了候选题应选**死前已拿到关键线索**的那种（SAMPLE-007）。

---

## 1. 要回答的决策

| 决策 | 现状 | 实验能给出的答案 |
|---|---|---|
| v3 的 `config/agent.json` 加不加 `clusterb_k: 3` | 不加（默认关，k=1 等价旧版） | 「k=3 相对 k=1 有/无正信号」——**只有在接线之后才有意义** |

---

## 2. 前置发现：cluster-B 在容器路径上是死开关

### 2.1 代码路径（行号核实）

| 位置 | 事实 |
|---|---|
| `agent_main.py:398-403` | 容器只接受 `run_mode == "instance"`，其他模式直接拒绝 |
| `agent_main.py:487-488` | 容器入口固定走 `Crew(...)` → `crew.solve(challenge_id)` |
| `crew.py:453` | `crew._delegate` 调的是 **`solver.run_role(...)`**，不是 `solve` |
| `solver.py:721-727` | 集群-B 只在 `Solver.solve` 里分支（`clusterb_k > 1 → _solve_clusterb`） |
| `cli.py:408` | 全仓唯一真实调用 `Solver.solve` 的地方是 **`solve` 子命令**（另有 `demo`） |

→ 也就是说，之前「真机 A/B 三次尝试机制全对」那轮，跑的是 **单智能体 `solve` 路径**（`run.cmd solve`），不是容器/crew 路径。

### 2.2 运行时探针（可复现）

`runs/_probe_clusterb_wiring.py`：把 `Solver.solve` 换成「一调用就抛异常」的桩，用离线假 MCP + 脚本化 LLM 跑完 `crew.solve`（delegate → report → submit）。

实测输出（2026-10-29）：

```
clusterb_k = 3
solver_solve_called = 0          ← 桩一次都没被触发
submitted = true  reason = flag_accepted
summary_has_clusterb = false     ← 汇总里没有集群-B 段
solve_attempts = []              ← state 里没有任何尝试记录
→ 判定：cluster-B 在 crew（容器）路径上不可达
```

可信度：**高**（代码路径 + 运行时探针双向一致）。

---

## 3. 免费先验：k=1 的失败到底是「重复烧钱」还是「预算/上下文耗尽」

工具：`runs/_clusterb_mine.py`（只读转录，0 LLM / 0 平台调用）。
候选：`sample-bank-v1/v2`（2026-10-28）里 3 道 web 失败题，**crew 路径、150k 题级预算**。

| 转录 | calls | dup_calls | dup_rate | 账本行 | 去重 URL | tokens | 尾部工具 |
|---|---|---|---|---|---|---|---|
| v1 SAMPLE-007 | 34 | 3 | 8.8% | 3 | 17 | 150,473 | http_request, list_attachments, delegate |
| **v2 SAMPLE-007** | 16 | 0 | **0%** | 4 | 3 | 146,721 | delegate×3 |
| v1 SAMPLE-008 | 20 | 2 | 10.0% | 3 | 4 | 145,131 | http_request, http_burst, delegate |
| **v2 SAMPLE-008** | 19 | 2 | 10.5% | 3 | 3 | 144,006 | http_burst, start_instance, delegate |
| v1 SAMPLE-013 | 21 | 0 | 0% | 4 | 8 | 143,945 | delegate×3 |
| **v2 SAMPLE-013** | 18 | 0 | **0%** | 3 | 3 | 150,262 | shell, shell, delegate |

口径：`dup_calls` = 与前面某次 `(tool, 归一化 args)` 完全相同的调用；`tokens` = 转录各行 `usage.total_tokens` 求和（与 `matrix.jsonl` 的 153k/158k 对得上，差额来自未落 usage 的行）。

**读法**：
- 重复率很低（合计 7/128 = 5.5%），账本去重几乎没拦住东西 → **k=1 不是「反复试同一招」死的**。
- 单次调用的 prompt 从 ~9k token 涨到 ~15k token（SAMPLE-007：9,256 → 14,888），16 次调用就把 150k 吃光 → **死在上下文膨胀 + 预算**，`max_steps=12` 还没用满。
- SAMPLE-007 死前已经拿到关键线索：首页注释指向 internal operations API、`/status` 200、边缘代理应剥离 `X-Internal-Request` 头……**事实已到手但没额度继续** → 这正是黑板继承能接上的形态。

**结论（先验，非实验结论）**：k=3 的预期收益来自「3 段全新上下文 + 事实继承」，因此候选题要选 **SAMPLE-007**（线索到手型）；SAMPLE-008 已知有判题答案形态歧义（单点未知题），SAMPLE-013 尾部在 shell 空转，优先级都低于 007。

---

## 4. 实验设计

### 4.1 假设与指标

- **H1**：在「每次尝试预算≈基线」的前提下，k=3 比 k=1 更容易解出或更接近提交。
- **主指标**：`summary.submitted`（平台 `flag_accepted` / `exploit_verified`）。
- **副指标**（都从转录/汇总免费取）：
  1. 候选 flag 产出（`summary.no_candidate` 字段）；
  2. 尾步动作 `tail_action ∈ {verify_exploit, verify_answer, submit_flag}`（`retry.NEAR_ACTIONS`）；
  3. **黑板继承生效**：第 2/3 次尝试的 prompt 里出现 `[前次尝试已确认事实]` 且事实非空（转录 grep）；
  4. 每尝试 tokens/calls/结束原因（`state.solve_attempts` 3 条）。
- **判定规则**：
  - solved 或副指标 1/2 命中 → 正信号，进入归因对照（§4.5）；
  - 3 次都 `attempt_cap`、且第 2/3 次事实为空 → **机制没生效**，先修接线，实验作废；
  - 3 次都 `budget_exhausted`（题级真耗尽）→ 负信号，说明预算切三份不如一口气。

### 4.2 对照组：零成本复用（不用重跑 k=1）

| 题 | 结果 | tokens | 时长 | 结束原因 | steps | 出处 |
|---|---|---|---|---|---|---|
| SAMPLE-007 | ✗ | 156,230 | 320 s | budget_exhausted | 4 | `runs/sample-bank-final/matrix.jsonl:7` |

代码差异：10-28 基线（`sample-bank-v2`）→ HEAD 之间只多了 ①集群-B（**关闭时等价旧版**）、②step_limit 归因修复（只改 reason 标签）、③`llm.py` thinking 开关（未设环境变量=不生效）→ 可当等价对照，**但需在报告里注明这一条假设**。

### 4.3 处理组：k=3 的预算口径（不拆预算）

`Budget.compute_attempt_caps`（`budget.py:108-117`，`solver.py:744-752` 接线）：
`第 i 次尝试切片 = (题级剩余 token) × (1 − 0.12) ÷ 剩余尝试数`

取题级上限 **511,000**：

| 尝试 | 切片上限 | 说明 |
|---|---|---|
| #1 | (511k−0)×0.88÷3 ≈ **149.9k** | 与 k=1 基线 150k **同口径** |
| #2 | (511k−已用)×0.88÷2 ≈ 159k | |
| #3 | (511k−已用)×0.88÷1 ≈ 178k | |

调用次数切片：`(80−已用)×0.88÷3 ≥ 4`，第 1 次约 23 次调用（够用）。

### 4.4 两条可选执行路径（**二选一，需批准**）

| 路径 | 做什么 | 成本 | 结论可用性 |
|---|---|---|---|
| **A. 先接线再测**（预测性强） | 改 `crew._delegate`：k 次 `run_role` + 温度/策略差异化 + 黑板事实注入 + `run_role` 导出 facts/dead_ends + caps 用共享预算；补离线 smoke；再跑 P1 | 代码 ~60–100 行 + 离线验证 0 token；P1 ≤511k | 直接预测 v3 行为 |
| **B. 只在 solo 路径测**（零改动） | 用 `solve` 子命令（`clusterb_k=3`、cap 511k）跑 007；另补 k=1 solo @150k 对照 | ≤511k + ≤150k ≈ 661k | 只回答「机制在 solo 模式」；**不直接外推 crew**（生产是 crew） |
| **C. 暂不测** | 承认死开关 + 低重复先验，v3 保持 `clusterb_k` 关闭，把预算留真实赛题 | 0 | 不回答收益问题 |

### 4.5 归因对照（P1 解出/近目标时才做）

**本设计最大的混淆项**：k=3@511k vs k=1@150k 同时改变了「尝试次数」和「总预算」，解出也无法归因。要拆开必须再加一臂 **k=1@511k**（成本 ≤511k）：k1@511k 也解出 → 是预算的功劳；只有它失败而 k3 解出 → 才是多尝试的功劳。

### 4.6 成本与时间（上限口径）

| 阶段 | 内容 | token 上限 | 墙钟 |
|---|---|---|---|
| P0 | 免费分析（**已完成**） | 0 | — |
| P1 | k=3 pilot，SAMPLE-007，cap 511k | 511k | ~25 min |
| P1′ | （仅当 P1 正信号）k=1@511k 归因臂 | 511k | ~15–20 min |
| P2 | （仅当 P1/P1′ 给出可归因正信号）SAMPLE-013 两臂 | ~1.02M | ~50 min |

**建议只批 P1（≤511k）**，跑完看数据再批下一阶段。

### 4.7 停止规则（任一命中即停，写进报告）

1. 起实例失败 ≥2 次（上游限流）→ 判 `env_bound`，不计入结论；
2. LLM 504/限流连续失败 → 停；
3. 触 511k token 或 40 min 墙钟 → 停；
4. 转录里没有任何 `[前次尝试已确认事实]`（第 2 次 attempt）→ 判「机制未生效」，不花钱扩测；
5. 实例配额冲突（跑前必须 `runs/_instances.py` 确认 0 实例，跑后清空）。

### 4.8 执行清单（P1）

```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONUNBUFFERED="1"
$py="$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
# 0) 前置：确认 0 实例 / key 就绪
& $py runs\_instances.py
# 1) 驱动（待批后新增）：复用 _bench_matrix 骨架，进程内注入 cfg.extra["clusterb_k"]=3，
#    cap=511000，模型 DeepSeek-V4.1-Flash，只跑 SAMPLE-007
& $py runs\_clusterb_exp.py --id SAMPLE-007 --k 3 --cap 511000 --run-id clusterb-p1-007
# 2) 验收：必须有三样
#    runs/clusterb-p1-007/.../state.json 里有 3 条 solve_attempts
#    转录里第 2/3 次尝试出现黑板事实
#    matrix 行里 clusterb.attempts=3
# 3) 收尾：清实例
& $py runs\_instances.py --stop
```

产物：`runs/clusterb-p1-007/`（转录 + matrix.jsonl）、`runs/clusterb-exp-summary.md`（一页结论 + 钱花在哪）。

---

## 5. 需要先拍板的 3 个问题

> **已拍板（2026-10-29）**：① 路径 A「先接线再测」；② P1 预算 511k。③ 语义选**challenge 级整轮重跑**（delegate 级重试在语义上不成立：专家正常以 `report` 收尾，重试已交付的派活只会白烧）。

1. **路径选 A / B / C？**（A 要改生产代码，风险与预测性都最高；B 零改动但结论外推弱；C 不花钱直接放弃）
2. **P1 预算上限 511k 可以吗？**（不改就是 511k，改小则每次尝试切片不足 150k，与基线不同口径）
3. 若走 A：**接线方案**选「delegate 级 k 次重试」还是「challenge 级整轮重跑」？（前者省 token、语义更贴黑板继承；后者改动更小但 k 次会把协调者开销也乘 k）

---

## 6. 结果回填：接线 + P1 已实测（2026-10-29，详见 `runs/clusterb-exp-summary.md`）

### 6.1 接线（已落地，零回归）

- `Crew.solve_clusterb()`（`src/ctfy_agent/crew.py:1012`）＝容器/生产唯一入口；`k<=1` 直接回落 `Crew.solve()`，与旧版逐字等价。
- 每次尝试 = 一次完整 `Crew.solve()`：预算切片（`caps_fn`，`crew.py:1041`：`(题级剩余)×(1-reserve)÷剩余尝试数`）、温度覆盖（协调者+专家同档）、黑板文本前置到首发 brief、中间尝试保留实例、末次交回默认释放；每次尝试落 `state.solve_attempts`（index/strategy/temperature/reason/tokens/calls）。
- 入口已切：`agent_main.py`（容器）、`cli.py` crew 与 auto 两处 → 全部走 `solve_clusterb`（k 缺失时等价旧路径）。
- 离线验收：`tests/smoke.py` **106 passed / 0 failed**（新增 `clusterb_crew` 7 条断言）；`runs/_probe_clusterb_wiring.py` **ALL PASS**（k 缺失=单次/无 clusterb 段；k=3=两次尝试+黑板进 prompt+提前早停）；既有 4 个 step 验收脚本与 retry 自测全 exit 0。

### 6.2 P1 实测（SAMPLE-007，k=3，cap 511k）

| 指标 | 基线 k=1（10-28） | P1 k=3（10-29） |
|---|---|---|
| 实际 tokens | 156,230 | **493,615（+216%）** |
| calls | 16 | 48 |
| 解出 | 否 | **否** |
| 收尾 | budget_exhausted | attempt_cap ×3 |
| 段内 dup_rate | 0.0 | 0.221 |

**判定：无收益**（按 §4.7：未解出 + 非 near-miss → 不进 P2、不默认进取因臂）。**但适用范围必须限定**：本次黑板注入两次全是「（无）」——`_ledger_facts` 只收 `ledger.observations`（只在 flag 形态输出时采集，`ledger.py:522`），本题是 proof-of-exploitation 无 flag → observations 恒为 0；专家 `report` 结论没有被纳入继承源。**所以本实验只测到「预算切片 + 温度差异化」，核心机制（事实/死路继承）实测未生效。**

### 6.3 由结果产生的两个新待决项

1. **归因臂 k=1@511k**（纯配置、~511k）：区分「预算不够」还是「切三段有害」——本次 P1 无法区分这两者。
2. **修黑板继承源**（改代码 ~30 行）：把 `report` 结论 + `Tried` 方向 + `refuted` 死路纳入继承，否则任何 k>1 都只是「重复侦察 + 上下文重置」（实测 dup 22.1%）。

### 6.4 归因臂已跑完（2026-10-29）：**预算不是瓶颈，切分尝试才是伤害 → 最终结论「v3 不启用 cluster-B」**

| 臂 | 预算 | 实际 tokens | 目标达成 | 平台记分 |
|---|---|---|---|---|
| 基线 k=1 | 150k | 156,230 | 否 | 否 |
| P1 k=3 | 511k | 493,615 | **否** | 否 |
| 归因 k=1 | 511k | **313,076** | **是**（`/status` → `worker_alive:false`，带 `X-Internal-Request` 的 POST 返回 200） | 否（本地收尾释放了实例，sidecar 无法轮询；容器保持实例，见报告 §10.2） |

结论：k=1 用 313k 就完成了整条链，k=3 花了 493k 却因每次 ~150-175k 被切片掐断 + 上下文重置，**没有任何一次走到最后一步**。→ `config/agent.json` **不加 `clusterb_k`**（保持 k=1），接线代码保留但默认关闭。
附带产物：修掉了「LLM 读超时裸 `TimeoutError` 穿透、整题报废」的缺陷（`llm.py` 归一为可重试 `LLMError` + 1 次重试；容器默认读超时 120→240s），并在 k=1 实跑中验证过一次专家超时被正确吸收。

---

## 附：可复现命令

```powershell
# 免费先验
& $py runs\_clusterb_mine.py <transcript.jsonl> [...]
# 接线探针（离线，0 token）
$env:PYTHONPATH="$PWD\src;$PWD\tests"; & $py runs\_probe_clusterb_wiring.py
```

| 证据文件 | 说明 |
|---|---|
| `runs/_clusterb_mine.py` | 转录量化（本轮新增，免费） |
| `runs/_probe_clusterb_wiring.py` | 容器路径死开关运行时探针（本轮新增，免费） |
| `runs/sample-bank-final/matrix.jsonl` | k=1 基线（10-28，crew，150k） |
| `src/ctfy_agent/retry.py`、`budget.py:108-117`、`solver.py:721-752` | 机制与切片口径 |
| `runs/_verify_clusterb_step4.py` | solo 路径 k=2 机制离线验收（ALL PASS，10 条断言） |

## 附：证据缺口（诚实标注）

- 交接文档里「真机 A/B：SAMPLE-001 k=1 解出 / SAMPLE-008 k=3 三次尝试」这轮**在本仓库没有留下产物**：`runs/` 下没有任何含 `clusterb` 的 run 目录，143 个 run 的 `state.json` 里也没有 `solve_attempts` 记录（全仓 grep 命中 0）。因此这两条数字**无法在本仓库复验**，只能作为口头历史；本文不把它当作已证事实使用。
- 10-28 `sample-bank-*` 基线的代码提交时间（10-28 21:31）早于集群-B 提交（10-29 08:22 起）→ 基线确实是「无集群-B」的 k=1 行为，可作对照。