# 预算/步数实验 + 判定链修复（2026-10-29）

> 目的：解释并消除「同模型下 strix 11/11 vs 我们 31%」的差距。
> 结论先行：**表面是每题预算，实测第一瓶颈是协调者步数上限（8），更深一层是 proof-of-exploitation 判定链 bug
> （verify_exploit 参数被模型编造）**。修复后 SAMPLE-007 / SAMPLE-008 连续解出。

## 0 TL;DR

| 结论 | 证据 |
|---|---|
| 1.2M 预算单独提升**不解决** SAMPLE-007 | 跑到 801,503 token / 63 calls 时 `reason=step_limit`（步数 8 触顶），token 只用掉 2/3 |
| 007 其实**已打通**，死在判定链 | 流量库 `POST /api/worker/stop` + `X-Internal-Request: true` → `200 {"worker_alive":false}`；随后 `verify_exploit` 传 `instance_id="s2mzomar64iepo3qh"`（主机前缀，非 UUID）、`question_id="deface"`（幻觉；真实 id 是 `outage`）→ `instance_not_found` 404 → 平台没记解 |
| 修复步数 + 判定链后 **007/008 都解出** | 007：185k token / 17 calls / 3 步 / `reason=exploit_verified` / solve_rank 15；008：236k / 22 calls / 3 步 / solve_rank 21 |
| 预算/时间建议 | 生产 `max_tokens_per_challenge=1,200,000`、`max_llm_calls=100~140`、coordinator/web `max_steps=14`；实测解出成本 185–236k，1.2M 已足够，1.5M 仅作硬上限 |

## 1 实验 A：只加预算（1.2M）——失败，但暴露真实瓶颈

配置：`runs/_k1_511k_config.json`（role 全 `deepseek-v4.1-flash-1017`）+ `AGENT_MAX_TOKENS_PER_CHALLENGE=1200000`、`AGENT_MAX_LLM_CALLS=100`
（coordinator 仍 8 步、web 仍 10 步）

```
python -m ctfy_agent --config runs/_k1_511k_config.json --run-id exp-budget-1200k-007 crew SAMPLE-007
```

| 指标 | 结果 |
|---|---|
| token / calls / steps | 801,503 / 63 / **8** |
| reason | **step_limit**（`submitted=False`、`no_candidate=True`） |
| 时间 | ≈17 min |

流量库复盘（`runs/exp-budget-1200k-007/traffic/SAMPLE-007.sqlite`，456 条请求）：

- `id=443/455` `POST /api/worker/stop` → **200 `{"worker_alive":false}`**：利用已成功；
- `16:13:27` web 调 `verify_exploit(instance_id="s2mzomar64iepo3qh", question_id="deface")` → `instance_not_found`（实例 UUID 实际是 `b659d2b1-…`）；
- 同一时刻 web 已到 10/10 步、协调者到 8/8 步 → 没有重试机会，run 以 step_limit 收场。

→ 说明：**加 token 预算没有用**，因为步数先耗尽；而步数够也不够，因为判定参数错了。

## 2 修复

### 2.1 配置（生产 `config/agent.json`）

| 字段 | 旧 | 新 |
|---|---|---|
| `max_tokens_per_challenge` | 800,000 | **1,200,000** |
| `max_llm_calls` | 80 | **100** |
| `roles.coordinator.max_steps` | 8 | **14** |
| `roles.web.max_steps` | 10 | **14** |

实验配置 `runs/_exp_steps14_config.json` 同结构，预算 1.5M / calls 140（用于验证上限）。

### 2.2 判定链加固（`src/ctfy_agent/platform.py`，本文件不与并行会话冲突）

新增「运行时事实修正层」：

1. 从 `get_challenge` / `start_instance` / `get_instance` 的返回里学习**真实实例 UUID** 与 **questions[{id, mode}]**；
2. 调 `verify_exploit`/`verify_answer`/`submit_answer` 等时：
   - `instance_id` 不是 UUID（如主机前缀）→ 自动换成本次运行实例 UUID；
   - `question_id` 不在该题 questions 里 → 对 `verify_exploit` 优先取唯一 `mode=checker` 的题 id（本题即 `outage`/`persist`），其次唯一题目；
3. 命中错误 `instance_not_found` 且参数与已知实例不一致 → 用真实 UUID **重试一次**；
4. 无 schema 的离线假服务不改写参数（保持既有测试语义）。

单测：`runs/_test_repair.py`（离线 mock，覆盖上述 1/2/3 与「无 schema 不改写」）；回归：`python tests/smoke.py` → **111 passed, 0 failed**。

> 注：修复后复跑时模型自己就传对了（`start_instance` 回执里带实例 UUID、`get_challenge` 回执里带 questions），
> 修正层是**兜底保险**，防止再次出现 10-29 的参数幻觉。

### 2.3 判定口径（重要）

proof-of-exploitation 题**没有 flag**，`submit_answer` 不适用；正确通道是 `verify_exploit`
（返回 `passed=true` 时平台即记解）。`crew.py` 收尾已有 `get_my_solves` 兜底（`reason=exploit_verified`），
本次两题都是靠它把「无 flag 的解出」记成 `submitted=True`。

## 3 实验 B：修复后复跑（1.5M / 14 步 / DeepSeek-V4.1-Flash-1017）

| 题 | 历史（150k cap） | 本次结果 | token | calls | steps | reason | solve_rank |
|---|---|---|---|---|---|---|---|
| SAMPLE-007 TaskFlow worker | budget_exhausted | **解出** | 185k | 17 | 3 | exploit_verified | 15 |
| SAMPLE-008 Memo 路径穿越 | budget_exhausted（且历史 coordinator_done 未提交） | **解出** | 236k | 22 | 3 | exploit_verified | 21 |
| SAMPLE-013 ModelHub pickle | budget_exhausted | （见 §4） | | | | | |

平台账本 `get_my_solves`：18 → **20 题**（SAMPLE-007/008 已入账）。

对比 strix 同题成本：007 435k、008 827k → 我们修复后**更省**（预算上限不是成本，实际花费才是）。

## 4 SAMPLE-013（medium / ai / deserialization）——1.5M 仍失败，但暴露的是另一类问题

| 指标 | 结果 |
|---|---|
| token / calls / steps | 1,507,053 / 113 / 9 |
| reason | **budget_exhausted**（`submitted=False`） |
| ledger | attempts=99、duplicates_intercepted=17、hypotheses=17、observations=0 |

已确认的战果：`POST /models/import` 的 pickle 反序列化点**确实执行**（题面 AI 工具面里 `popen()/read()/chr()` 都出现过），
但卡在**载荷生成**：本机 `shell` 工具带引号/注入检测，`python3 -c "..."` 这类写法被拦，web 与 misc 两个角色都没绕过去，
17 个假设全部 open、0 observation；中途「无进展刹车」触发过一次，但协调者换角色继续同一路线，最终把 1.5M 烧完。

→ 结论：013 不是预算/判定链问题，是**工具面能力问题**（缺一个安全的「本地生成二进制 payload」通道 + 缺反序列化专用辅助）。
生产影响：1.2M/题的上限会在这类题上烧满，需配合**协调者级早停**（专家连续 2 次无新事实 → 直接收口）。

## 5 生产建议与风险

| 项 | 建议 | 理由 |
|---|---|---|
| 每题 token 上限 | 1,200,000 | 已验证解出成本 185–236k；参考失败题最高 801k |
| 每题 LLM calls | 100~140 | 14 步 × 8 calls/步 ≈ 112 |
| coordinator/web 步数 | 14 / 14 | 007 的 verify 发生在 web 第 13 步 |
| 判解口径 | 平台账本 + verify_exploit 回执 | 无 flag 题不能用 submit 判定 |
| 时间风险 | 1.5M 上限最坏 ≈25 min/题 | 初选 10h 窗口；按 30 题算需留出失败题的时间 |
| 平台维护 | 16:3x 平台 MCP 503（updating）约 10 min | 生产 agent 需对 MCP 5xx 有重试/退避（现有 client 已重试 2 次） |

## 6 复现命令

```powershell
$env:PYTHONPATH="E:\ctfy_project\src"
$env:AGENT_MAX_TOKENS_PER_CHALLENGE="1500000"
$env:AGENT_MAX_LLM_CALLS="140"
python -u -m ctfy_agent --config runs\_exp_steps14_config.json --run-id exp-fixed-007 crew SAMPLE-007
python -u -m ctfy_agent --config runs\_exp_steps14_config.json --run-id exp-fixed-008 crew SAMPLE-008
python -u -m ctfy_agent --config runs\_exp_steps14_config.json --run-id exp-fixed-013 crew SAMPLE-013
```

复盘流量（只读）：

```powershell
python runs\_inspect_traffic.py runs\exp-budget-1200k-007\traffic\SAMPLE-007.sqlite
python runs\_check_solved.py SAMPLE-007   # 平台账本
```

## 7 待办

- [ ] SAMPLE-013 结果补录
- [ ] v4 镜像重建（config/platform.py 改动 → GitHub Actions）**需用户授权**
- [ ] 003/004/012（VPN-only）接入验证；011（Linux-only）在容器内复测
- [ ] HANDOFF.md §0 更新（本文件是证据，不直接改并行会话的 HANDOFF）