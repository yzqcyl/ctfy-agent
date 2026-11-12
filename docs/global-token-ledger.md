# 全局 token 总账（1B 总量，跨题 / 跨进程）

> 规则依据：CCF-TPCTF 赛事规则手册 v2.5 §二(五)（P3）——正式赛**仅智谱** `open.bigmodel.cn/*`，
> **Token 总量 1B**，且「不得通过代理、重定向、子进程或其他方式绕过网络和资源限制」。
> 这篇只讲总账本身；智谱端点/注入核查见 `docs/audit-rules-v2.5-2026-11-10.md` §3。

## 为什么需要

题级 `max_tokens_per_challenge`（800k）只管**一道题**。正式赛的硬约束是整队 **1B 总量**：
多题连做、同题重跑、容器最多 3 次重启叠加起来算。没有跨题账本时，单题墙只会在「每题 800k」
处响，总量却没有闸——真到 1B 时只会表现为平台侧拒绝/清零，事后无从定位。

## 实现（`src/ctfy_agent/gledger.py`）

| 点 | 行为 |
|---|---|
| 记账时机 | `DeepSeekLLM.chat()` 收到响应后 `record(usage)`；异常/重试不重复记（只有 200 响应才记一次） |
| 记账口径 | **原始 token** = `prompt_tokens + completion_tokens`，**不减缓存折扣**（平台计费口径；`budget.py` 的缓存折价只用于题级规划） |
| 闸门时机 | `chat()` **发请求之前** `check()`：`used >= cap` 抛 `BudgetExceeded(kind="budget_exhausted")`，不产生费用 |
| 累加范围 | 跨题、跨进程（每次从磁盘重读再原子写回），文件里还按 `run_id` 分桶 |
| 落盘 | `tmp + os.replace` 原子写；坏文件读成空账不会炸 run |
| 上限 | `config.max_total_tokens`，默认 **1_000_000_000**；`0` = 关闭（本地调试） |

落盘路径优先级（`gledger.ledger_path()`）：

1. `config.extra.global_ledger_path` / 环境变量 `CTFY_GLOBAL_LEDGER`；
2. `$CTFY_WORK_DIR/.ctfy-global-ledger.json`（平台容器：work 目录随 run 归档）；
3. `<runs_dir 上一级>/.ctfy-global-ledger.json`（本地：项目根下，跨 run 共享）。

> 本机调试时该文件在项目根（已在 `.gitignore` 里），含用量不发仓库。

## 配置 / 环境变量

```jsonc
// config/agent.json（已显式写出正式赛口径）
"max_total_tokens": 1000000000,
"global_ledger_path": ""
```

| 环境变量 | 作用 |
|---|---|
| `AGENT_MAX_TOTAL_TOKENS` / `CTFY_MAX_TOTAL_TOKENS` | 覆盖总量上限（`0` 关闭）；`AGENT_*` 与 `CTFY_*` 都认 |
| `AGENT_GLOBAL_LEDGER` / `CTFY_GLOBAL_LEDGER` | 显式指定账本路径 |
| `CTFY_RUN_ID` | 记账时按 run 分桶（可选） |

## 运行时怎么核对

- 每题开跑时 transcript 会落一行：`[全局总账] 全局 token X / 1,000,000,000（Y%，跨题累计）（账本 …）`。
- 到顶时该题以 `budget_exhausted` 收口（`retry.STOP_REASONS` 已有该值 → 不再尝试下一题），
  异常文案里带已用/上限与账本路径，不会出现「静默白烧」。
- 人工查看：`python -c "import json;print(json.load(open('.ctfy-global-ledger.json')))"`。
- 需要重置（仅本地练习）：删掉账本文件即可；正式赛**不要**删（删了就是绕过总量限制）。

## 回归

`python tests/smoke_gledger.py` → **19/19**：落盘累计、原始口径（缓存折扣不生效）、到顶拦截、
`cap=0` 关闭、三级路径解析、环境变量覆盖、`build_global_ledger` 关停、LLM `chat()` 记录/拦截、
多实例共享累加。

## 已知边界

- 进程间没有文件锁（`fcntl` Windows 不可用）：单 agent 进程 + 原子写足够；GUI 并行多 run 时
  理论上存在极小概率的读-改-写丢失（丢的是一次调用的记账，不会放大用量）。若以后要严格，
  再加 `msvcrt.locking`/`fcntl.flock` 分支。
- 记账依赖响应里的 `usage`；网关不返回 usage 时该次调用记 0（真实额度仍会消耗）——
  正式赛前用 `doctor`/一次真实调用确认智谱网关返回 usage（规则要求平台侧按 token 计费，正常会返回）。