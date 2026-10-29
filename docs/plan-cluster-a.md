# 集群-A 方案（无状态题同题 N 路并行）

> 状态：已实施（commit `0c7f4cb`）+ 二轮审查修正（见 §9：用量合并记账/提交状态回传/归因落盘/预检短路/并发钳制）。默认关闭。
> 定位：集群-B 挖深度（串行 k 次），集群-A 拖广度（无状态题并行 N 路抢墙钟）。默认关闭，实验开关。

## 0. 结论一句话

集群-A = 对**无状态题**（crypto/rev 附件，不 start_instance）做同题 **N∈[2,3] 路并行**独立尝试：
只复用集群-B 的「策略温度差异化 + attempt_caps 切片」，不做黑板串行继承（并行没有「前次」），
先到先赢仲裁。**默认关闭**（`cluster_a_concurrency` 未设 = 0 = 走原串行，逐字零回归）。

## 1. 现状与硬前提（HANDOFF §4.5）

| 事实 | 对策 |
|---|---|
| 多模型并行已被 A/B 证伪 | 不做换模型，只用温度/首动作差异化 |
| 硬前提①预算 ×N，否则拆预算必亏 | 预算**预分配**：每路 `题级池 × multiplier ÷ N`；默认 `multiplier=1.0`=总预算不增（≈串行 k 的总量），只换墙钟 |
| 硬前提②环境失败（实例限流/旧实例/504）对 N 路同时生效 | 只对**无状态题**（不碰实例）并行；有状态题维持串行，天然规避实例并发 |
| `Crew` 单线程编排 + 账本非线程安全 | 每路**独立 Crew + 独立 Store 内存视图**，仅通过文件锁协调 submit |

## 2. 架构

```
solve_clusterb(challenge_id)
 ├─ trigger = cluster_a_concurrency∈[2,3] 且 category∈stateless_categories ── 否 → 原串行 Orchestrator（集群-B，不变）
 └─ 是 → ThreadPoolExecutor(max_workers=N) 并行 N 路：
        lane_i = new Store(run_dir).load() → new Crew(config, platform, lane_store, llm_factory)
        summary_i = lane_crew.solve(cid, attempt_caps=cap_i, temperature=SPECS[i].temperature)
        任一路 solved → cancel_event.set() → 未启动的路直接 superseded
     → 合并 facts/dead_ends/candidates（去重）+ 各路终态快照并入主 store + 用量合并记账
     → 归因落盘 → 权威单次 save → 汇总 summary{cluster_a:{...}}
```

- **每路独立 Crew**：规避 `_attempt_temperature`（crew.py:424）等实例可变字段的并行竞态。
- **共享 platform**：`McpHttpClient` 用 stdlib `urllib`（无共享 requests.Session/cookie），每个请求独立连接；仅 `session_id` 初始化可重复，影响可忽略。
- **LLM 客户端**：`llm_factory` 每次 new（agent_main.py:487），天然每路独立。

## 3. 预算预分配（核心决策）

每路切片 `cap_i`：

```
tokens_i = (max_tokens_per_challenge − 已用) × multiplier ÷ N
calls_i  = max(min_calls, (max_llm_calls − 已用) × multiplier ÷ N)
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `cluster_a_multiplier` | 1.0 | 并行总预算放大倍数；1.0=总预算不增（等价串行 k 次），2.0=×2 换更多上下文 |
| `cluster_a_min_calls` | 4 | 每路 calls 下限（保证一路能独立跑一个完整回合） |

- Budget 已支持 attempt 级切片（budget.py `set_attempt_caps`/`attempt_exceeded`），每路设 `cap_i` 即受控。
- 每路 `Budget.load(challenge.tokens)` 因独立 Store 视图互不 etua 可见 → 各算各的，无竞态；总成本 = N 路之和 ≈ `multiplier × 题级池`。
- **钳制注记**：每路还受题级池硬闸（lane Budget 的 max = `max_tokens_per_challenge`），切片 ≤ 题级池恒成立，
  因此 `multiplier > N` 的部分会被题级池静默钳掉（有效放大上限 ≈ N）。要真加预算须同时调大题级池。
- **池语义**：某维度池为 0 = 不限（Budget 语义）→ 切片也置 0 不设限；`min_calls` 下限只在池 > 0 时应用。

## 4. 无状态判定

```python
def _stateless_categories(cfg) -> set[str]:
    raw = (cfg.get("cluster_a_categories") or "crypto,rev")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}
```

- category 来自 `get_challenge` 归一（`_enrich_facets` 已入库，agent_main.py:282）。
- 判定不到 / category 缺失 / 不在白名单 → **保守串行**（宁可少并行，不污染有状态题）。
- 说明：static category 先验有误差（个别 crypto 题也可能起实例）。若某条并行路内部确实调了 start_instance，
  现有 `start_instance` 退避 + `instance_ops_limit` 频控会兜底，且实例配额=1 只影响稀有情况。

## 5. 仲裁与失败

| 项 | 机制 |
|---|---|
| submit 先到先赢 | 复用 `reserve_submit`/`mark_submitted` 文件锁（state.py:301/322，`state.lock` + `_refresh_flags` 读磁盘），跨 Store 实例安全 |
| 取消 | `threading.Event` 广播；solve 内无细粒度取消点 → 仅 **lane 启动前检查**（未启动的路直接 superseded）+ 结果层仲裁；已启动的路跑完各自预分配切片（设计权衡，见 §8） |
| 单路异常 | `LLMError`/`BudgetExceeded(attempt)` 只结束该路，不影响其它路 |
| 黑板合并 | 并行无「前次」→ `prior_text=""`，仅 `temperature` 差异化起步；`card_hint/first_action` 注入仍受 HANDOFF §8 禁忌 8 约束（待单独批准） |

## 6. 开关与零回归

| config.extra 键 | 默认 | 语义 |
|---|---|---|
| `cluster_a_concurrency` | 未设（0） | 2–3 开启并行；0/1/未设=关闭 |
| `cluster_a_multiplier` | 1.0 | 预算放大 |
| `cluster_a_min_calls` | 4 | 每路 calls 下限 |
| `cluster_a_categories` | `crypto,rev` | 无状态白名单 |

- 未设 `cluster_a_concurrency` 或题非无状态 → `solve_clusterb` 走原串行分支，**逐字零回归**。

## 7. 验收

| 层 | 内容 |
|---|---|
| 离线（本轮） | mock `crew.solve`：验 N 路分发、预分配切片=`池×multiplier÷N`、温度差异化、先到先赢取消、facts 合并去重、归因落盘、零回归（未设/非无状态=串行）。**不真机并发**。 |
| 真机 A/B（正式赛） | 2 路 × crypto/rev medium × 4–6 题，HANDOFF §4.5 建议；指标=解出率/墙钟/token per 解出。 |

## 8. 风险

| 风险 | 缓解 |
|---|---|
| 拆预算可能有害（k=3 串行实测有害：切片切断累积上下文） | 默认关闭 + multiplier=1.0 仅换墙钟 + 实验开关；真机 A/B 前不启用 |
| category 误判无状态 | 保守白名单 + 兜底串行 |
| store 中间 save 竞态（N 路各自 os.replace state.json） | submit 走文件锁（`_refresh_flags` 读盘）；预算/token 累计各算各的不依赖实时合并；结束由主线程单一 save 收口 |
| 无细粒度取消 | 软取消（下一检查点退出），不做硬 kill |
| transcript.jsonl 并行 append 单行可能交错（各路 open-write-close 不持锁） | 诊断数据非关键路径，离线套件未见损坏；如需严格再改每路独立文件 |
| 中间态 state.json 脏写（慢路无锁整体 save 会洗掉 winning lane 刚落盘的 flag） | 每路收尾捕获终态快照（内存），收口合并 + 单次权威 save 覆盖所有脏写（§9 F1） |

## 9. 审查修正（2026-11 二轮）

对初版（commit `0c7f4cb`）做严格代码审查，发现 9 处问题（2 严重 / 3 高 / 4 中），全部修复并落正式回归 `tests/smoke_clustera.py`（13 断言，计入 smoke 总数）：

| # | 严重度 | 初版缺陷 | 修复 |
|---|---|---|---|
| F1 | 严重 | winning lane 的提交状态/flag 只写进它自己的 lane Store 内存（solve 内 set_tokens/状态写入均针对本路视图）；且慢路的无锁整体 save 会把磁盘上刚写的 flag 洗掉 → 主 store 与磁盘**双双丢提交状态**，result.json 的 answers 通道失效 | 每路收尾**捕获该路 store 终态快照**（flag/提交状态/flags_tried/计数），收口按「只升不降」并入主 store，最后一次权威 save 覆盖所有中间脏写 |
| F2 | 严重 | 各路各自 `set_tokens`+save，磁盘「最后写者胜」→ 用量只记一路，**低计 (N−1) 路费用**；续跑预算闸误判、费用报表失真 | 用量合并：各路 usage 求和后扣回 (N−1)×初值，回写主 store；summary 的 usage/budget/tokens_spent 同口径 |
| F3 | 高 | `cluster_a_concurrency: 10` → 10 线程，违反平台规则 QA#26「agent 自主并发 ≤3」 | 触发处钳制 `n = min(n, 3)` |
| F4 | 高 | `int("auto")` 直接 ValueError 炸掉编排入口（整题报废） | try/except 兜底串行（对齐 resolve_k 的宽容语义） |
| F5 | 高 | solve_attempts 归因一条没写（与集群-B 及本方案 §7 承诺不符） | 每路一条（index/strategy/temperature/reason/steps/tokens/calls），按 lane 序落盘 |
| F6 | 中 | 无预检：复跑已解题照样烧 N 路 | 对齐集群-B precheck_fn（store 状态 + my_solved_ids）→ `_presolved_summary` 零成本收口 |
| F7 | 中 | `preallocate_caps` 池=0（=不限）时被 min_calls 错切成每路 4 次 | 池>0 才应用下限；池=0 切片=0（对齐 Budget / 集群-B caps_fn 语义） |
| F8 | 中 | 未解出不置 stalled；收口无权威 save，state.json 终态=某路中间脏写 | 合并后置 stalled（未解出时）+ 单次权威 save 收口 |
| F9 | 中 | 回归脚本只在 gitignored 的 `runs/`，新克隆零编排覆盖；且 `del Crew.solve` 是破坏性 monkeypatch（会把被替换的方法整个删掉） | 正式用例迁入 `tests/smoke_clustera.py` 并注册进 smoke.py（111→**124** passed）；桩一律存引用再还原 |

低危记录（不修，留档）：软取消下已启动的各路仍会烧完预分配切片（设计权衡，§5）；多问题题 next_question_id 并行下不共享（各路各答各的，提交以先到先赢+文件锁为准）。

验收：`tests/smoke.py` **124 passed / 0 failed**；retry 自检（含 4 条 preallocate 断言）+ 集群-B 离线 6 脚本 + `runs/_verify_clustera_step1.py` 全绿。仍**未真机并发**（真机 A/B 留正式赛，2 路 × crypto/rev medium）。