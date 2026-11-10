# 资格赛（11-14）与初选赛（11-15）备战方案

> 依据：CCF-TPCTF 赛事规则手册 **v2.5**（2026-11-10 发布，平台附件 `4154270c`，与本地 PDF 同一份）
> + 平台 `/api/v1/competitions` 只读实测（2026-11-10 21:20 CST）
> 本文只覆盖**入围测试 + 初选赛**；出题（11-21）另见 `docs/authoring-plan.md`。

---

## 1. 平台实测事实（这些是硬的，别再猜）

赛事 id：`c0d8b4e3dd3b42c095d63d7dfa66431f`（`TPCTF 2026 · 智能体团队赛道`）

| 项 | 值 | 影响 |
|---|---|---|
| `sessions` | 11-15 10:00–20:00 / 11-21 10:00–20:00 / 11-22 10:00–20:00（CST，各 10h） | 每段只有 10 小时，**不是 24 小时** |
| `session_end_mode` | `delete` + `session_end_grace_s=900` | 段末销毁环境，15 分钟宽限；跨段不能接着跑 |
| `max_instances_per_team_effective` | **3** | 可并行；**不能盲停别的题的实例** |
| `internet_policy` / `agent_internet` | `deny` / `deny` | 运行期零公网，依赖必须预装 |
| `flag_prefix` | `FLAG` | flag 形如 `FLAG{...}` |
| `expects_report` | `false` | 智能体赛道**不交 WP** |
| `format` | `questions` | 即 instance 形态的题目交付 |
| `challenges_withheld` | `true`，`challenge_count=null` | 赛前不知道题量 |
| `registered_count` | **423**（11-10 实测） | 150 入围 → 30 进正式赛 |
| `registration_ends_at` | **11-13 20:00 CST** | 我们已注册（captain，队名「一只七彩尧狼」） |
| `ordering` | score desc → flags_solved desc → last_solve_at asc | 同分看解题数，再早解优先 |
| `authoring_windows` | 11-21 10–12 / 14–16 / 18–20 | 出题三轮，每轮 2h |
| `authoring_review` / `smoke_quota` | `human` / 10 | 人工审，试运行 10 次 |

**11-14 入围测试**：平台上目前**没有** 11-14 的 competition（sessions 从 11-15 开始），最可能沿用
SAMPLE / practice 机制下发若干模拟题。我们 11-09 已经用 v6.9 在 SAMPLE 上跑到 5/6，机制是通的。

---

## 2. 手册 v2.5 里真正约束我们的 5 条

1. **解题范围是全类别**：初选赛「解答命题团队提供的**常规 CTF 赛题**」，Crypto/Reverse/Misc/Web/Pwn/AI 都算。
   只有**出题**限定 Web/Pwn/AI Security。以前文档写成「赛道仅 Web/Pwn/AI」是错的，已改 `PROGRESS.md`。
2. **网络**：解题 Agent 无公网，只能访问平台接口、大模型网关、题目入口；工具依赖须预装；
   不得用代理/重定向/子进程绕过。
3. **模型**：入围及初选赛可用白名单 **12 家**（含百炼 `compatible-mode`、DeepSeek 官方），团队自备 key；
   正式赛只能用智谱 `open.bigmodel.cn` 且 **Token 总量 1B**。
4. **晋级**：423 队 → 入围 150 → 初选赛 → **前 30** 进正式赛。
5. **纪律**：不得重复参加两个赛道；账号唯一；只打授权目标；不得爆破提交接口。

---

## 3. 本轮代码改动（已改完，回归 175/0 + 独立套件全绿）

| # | 文件 | 改动 | 为什么现在改 |
|---|---|---|---|
| 1 | `solver.py` | `_evict_stale_instances(challenge_id, quota)`：从 429 文案解析团队配额，**配额 ≥2 时改走 `instance_guard.reap` 的年龄判定**，不再盲停别的题的实例 | 平台配额从 1 变 3。旧逻辑会把并行兄弟任务的实例当残留杀掉，三个 run 一起崩 |
| 2 | `solver.py` | 429/瞬时失败给模型的提示改成配额中性与「先 list_instances 看清谁在占」 | 旧文案硬写「配额只有 1 个」，在 3 配额下是错误指令 |
| 3 | `agent_main.py` | 新增 `UNSUPPORTED_RUN_MODES`（defence/defense/awd/engagement）显式拒绝；**未知或空的 run_mode 降级按 instance 跑**，并记 `config.extra["degraded_run_mode"]` | 旧实现任何没见过的模式一律 `refused` = 整题弃权。平台换个词我们就 0 分 |
| 4 | `llm.py` | `enable_thinking` 只在 `*.aliyuncs.com` 类端点发送 | 正赛换智谱时该字段可能吃 400；旧实现无条件发 |
| 5 | `config/agent.json` | `oracle_limit` 300 → **100** | 自校验 300 次/题在「禁止爆破提交接口」上解释不清；正常用量 <20 |

回归：`tests/smoke.py` **175 passed / 0 failed**；`tests/smoke_*.py` 独立套件全绿（8 个需 `.env` 的按 CI 口径跳过）。

> 兼容性说明：配额解析不出数字时（老平台只回 `team_capacity_exceeded`），仍走**旧的盲清逻辑**，
> 所以 CTFARENA 练习套（配额 1）的行为与 v6.9 完全一致，没有回归风险。

---

## 4. 倒排（今天 11-10 21:30 起）

| 时间 | 事 | 谁 |
|---|---|---|
| **11-10 今晚** | ① 确认团队只报了智能体赛道（双赛道冲突是出局项）② 确认 `CTFY_TOKEN` 是本队专用 | 你（5 分钟） |
| **11-10 今晚** | 把 v7 提平台审核（浏览器：镜像 → 提交 → 等协议检查），**不要覆盖 v6.9** | 你 |
| 11-11 | 审核通过后跑一次 SAMPLE 复核（确认 v7 在真平台能出分、且没被 `refused`） | 我 |
| 11-11 | 若审核结论要求改，出 v7.1；否则锁定镜像 | 我 |
| 11-12 | 全量回归 + 一次长跑压测（连跑 3 题，验证实例不会被互杀） | 我 |
| 11-13 20:00 | 报名截止（已报名，仅确认） | 你 |
| **11-14** | 入围测试：全程盯 `result.json` 的 `stop_reason`，出现 `refused` 立即看 `degraded_run_mode` | 我 |
| **11-15 10:00–20:00** | 初选赛。每段 10h、题量未知、配额 3 | 我 |

---

## 5. 已知残余风险（本轮不解决，记在这里）

1. **出题能力为 0**（P0，占最终 50%）。11-21 才用得上，11-15 之后有「两赛段之间」的更新窗口（需登记），
   到时出 v8。**但 11-14 入围测试若下发出题任务，我们会 `refused`** —— 现在的降级改动至少让它按通用链路跑
   而不是直接弃权。
2. **没有 1B token 的全局计数器**：只有单题 800k。按 30 题估算约 24M，撞不上；但没有额度耗尽的降级手段。
3. **运行记录归属**：transcript 在 `/ctfy/work/runs`，`session_end_mode=delete` 会销毁环境。
   智能体赛道 `expects_report=false`，核验材料大概率由平台自己留，待 11-14 现场确认。
4. **题量未知 + 10 小时**：若题量 >40，单题可用时间不足 15 分钟。当前 `max_steps=12`、单题 800k 偏保守，
   赛前可视 11-14 的实测吞吐再调 `AGENT_DEADLINE_MINUTES`。

---

## 6. 一句话结论

11-14/11-15 这两场**不考出题、只考解题**，我们现在的短板不在能力而在「别被平台形态变化坑掉」：
配额 1→3 的互杀 bug、未知 run_mode 的弃权、智谱字段兼容——这三个都已在本轮修掉并过回归。
剩下的关键路径是**把 v7 提审并在真实平台上跑一次**，这一步只能你来操作浏览器。
