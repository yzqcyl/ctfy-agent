# 大规模测试：现状盘点、统计与 Bug 报告

> 状态：**调查完成，未执行真机测试**（并行会话正在改 `src/`，跑真机会读到半截代码）
> 数据快照：`runs/` 产物 mtime 截至 2026-10-29 18:50
> 本轮操作性质：**纯只读**（`read` / `grep` / `Get-ChildItem` / python 读 JSON，脚本走 stdin，未建文件、未跑测试、未抓外网）

---

## 0. 一句话结论

**测试框架、题池、模型矩阵都已经存在，缺的是"规模"和"口径"。** 全历史只有 **154 条**挑战尝试记录、**44 次**解出（28.6%），模型矩阵每模型 **n=5**——这个样本量下 10 个模型的差异全部落在置信区间内，**当前的模型对比结论无效**。更关键的是：**61% 的记录跑在 150k token 预算下，而单题消耗中位数就是 152k**，所以历史上大部分 `stalled` 是**预算设小了**，不是模型/策略不行。

在动真机之前必须先修两件事：**P0 凭据明文泄露**、**M1 预算口径**。不修则新一轮 145 道大规模测试会重复同样的伪影。

---

## 1. 报告边界

| 做了 | 没做 |
|---|---|
| 全量只读统计 139 份 `state.json`、11 份 `bench.jsonl`、158 份 transcript（8,262 行） | 未启动任何挑战实例、未调用任何模型 |
| 核实题池目录 793 行（`runs/site/catalog-challenges.json`） | 未修改 `src/`、`config/`、`runs/` 任何文件（本报告文件除外） |
| 定位并量化 3 类元问题 + 9 个具体 bug | 未修复任何 bug（等批准） |
| 给出综合题组方案与产能/成本测算 | 未评估外部题库接入（上一轮已结论：不需要） |

---

## 2. 数据源与规模

| 资产 | 数量 | 说明 |
|---|---|---|
| `state.json` | **139** | 每份含挑战状态机（`status/attempts/submitted_ok/tokens/flags_tried/tool_counts`）；其中 **131** 份有实际尝试 |
| `bench.jsonl` | **11** | 按题聚合的 bench 记录，共 **25** 条 |
| transcript | **158** 文件 / **8,262** 行 | 逐步会话记录 |
| checkpoint | **11** | 单题续跑快照 |
| 模型矩阵 run | `matrix-v1-*` | **10 个模型** × SAMPLE-001~005 |
| 题池目录 | **793** 行 | `runs/site/catalog-challenges.json`，1,126,351 字节 |
| 历史碰过的题号 | **40** | SAMPLE 14 / CTFARENA 14 / AGENTBENCH 5 / AGENT-DEFENSE 4 / 杂 3 |
| 从未跑过的可启动题 | **≈337** | 可启动实例池 375 减去已试 |

---

## 3. 核心统计

### 3.1 总量

| 指标 | 值 |
|---|---|
| 挑战尝试记录 | **154** |
| 解出 | **44** |
| 解出率 | **28.6%**（Wilson 95% CI ≈ [22.0%, 36.2%]） |

### 3.2 状态分布（154 条）

| status | n | 占比 |
|---|---|---|
| `stalled` | 100 | **65.0%** |
| `submitted` | 44 | 28.6% |
| `instance_up`（只起了实例，没进入解题） | 10 | 6.5% |

### 3.3 资源消耗

| 指标 | n | min | p50 | p90 | max |
|---|---|---|---|---|---|
| 单题耗时（s） | 25 | 25 | **352** | 1,566 | 1,945 |
| 单题 token | 143 | 1,063 | **152,086** | 268,475 | 1,507,053 |
| steps | 24 | 1 | 3 | 8 | 30 |

### 3.4 `bench.jsonl` 侧的失败归因（25 条）

| reason | n |
|---|---|
| `budget_exhausted` | 13 |
| `flag_accepted` | 5 |
| `no_more_actions` | 4 |
| `llm_error` | 1 |
| `coordinator_done` | 1 |
| `None`（记录残缺） | 1 |

### 3.5 模型矩阵（每模型 n=5，SAMPLE-001~005）

| model | attempts | solved | rate | tok/solve |
|---|---|---|---|---|
| mimo-v2.6-pro | 4 | 2 | 50.0% | 223k |
| deepseek-v4-pro-0813 | 5 | 2 | 40.0% | 281k |
| glm-4.7 | 5 | 2 | 40.0% | 309k |
| qwen3.7-max | 5 | 2 | 40.0% | 336k |
| qwen3.8-max | 5 | 2 | 40.0% | 317k |
| deepseek-v4.1-flash-1017 | 23 | 7 | 30.4% | 467k |
| minimax-m3 | 4 | 1 | 25.0% | 537k |
| glm-5.3 | 5 | 1 | 20.0% | 665k |
| kimi-k2.7 | 5 | 1 | 20.0% | 559k |
| kimi-k3 | 5 | 1 | 20.0% | 540k |

**效力判定：不可用于选型。** n=5 时 40% 的 95% CI ≈ **[12%, 77%]**；n=4 时 50% 的 CI ≈ **[15%, 85%]**。表里 20%~50% 的排序完全在噪声内。唯一的弱信号是 **tok/solve** 的 3 倍差距（223k vs 665k），这个指标方差小得多，值得作为主指标。

### 3.6 行为卫生（好消息）

`flags_tried` 分布 = `{0 次: 110, 1 次: 44}` —— 全程几乎没有暴力搓 flag，行为干净，无作弊迹象。

---

## 4. 元问题（比单个 bug 更影响结论）

### M1｜预算是瓶颈，模型不是（P0 级口径问题）

- **94/154（61.0%）** 的尝试记录跑在 **150,000** token 上限下。
- 全域单题 token 的 **p50 = 152,086** —— 中位数的题刚好能把 150k 预算烧穿。
- 推论：150k 档下的 `stalled`（以及 bench 里的 `budget_exhausted`）主要由**预算设置**决定，而不是模型能力或策略优劣。
- **影响**：在该档下做的一切模型对比、k=1 vs k=3 对比都是**预算伪影**。

### M2｜实验口径未标准化

历史上出现过 **12 种** `max_tokens` 档位：

| 档位 | 记录数 |
|---|---|
| 150,000 | 94 |
| 260,000 | 21 |
| 800,000 | 15 |
| 500,000 | 7 |
| 511,000 | 6 |
| 1,500,000 | 4 |
| 80,000 / 300,000 / 1,200,000 / 200,000 / 250,000 / 60,000 | 各 1 |

跨档位混在一起统计 → 数字不可比。后续必须固定档位（建议 300k 或 500k）并写进实验协议。

### M3｜统计源双轨且不一致

`bench.jsonl`（按题聚合，25 条）与 `state.json`（状态机，154 条）口径不同，bench 中还有 1 条 `status=None`。出一份可信报表前必须先定**唯一事实源**（建议 `state.json`，bench 只做派生视图）。

---

## 5. Bug 清单

### P0-1｜凭据明文进入 transcript，并被送进模型上下文

**证据**
- `runs/mat-domain-v1-deepseek-v4.1-flash-1017-SAMPLE-011/transcripts/SAMPLE-011.jsonl`：shell 工具执行 `set`，返回体含 `CTFY_TOKEN=pf_***`、`DEEPSEEK_API_KEY=sk-***`（本报告已打码），该 tool result **随后进入模型上下文**。
- 泄露面量化（`runs/` 下 json/jsonl 全文匹配）：

| 模式 | 次数 | 涉及 run 目录 |
|---|---|---|
| `pf_…` | **213** | 18 |
| `CTFY_TOKEN=` | **96** | 15 |
| `DEEPSEEK_API_KEY=` | **25** | 12 |
| `sk-…` | **21** | 12 |

- 集中在 SAMPLE-003 / 004 / 010 / 011 的 matrix 系列。
- **无全局脱敏**：`src/ctfy_agent/tools_websec.py:305` 的 `_SECRET_RE` 只用于扫**目标站** JS 里的密钥，不处理 agent 自身 env。
- `runs/` 已被 `.gitignore:7` 排除（未进 git），但本地打包外发前必须清洗。

**赛内后果（这才是要点）**
1. 官方 `LLM_API_KEY`（1B token 额度）会被 `env`/`set` 打进 transcript → 平台归档留存；
2. 恶意题面可用 prompt injection 诱导 agent 执行 `env` 并把结果 POST 给题目实例 → **凭据被题目作者拿走**；
3. 平台 `CTFY_TOKEN` 同样会经模型上下文发往第三方模型服务。

**修法**：在 tool result 进入上下文/落盘之前加统一脱敏过滤器（匹配 `sk-`/`pf_`/`Bearer`/`*_KEY=`/`*_TOKEN=` 等形态 + 已知 env 值精确匹配），并对 `env`/`set`/`printenv` 类命令的输出强制走该过滤器。

### P1-1｜上游限流无退避，`instance_up` 直接废掉整题

- 证据：`[工具报错] Rate limited by upstream API, please retry later`（257 次匹配）；随后平台硬拦：`start_instance 已达本题上限 5 次，停止反复启停实例`。
- 结果：**10/154（6.5%）**记录停在 `instance_up`，题都还没开始做。
- `HANDOFF.md:121` 记的「CTFARENA 全库 50 道 web 全起不来」同源。
- 修法：`start_instance` 失败分级（限流/配额/不可用），限流走指数退避 + 上限内不重复启动；连续失败则整题标记 `infra_unavailable` 并从统计分母剔除（不计入模型能力）。

### P1-2｜step_limit 的 rescue 结论被当事实继承

- 证据：`[web 结论（10 步，rescued）] [无逐字证据·未验证]`、`[rev 结论（10 步，no_more_actions）] [rev 跑了 10 步后未给出结论]`。
- 风险：这类"未验证结论"会写入黑板并被下一轮当既成事实继承 → 与集群-B 的黑板继承机制直接冲突（污染事实源）。
- 修法：黑板事实项加 `verified` 字段，rescue/no_more_actions 产出只进"假设"区，不进"事实"区。

### P2-1｜stdlib-only 未写入提示词

- 证据：`ModuleNotFoundError: No module named 'jwt'` / `'cryptography'`（bench-pilot2 连续试 import，烧步数）。
- 修法：提示词声明"仅 Python 标准库，禁第三方"，并在 import 失败后禁止同类重试。

### P2-2｜没有结构化错误码，失败统计不可靠

- 本轮按正则统计得到 `paused` 235 次、`5xx` 578 次，但抽样后**绝大部分是题面正文**（SAMPLE-007 本身就是"pause 一个 worker"）和端口号。
- 结论：当前无法可靠统计失败原因。必须先定义错误分类枚举（如 `flag_rejected / budget_exhausted / step_limit / instance_unavailable / rate_limited / llm_error / tool_error / no_more_actions / coordinator_done`），归因只认结构化字段，不认正则。
- 这条同时是集群-B `classify_failure` 与待办②归因口径的前置条件。

### P2-3｜`instance 未清理` 风险（观察项，未证实）

多个 `instance_up` 记录后紧接新题尝试，与 `max_instances_per_team_effective=1` 叠加时容易自锁。建议每轮开始前强制 `list_instances` + 清理。

---

## 6. 题池现状（决定"能测多少"）

来源：`runs/site/catalog-challenges.json`（793 行，2026-10-28 23:31 抓取）。

| category | 数量 | is_qa_only | 有启动记录 | 备注 |
|---|---|---|---|---|
| web | 375 | 0 | **363** | 真正解题池 |
| misc | 412 | **411** | 6 | 410 道是 `single_select` 知识选择题（SECQA 210 / CTI-MCQ 100 / MMLU-CS 100），**不是智能体解题对象** |
| reverse | 3 | 0 | 3 | 赛道不收出题 |
| pwn | **2** | 0 | 2 | 同一题两个变体（SAMPLE-003 / 012） |
| crypto | 1 | 0 | 1 | 赛道不收出题 |

- 在线交互类池 = **377**（web 375 + pwn 2）；有实例轨迹 **365**。
- web 难度：easy 103 / medium 187 / hard 85。
- web 题目形态：`dynamic` 372 / `static` 24 / `checker` 4 / `seeded` 1。
- AI 安全仅 **17** 道（tag `ai`/`agentic_ai`/`llm`，全部挂在 web category）。
- **本地无法起服务**：全仓 0 个 `metadata.yaml` / `docker-compose.yml`；目录里 9 道 attachment 也只是 `setup.txt`/`README.txt`。官方题包需另从 `CTF-Arena/challenges@0aa7b43` 取。

**两条硬伤**
1. **pwn 只有 2 道** → pwn 方向既无统计意义也无法出题对标；
2. **AI 安全 17 道** → 只够定性。

---

## 7. 综合题组方案（6 组 / 145 道）

| 组 | 来源 | 数量 | 选法 | 目的 |
|---|---|---|---|---|
| G0 基线 | SAMPLE-001~014 | 14 | 全量 | 与历史数据对表；含 4 道 `checker` 状态型题 |
| G1 主体 | CTFARENA | 30 | easy 15 + medium 15 | web 主力，验证率与稳定性 |
| G2 高难 | XBOW | 25 | easy 10 + medium 15 | 真实漏洞利用，压上限 |
| G3 注入 | SQLILABS | 20 | 难度分层 | 注入链路工具正确性 |
| G4 上传 | UPLOADLABS | 15 | 全量 | 文件上传/绕过 |
| G5 AI 安全 | AGENT-DEFENSE 2 + AGENTBENCH 2 + ai-tag web 17 | 21 | 全量 | 赛道 AI 方向（池子上限） |
| G6 简单靶 | CYBENCH static | 20 | 随机 | 基础 sanity，抓框架级崩溃 |

### 产能与成本测算

| 项 | 估算 |
|---|---|
| 单题耗时（p50 / p90） | 352s / 1,566s |
| 10h 一段上限（串行，配额 1） | **≈102 次**（按 p50）；实际含难题约 60~80 次 |
| 145 道所需 | **1.5~2 段**（≈14.2h @ p50） |
| token（145 道 @ p50 152k） | **≈22M**；若预算提到 500k，上限 ≈72M |
| 统计效力（单配置 100 次） | 解出率 CI ≈ **±9pp** |

**定位建议**：把"大规模"定位为**广度扫 bug / 崩溃 / 基础设施故障**，而不是精度定论。k=1 vs k=3 的收益判断只能检出 ≳20pp 的大效应；若要看 10pp 级差异，需 ~300 配对尝试，赛内产能做不到 → 改用 **tok/solve（效率）** 作为主指标（44 个解出样本已能给粗值，且方差小）。

---

## 8. 需要在动手前拍板的事

| # | 事项 | 选项 |
|---|---|---|
| D1 | 先修还是先跑 | (a) 先修 P0-1 脱敏 + M1 预算→300k，再开跑（推荐）；(b) 先跑广度扫拿数据，接受伪影 |
| D2 | 并行会话何时落定 | 等 `state.py`/`crew.py`/`platform.py` commit 且 `git status` 干净后再跑真机 |
| D3 | 预算与模型 | 建议 `max_tokens=300k` 或 500k 固定档；模型沿用 `deepseek-v4.1-flash-1017` + `LLM_ENABLE_THINKING=0`（关闭思考模式，避免 reasoning token 吃满上限——这是历史上 `finish_reason=length` 的来源之一） |

**建议顺序：D1(a) → D2 等落定 → 跑 G0+G1（44 道，约 4~5h，~7M token）出第一版真实 bug 报表 → 再扩到 145 道。**

---

## 9. 增量（2026-10-29 晚）：烟测实证 + bank-500k-v1 启动

用户拍板：预算 500k 先行（D1a 落地为 500k），随后重跑。烟测两轮，暴露 3 个新问题、确认 2 件事，批次已启动。

### 9.1 烟测 1：模型名大小写 → 404 → 假阳性解出（新 P0/P1 bug 实锤）

- 传 `--models DeepSeek-V4.1-Flash-1017`（历史写法）→ 百炼返回 `model_not_found`（HTTP 404），**LLM 从未被调用**：0 token / 0 calls / 1 step / 39s。
- 但 `matrix.jsonl` 记了 `submitted=true, reason=exploit_verified` —— 机制：`src/ctfy_agent/crew.py:1006-1013` 收尾用 `platform.my_solved_ids()` 对账，SAMPLE-014 属队伍历史已解出 → 把内存里的 summary 改成 submitted，而 `store.save()` 在第 993 行已执行过，**对账结果不回写 state.json**（state 仍是 `stalled`）。
- 双害：① benchmark 虚报解出（tokens=0 的"解出"）；② `bench.jsonl` 与 `state.json` 双轨不一致（M3）的活体证据。
- **顺带发现（待修 P1）**：`config/agent.json` 的 `roles.*.model = 'DeepSeek-V4.1-Flash-1017'`（大写）在当前百炼端点下会 404——直接 `python -m ctfy_agent` 跑会全军覆没；有效 id 是小写 `deepseek-v4.1-flash-1017`（`runs/_bailian_models.json` 收录，`.env` 注释也写明小写才有效）。

### 9.2 平台记忆效应：已解出题不能重跑（统计口径硬约束）

- 实测 `my_solved_ids()`：**本队已在平台解出 20 道**（跨 suite 共享）：SAMPLE 9/14（001/002/005/006/007/008/009/010/014）+ CTFARENA 7（000/006/038/039/040/041/042/044）+ AGENT-DEFENSE 3。
- 后果：重跑已解出题会触发预检短路（`crew.py:1211-1217`）→ 0-token 假解出，或收尾对账虚报。**SAMPLE 全量重跑 9/14 是废数据**。
- 规则：任何 bench 前必须先拉 `my_solved_ids()` 过滤，统计只认未解出题。未解出池：SAMPLE 5 / CTFARENA 107 / CYBENCH 40 / XBOW·SQLILABS·UPLOADLABS 全量。

### 9.3 确认的两件事

- **`LLM_ENABLE_THINKING=0` 已生效**（`.env` 第 14 行，精确匹配 `^LLM_ENABLE_THINKING=0$`）——待办①实际已完成。
- **500k 档真实生效**：烟测 2（正确模型 id + CTFARENA-001，未解出题）**329s / 507,953 token / 6 步 / 解出（exploit_verified）**，state.json `tokens.max_tokens=500000`。单题 50 万 token 在 ~5.5 分钟内烧完，500k 档的单题墙钟 ~330-800s，`--timeout 1800` 足够让预算（而非超时）成为约束。

### 9.4 bank-500k-v1 批次（已启动，后台 job pwsh-844）

| 臂 | competition | 题目 | 数量 | 熔断 |
|---|---|---|---|---|
| sample | `e4e78ff3…`（SAMPLE 库） | SAMPLE-003/004/011/012/013（全部未解出） | 5 | 2.5M tok / 5400s |
| ctf | `4394a10a…`（CTFARENA 练习场） | CTFARENA-002~005,007~017（未解出） | 15 | 7.5M tok / 9000s |
| cyb | `d35cc057…`（CYBENCH） | CYBENCH-001~015 | 15 | 7.5M tok / 9000s |

- 统一口径：`--cap 500000`、`--timeout 1800`、单模型全角色 `deepseek-v4.1-flash-1017`、思考模式关闭。
- 产物：`runs/bank-500k-v1/{sample,ctfarena,cybench}.jsonl` + `run.log`/`err.log`；**可续跑**（runner 按 out 文件里 (model, id) 去重，中断后重跑同一命令即接续）。
- 预计 3~6h；完成后统计口径：解出率 / tok-per-solve / 失败归因（对照 §5 P2-2 的枚举）。

### 9.5 可比性警示

历史 150k/260k 数据产自 Paratera 端点 + 大写模型名时代；本次 500k 跑在百炼端点。**"500k vs 150k"的 A/B 与端点/模型 id 变化混杂**——严格的预算结论只能来自同一端点下的重跑（本批次即是新基线，往后一切对比以它为准）。

## 10. bank-500k-v1 完测结果与错题归因（2026-10-29 深夜）

### 10.1 总表（35 题全部跑完，job exit 0）

| 臂 | 题数 | 解出 | 解出率 | 总 token | tok/解 | 解出中位耗时 |
|---|---|---|---|---|---|---|
| ctfarena | 15 | 7 | **46.7%** | 6,218,627 | 308,435 | 152s |
| cybench | 15 | 3 | **20.0%** | 6,683,187 | 255,790 | 283s |
| sample | 5 | 0 | 0% | 2,429,975 | — | — |
| **合计** | **35** | **10** | **28.6%** | **15,331,789** | **~292k** | 178s |

口径：`--cap 500000`、`--timeout 1800`、单模型全角色、思考关闭、全部为平台未解出题；0-token 假解出 = 0。

### 10.2 决定性统计：预算是「调用次数」约束，不是「单次上下文」约束

| 指标 | 解出题（n=10） | 未解题（n=25） |
|---|---|---|
| 模型调用次数 中位 | **23.5**（12~41） | **39**（32~42） |
| prompt/call 均值 | 11,312 | 12,587 |
| 耗时中位 | 178s | 276s |

- 500k ÷ ~12.6k/call ≈ **40 次调用**——批量上限实际等价于「~40 次模型调用」；
- 解出题中位 23.5 次 ≈ 265k token：**500k 预算只够约 1.9 次「完整尝试」**；
- 有 2 道解出题（CTFARENA-013/015）是压着 **40~41 次调用、482~491k token** 解出的——临界解出，说明部分题确实需要接近满预算；
- 未解题**全部**在 32 次调用之后才被砍，没有一道是「早停但答错」。

### 10.3 失败归因：22/25 是 budget_exhausted，且不是答错

未解出的 25 道：`budget_exhausted` **22**、`coordinator_done` 2、`no_candidate` 1；状态全为 `stalled`。
**零 `flag_rejected`/答错**——问题是「走不到候选答案」，不是「验证环节被拒」。

### 10.4 轨迹证据（transcript 实测，非推测）

| | 未解出 CTFARENA-002 | 解出 CTFARENA-007 |
|---|---|---|
| 事件数 / 动作数 | 115 / **67** | 34 / 16 |
| `http_request` 单独调用 | **49 次** | 7 次 |
| 同一 JS 文件重复取 | **13 次** | — |
| `/api/auth/login` 重复 | **12 次** | — |
| `/api/auth/me` 重复 | **12 次** | — |
| 模型调用 | 35 | 12 |

未解题的典型形态：**对同一端点反复取（13/12/12 次）、逐条 49 次单点请求、无重复检测、无进度刹车**——
观察被反复重新注入上下文，既烧 token 又稀释判断。

### 10.5 结论（三条）

1. **瓶颈在控制流与调用效率，不在单次上下文长度**：prompt/call 仅差 11%（11.3k vs 12.6k），而调用次数差 66%（23.5 vs 39）；
2. **缺「无进展刹车」**：同 URL ×12 次、同文件 ×13 次仍继续——没有「同一观察重复出现即换策略/移交」的机制；
3. **缺调用预算感知**：agent 不知道剩余预算≈剩余调用次数，不会在 50% 预算时收敛、75% 时强制收口。

### 10.6 上下文优化方法与论文（联网检索 2026-10-29，arXiv API）

| # | 论文 / 方法 | 关键结论 | 我们可落地点 |
|---|---|---|---|
| 1 | **TRACE**：Reliable Context Compression for Long-Horizon Agents（2608.06503） | 压缩会削弱「最近交互」权重 → 动作被阻塞、**重复探索**、跨运行不稳定 | 正对上 §10.4 的重复取数；压缩必须保留最近 K 轮原文 |
| 2 | **PAIR**：counterfactual continuations（2609.36526） | 压缩在「可解性」下降前先损害「可靠性」；**孤立压缩事件**是退化主因；改压缩模板可缓解 | 压缩要平滑/非孤立触发（与 M7 的滑窗时机耦合） |
| 3 | **FOCUS**：decision-preserving compression（2609.37590） | training-free，峰值上下文 -48%、依赖 -73%、成功率 +8.9pp | 我们的 FactsBoard 已有雏形，可按「决策依赖」而非「信息量」压缩 |
| 4 | **压缩成本归因**（2609.22114） | **工具 schema 过滤是唯一线性无歧义收益（21-57k tok/turn）**；内容压缩前期省 2%、后期二次增长；网关需可召回 | 按角色裁剪工具面（已有 `_role_specs`）——量化其 token 占比 |
| 5 | **What Does Context Compression Cost an Agent?**（2608.16370） | 压缩不降完成率但**大幅增加再获取（重复检索）次数**，隐性成本爆炸 | 与 §10.4 完全同构：我们的隐性成本就是重复取数 |
| 6 | **TokenPilot**（2606.17016） | 双粒度：ingestion-aware compaction（**稳定前缀**）+ lifecycle-aware eviction；成本 -56%~-87% | 压缩放在「角色步结束」等稳定边界，避免前缀抖动 |
| 7 | **Context-Folding**（2510.11967） | 子轨迹折叠成摘要，活跃上下文小 10× | 对齐 cluster-B 子 agent + 黑板继承 |
| 8 | **AdaCoM**（2605.30785） | Fidelity-Reliability 权衡：**能力越弱的模型越需要激进压缩** | 我们用 flash 模型 → 压缩应更激进（但保 ID/路径原文） |
| 9 | **Continuous Context Management**（2609.35540） | 每轮压缩降低累计输入，但**多数模型成功率下降** | 反例警示：别做成每轮压缩，按链段压缩 |
| 10 | **Sleeping Agent**（2608.11775） | gist 压缩丢「日期/时间」，一句话改模板可恢复 +0.31 准确率 | 事实块必须保 **ID/路径/token/时间戳** 原文，只压叙述 |
| 11 | MarkovSim（2610.35349） | 有界上下文：FSM 状态转移作摘要 | 替补方案（我们现在状态机在 state.py） |
| 12 | ContextEvo（2609.34649）/ EvoDS（2606.03841）/ SUPO（2510.06727） | 上下文策略需自适应/学习，固定策略在长程压力下失效 | 中期方向：按题型（web/cybench/ai）分策略 |

### 10.7 建议优先级（按实测收益排序，均需批准；涉及并行会话文件的要排队）

| 优先级 | 动作 | 落点 | 依据 |
|---|---|---|---|
| **P0 ✅ 已落地** | **同观察去重 + 无进展刹车**：digest 相同的观测替换为省略标记（审计 transcript 仍存全文）；同一 (tool,target) 第 4/8/12…次注入换法提示，不被其它目标的新输出稀释 | `progress.py`（新）+ `solver.py` 两条路径接线 | §10.4 实测 13/12/12 次重复；论文 1/5。验收：`tests/smoke_progress.py` |
| **P0 ✅ 已落地** | **调用预算感知**：每步在事实头部、每轮给协调者注入「已用 X/Y tok（N%，约剩 M 次调用）— 探索/收敛/收口期」阶段行（advisory，不硬拦，不影响 96-98% 用量的晚解出） | `crew.py` + `solver.py`（未改提示词文件） | §10.2 预算=calls。验收：`tests/smoke_progress.py` |
| **P1 ✅ 已落地** | **工具 schema/响应裁剪**：响应截断（HTTP_WINDOW=5000 窗口 + 消息层 1600 收口 + grep/offset 翻页）经核查已由并行会话 v3 改动覆盖；本次补齐 schema 门控——`traffic` 工具（~413 tok/次）从「无条件全角色」改为「web 默认 + 其它按 local 显式声明」，依据=实测 1495 次调用中 traffic 仅 9 次且 9/9 出自 web | `crew.py`（TRAFFIC_DEFAULT_ROLES 门控） | 论文 4；本会话转录复测 |
| **P1 ✅ 已落地** | **事实块保真**：压缩时 JWT/≥16位hex id/ISO 时间戳/api_key 键值逐字保留（各族限量防挤占，只压叙述）；不同 flag 候选各占一条（按值去重）；render 按「flag/会话/组件 > 工具/表单 > 端点/链接」优先级排序，520 字符窗口先截软事实；最近 K 轮不压（solver 的 KEEP_TAIL_GROUPS=3，已有） | `ctxcompress.py` | 论文 1/10（Sleeping Agent 2608.11775） |
| **P2 ✅ 已落地（待实跑验证）** | **题型分策略**：web=批处理（单步工具 +4、web 专家步数 +4）、ai=对话式（协调轮次 +4、ai 专家 +2）、cybench=深单路（强制 k=1、关闭集群-A、rev/crypto/pwn/misc 步数 +6）。实现为**增量语义（只加不减）**——config/agent.json 已有基线调优（coordinator 14 轮、web 14 步、单步 8 工具），用绝对值会把它覆盖成降级；白名单 `PROFILE_KEYS` 只允许编排键，**未改任何提示词** | `crew.py`：`DOMAIN_PROFILES` / `domain_profile()` / `profile_rounds|steps|calls_cap()` + `solve()`/`_delegate()`/`solve_clusterb()` 三处接线；`domain_profiles=false` 应急开关 | 论文 12。验收：`tests/smoke_p2.py`（档位选择/增量只加不减/白名单/零回归/deep 强制 k=1） |
| **P2 ✅ 已落地（实测零差异，定性为保险）** | **压缩触发改链段边界**：软压缩只动「已闭合链段」（链段=自上次 PLAN 变更以来的连续步，模型自己声明的换方向界标），当前段与最近 3 步保持原文；硬窗（超 max_tokens）仍整块丢最旧（API 约束优先），tool_call 配对不拆散 | `solver.py`：`_compact_history(seg_start=)` + `_solve_once`/`_delegate` 两处接线（`seg_first_step` 随 PLAN 变更推进）；coordinator 每轮压缩本就是轮边界，不动 | 论文 2/6/9。验收：`tests/smoke_p21.py`（旧实现逐字节一致/段保护/单段不动作/硬窗优先/14 步行为链）；**真机转录重放（1411 步）：新旧零差异**——段长≤4 占 99.7%，KEEP_TAIL=3 已覆盖，见落地记录 |

P1 落地记录（2026-10-30，经用户授权在并行会话未提交改动之上增量修改，diff 备份 `runs/_backup_p1_20261030_1234.patch`）：
`crew.py` traffic 门控；`ctxcompress.py` 硬值正则 + 多 flag 候选 + render 优先级。验收 `tests/smoke_p1.py`
（JWT/sha256/ISO/api_key 逐字保留、双 flag 各一条、后到 flag 不被端点挤出、traffic 门控四态）。
全套 `tests/smoke.py` **132 passed / 0 failed**。schema 体积实测：全量 18 工具 ≈ 4.6k tok/次调用
（40 次 ≈ 185k/题 = 37% 预算），角色分面后 web ≈ 2.8k、ai ≈ 4.1k、rev/crypto ≈ 1.7k（不含 platform 侧）。

落地记录（2026-10-30）：新增 `src/ctfy_agent/progress.py`（ObservationTracker + BudgetPhase）；
`solver.py` 的 `run_role`/`_solve_once` 与 `crew.py` 协调者轮循环接线；`tests/smoke_progress.py` 验收
（去重/时变字段/第 4·8 次提示节律/三档预算阶段 + 接线守卫）。全套 `tests/smoke.py` **131 passed / 0 failed**。
顺带修复：`tests/smoke_clustera.py` lane 起步时序余量 0.03→0.5s（二分实证其竞态与本次改动无关——
单跑也会挂 300/20，负载下第 3 路迟到被「他路解出」正确取消所致）。

## 附录 A｜复算方法

所有数字可复跑：读 `runs/**/state.json` 的 `challenges[*]`（过滤 `status != "new"`），读 `runs/**/bench.jsonl`，聚合 `tokens.total_tokens` / `seconds` / `steps` / `status` / `reason`。统计脚本未落盘（避免与并行会话冲突），需要时可固化为 `tools/` 下的正式脚本。

## 附录 B｜本报告的打码约定

文中 `pf_***`、`sk-***` 为打码后的凭据形态。原始明文仅存在于 `runs/` 下的历史 transcript（已在 `.gitignore:7` 排除），**外发/打包前必须清洗，并考虑轮换 `CTFY_TOKEN` 与模型 key**。

## 11｜P0+P1 验证（bank-500k，2026-10-30）——含两批无效样本的方法学记录

**设计**：v0 未解出的 8 道 CTFARENA（002/003/005/008/010/012/014/017）同口径重跑（同模型
deepseek-v4.1-flash-1017、--cap 500000、单模型全角色），另跑 v0 已解出的 3 道（007/013/015）
做「早收口是否误伤」回归。改前=v0；改后=当前工作区 v3 未提交改动（缓存去重/翻页/账本）+
本会话 P0/P1。**归因是合并的**：不能说成 P0/P1 单独贡献。

### 11.1 有效样本筛选（先过实例可达性，才认数据）

平台「团队实例配额=1」且有上游 start 速率限制，前两轮批次被大面积污染：

| 批次 | 实例可达 | 处置 |
|---|---|---|
| r1 主批 bank500k-p0p1 | 1/8（仅 002；其余 rate=6-8、实例 HTTP=0） | **作废** |
| r1 回归 bank500k-p0p1r | 0/3 | **作废** |
| r2 主批 bank500k-p0p1b | 3/8（002/003/005 有效；008 起 rate=7-9） | 前 3 题有效 |
| r2 回归 bank500k-p0p1rb | 0/3 | **作废** |
| paced 主批+回归 bank500k-paced | **8/8**（rate=0、实例 HTTP 6-27、每题 1 主机） | **有效** |

机制（实测）：被拒的 start_instance 可能仍在服务端异步建出实例 → 泄漏占配额 → 后续更堵（自我强化）；
stop_instance 报 stopped 后 teardown 期间实例可能短暂复现。故 paced 批改为**驱动代拿实例**：
驱动慢速退避 start → 预注册进 store（challenge.instance/instance_id）→ solver 直接复用
（007 的 start=0 即证据，agent 完全没自己起实例）→ 跑完多重轮询清理。跑批前置 _preflight_instances.py
强制 0 实例；两个旧驱动也补了 _release_instances() 兜底。

### 11.2 最终对照（仅有效样本）

**A. v0 未解出的 8 题**

| 题 | v0 | 现在（v3+P0/P1） | 行为 |
|---|---|---|---|
| CTFARENA-002 | ✗ 508k budget_exhausted | ✗ 449k coordinator_done | 换法提示 1、预算线 5 |
| CTFARENA-003 | ✗ 501k budget_exhausted | **✓ 303k exploit_verified** | 预算线 5 |
| CTFARENA-005 | ✗ 503k budget_exhausted | ✗ 502k budget_exhausted | 换法提示 3、预算线 5 |
| CTFARENA-008 | ✗ 507k budget_exhausted | ✗ 438k coordinator_done | 预算线 4 |
| CTFARENA-010 | ✗ 504k budget_exhausted | ✗ 500k budget_exhausted | 换法提示 1、预算线 4 |
| CTFARENA-012 | ✗ 510k budget_exhausted | ✗ 505k coordinator_done | 换法提示 1、预算线 5 |
| CTFARENA-014 | ✗ 513k budget_exhausted | ✗ 504k budget_exhausted | 换法提示 2、预算线 4 |
| CTFARENA-017 | ✗ 510k budget_exhausted | **✓ 413k flag_accepted** | 换法提示 2、预算线 3 |
| 合计 | 0/8、4.06M tok | **2/8、3.62M tok（−11%）** | |

**B. v0 已解出的 3 题（回归：早收口是否误伤）**

| 题 | v0 | 现在 | 结论 |
|---|---|---|---|
| CTFARENA-007 | ✓ 130k flag_accepted | ✓ **78k** flag_accepted | 保持，成本 −40% |
| CTFARENA-013 | ✓ 501k exploit_verified | ✓ **349k** exploit_verified | 保持，成本 −30% |
| CTFARENA-015 | ✓ 496k exploit_verified | ✓ 503k exploit_verified | 保持 |
| 合计 | 3/3、1.13M tok | **3/3、0.93M tok（−17%）** | 无误伤 |

### 11.3 结论与边界

1. **解出率**：11 道有效样本 3/11 → 5/11；其中「历史未解」子集 0/8 → 2/8。新增两解（003 走
   exploit_verified、017 走 flag_accepted）均为平台判题通过，非猜测。
2. **成本**：两个子集 token 分别 −11% / −17%（合并 5.19M → 4.54M，−13%）。
3. **「早收口误伤」未发生**：3 道曾解出的题全部保持，且两题更便宜；P0-2 预算线是建议性文本，
   实际仍允许走到 96-100% 预算（015 用到 503k）。
4. **P0 机制确认生效**：预算线每题注入 2-6 次（协调者每轮 1 次）；同目标换法提示 0-3 次/题。
5. **归因边界**：当前工作区同时含并行会话 v3（缓存/翻页/账本校验）与本会话 P0/P1，无法拆分贡献；
   可说的是「v3+P0/P1 组合相对 v0 有正向收益」，不能声称 P0/P1 单独带来 +2 解。
6. **运营教训**（写入跑批规范）：跑前必查 0 实例；不要高频重试 start；优先「驱动代拿 + 预注册复用」；
   任一题没拿到实例即判该题无效，不得计入对照。

P2-2 落地记录（2026-10-30，用户批准「按域策略分流」）：

- crew.py 新增 DOMAIN_PROFILES / domain_profile() / profile_rounds() / profile_steps() /
  profile_calls_cap() 与 PROFILE_KEYS 白名单；solve()（轮次、单步工具数）、_delegate()（角色步数）、
  solve_clusterb()（deep 档 force_k1 + no_cluster_a）三处接线。**未触碰任何提示词文本与工具面。**
- 增量语义（只加不减）的理由：实测 config/agent.json 已把 coordinator 调到 14 轮、web 14 步、
  单步 8 工具（并行会话的既有调优），若档位写绝对值会把它们覆盖成降级。
- 验收 	ests/smoke_p2.py 6 条：档位选择（web/ai/deep）、未识别→空档（零回归）、
  增量只加不减、应急开关 domain_profiles=false、热调参白名单丢提示词键、
  _delegate 行为（web 生效步数 = 基线 + 4）、deep 档 solve_clusterb 强制 k=1 且禁集群-A、
  非 deep 题仍走集群-A。
- 全套 	ests/smoke.py **138 passed / 0 failed / exit 0**。
- 顺带修掉 	ests/smoke_clustera.py 的既有 flake（非产品缺陷）：lane 入口的 cancel 检查 +
  线程调度导致迟到的 lane 被判 superseded，用量少并一路（偶发 300/20 vs 450/30）；改为确定性栅栏
  （各处桩按真实并发数放行；串行 2 次 attempt 场景按 1 路），连跑 5 次稳定。
- **尚未实跑验证**：P2-2 会改变 web/ai/cybench 三类题的编排，需要真机批（paced 跑法）确认
  「解出率不降、成本下降」，未验证前不对外声称收益。

P2-1 落地记录（2026-10-30，用户批准开工）：

- 实现：solver.py _compact_history 增 seg_start 参数——软压缩只压下标 < seg_start 的
  **已闭合链段**；当前段（自上次 PLAN 变更以来）与最近 3 步（KEEP_TAIL_GROUPS）保持原文；
  硬窗不变（超 max_context_tokens 仍整块丢最旧到塞下，最新一步必留，配对不拆散）。
  _solve_once 与 _delegate（run_role）两处循环接线：seg_first_step 在 plan.observe
  报告 changed 时推进为新段首步。链段界标复用提示词既有约定「计划没变就原样复制」，
  **零提示词改动**。coordinator 每轮压缩本就发生在轮边界，未改。
- 验收 	ests/smoke_p21.py 5 条：seg_start=None 与旧实现**逐字节一致**（零回归兜底）、
  软压缩只动闭合段、单段历史不动作、硬窗优先（段保护不挡 API 窗）、行为链 14 步
  （步 7 换 PLAN：段 A 压缩/段 B 原文/任何请求当前段无标记）。全套 smoke **144/0/exit 0**。
- **真机转录重放（runs/_p21_replay.py，零 API 成本）的诚实结论：零差异。**
  语料 = v0（ctf 15 + cyb 15）+ v3+P0/P1（paced 8 + p0p1b 3），共 123 个专家 run / 1411 步：
  - 链段很短：段长分布 {1:1076, 2:106, 3:27, 4:6, 6:1, 12:1}，≥5 步的段仅 2 个——
    模型几乎每步都改写 PLAN（计划漂移），「稳定长链」在这批真机上不成立；
  - 因此旧逻辑（只保尾 3 步）在本语料上**从未截到段中间**（段内被压组=0），
    新旧软压缩事件（34/21 次）、压缩组数（193/143）、硬丢（0/0）、请求体积（Δ=0 est tok）完全一致；
  - 结论：P2-1 是**零实测收益、零实测成本**的保险——当未来编排产生更稳的长链
    （如 P2-2 web 批处理档把单步工具数放宽后），段保护自动生效；本批真机数据上不构成改进，
    不对外声称任何收益。
