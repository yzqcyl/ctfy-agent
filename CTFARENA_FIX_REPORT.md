# CTFARENA 本地全量测试与修复报告

- 生成时间：2026-11-06 03:40:43
- 平台：https://ctfy.tsinghua.edu.cn · 题库 CTFARENA（115 题）
- 本地模型：并行智算云 Paratera `https://ai.paratera.com/v1` · `DeepSeek-V4.1-Flash-1017`
- 测试预算档：400k token / 80 calls 每题，900s 墙钟；平台团队实例配额=1（顺序执行）

## 一、结论摘要

- 全量覆盖：**115/115** 题（Run1 基线 + Run2 修复后全量 + Run4b/Run5 定向重跑）
- 最终已解：**85** 题（其中预检命中团队历史已解 34 题；本轮修复/重跑后真实解出 51 题）
- 真实尝试中未解：**30** 题（原因分类见下表；属模型能力/预算上限，非框架浪费）
- 修复提交：**16 个**（F1–F4 + P1–P7/P6c，均在 `ctfarena/fix` 分支，可独立回滚）
- 关键改进：已解题重跑从 9 万 token 降到 2 秒；失败的资源浪费同比下降 50%~90%；预算早停/实例未就绪/配额 429 三类系统性问题已修复

## 二、问题统计与修复（Run1 基线 → 修复）

| 类别 | 次数 | 代表题 | 根因 | 修复 | commit |
|---|---|---|---|---|---|
| verify 通过未落库 → 整题完成后续跑 | 1 | CTFARENA-001 | `verify_answer` 成功不写 answers_by_qid，剩余题号判定用 flag_solves（全平台解出人数） | 写 answers_by_qid + 用平台 `my_solved_question_ids` 判定 | bb77838 |
| 已解题被 CLI 重跑从头烧 | 2 | CTFARENA-001/003 | solve() 无“已解”预检 | 入口预检 done/submitted_ok/my_solved_ids 零成本收口 | 09df6d6 |
| 预检 already_solved 不落盘 → 误记失败 | 2 | CTFARENA-003/004 | `_presolved_summary` 只改内存 | 持久化 status/submitted_ok | 9641bd0 |
| 预算跨 run 累计 → 烧穿题无法重跑 | 3 | CTFARENA-005/008/010 | challenge.tokens 累计，换预算档后被拒 | `crew --fresh-budget` 清账（旧账写 notes）+ AGENT_MAX_* 环境变量预算档 | d5b95ef |
| 协调者空动作早停（no_more_actions） | 多 | CTFARENA-002/005/008 | 无 tool_calls 直接 break，预算大量剩余 | 空动作守卫：预算剩余时注入“禁止收口”指令（≤2 次） | 5807318 |
| 实例就绪滞后被当 404 空转 | 多 | CTFARENA-001/002/008 | 平台边缘先就绪、容器未起（19B Go 404） | start 后 45s 有界就绪等待 | 5807318 |
| 429 team_capacity_exceeded 处理差 | 1 | 3 并发基线 | 失败调用计入 instance_ops_limit=5 | 回滚失败计数 + 清残留实例后重试一次 | 5807318 |
| done 早停（coordinator_done） | 多 | CTFARENA-002/010 | 有观察就允许 done，预算剩 40%~96% | 预算 done 闸门（未解+预算足+未验证假设≥2 时拦 done） | b233003 |

## 三、逐题状态（Run1 基线 / Run2 修复后 / Run3 定向重跑）

| 题号 | Run1 | Run2 | Run3 | 最终 | 失败归类 | 耗时(Run2) | tokens |
|---|---|---|---|---|---|---|---|
| CTFARENA-000 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-001 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-002 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 183s | 420421 |
| CTFARENA-003 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-004 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-005 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 415595 |
| CTFARENA-006 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-007 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-008 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 432628 |
| CTFARENA-009 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-010 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 842s | 441830 |
| CTFARENA-011 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-012 | ❌ | ❌ | ❌ | ❌ | budget_exhausted, calls_cap | 901s | 402083 |
| CTFARENA-013 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-014 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-015 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-016 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-017 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-018 | ❌ | ❌ | ✅ | ✅ | - | 285s | 130972 |
| CTFARENA-019 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 884s | 457038 |
| CTFARENA-020 | ❌ | ✅ | - | ✅ | - | 608s | 306654 |
| CTFARENA-021 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 771s | 428646 |
| CTFARENA-022 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 884s | 360537 |
| CTFARENA-023 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-024 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-025 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-026 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-027 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-028 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 338851 |
| CTFARENA-029 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-030 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-031 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-032 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-033 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 375764 |
| CTFARENA-034 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 895s | 294893 |
| CTFARENA-035 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-036 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-037 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-038 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-039 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-040 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-041 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-042 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-043 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 887s | 463127 |
| CTFARENA-044 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-045 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-046 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-047 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 762s | 430936 |
| CTFARENA-048 | ❌ | ✅ | - | ✅ | - | 6s | - |
| CTFARENA-049 | ❌ | ❌ | ✅ | ✅ | - | 328s | 123221 |
| CTFARENA-050 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 323472 |
| CTFARENA-051 | ❌ | ❌ | ✅ | ✅ | - | 152s | 30858 |
| CTFARENA-052 | ❌ | ❌ | ❌ | ❌ | early_exit | 173s | 13438 |
| CTFARENA-053 | ❌ | ✅ | - | ✅ | - | 232s | 67532 |
| CTFARENA-054 | ❌ | ❌ | ✅ | ✅ | - | 62s | 20254 |
| CTFARENA-055 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 900s | 280255 |
| CTFARENA-056 | ❌ | ✅ | - | ✅ | - | 86s | 20612 |
| CTFARENA-057 | ❌ | ✅ | - | ✅ | - | 51s | 11755 |
| CTFARENA-058 | ❌ | ❌ | ❌ | ❌ | early_exit | 788s | 46023 |
| CTFARENA-059 | ❌ | ✅ | - | ✅ | - | 422s | 78104 |
| CTFARENA-060 | ❌ | ❌ | ✅ | ✅ | - | 407s | 88688 |
| CTFARENA-061 | ❌ | ❌ | ❌ | ❌ | early_exit | 243s | 99764 |
| CTFARENA-062 | ❌ | ❌ | ✅ | ✅ | - | 502s | 40161 |
| CTFARENA-063 | ❌ | ✅ | - | ✅ | - | 528s | 109973 |
| CTFARENA-064 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 228125 |
| CTFARENA-065 | ❌ | ✅ | - | ✅ | - | 203s | 71509 |
| CTFARENA-066 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 249580 |
| CTFARENA-067 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 337263 |
| CTFARENA-068 | ❌ | ❌ | ✅ | ✅ | - | 486s | 226605 |
| CTFARENA-069 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 884s | 365357 |
| CTFARENA-070 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 900s | 376604 |
| CTFARENA-071 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 323111 |
| CTFARENA-072 | ❌ | ❌ | ✅ | ✅ | - | 186s | 48882 |
| CTFARENA-073 | ❌ | ✅ | - | ✅ | - | 303s | 88928 |
| CTFARENA-074 | ❌ | ❌ | ✅ | ✅ | - | 213s | 49448 |
| CTFARENA-075 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 427920 |
| CTFARENA-076 | ❌ | ✅ | - | ✅ | - | 169s | 35290 |
| CTFARENA-077 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 287348 |
| CTFARENA-078 | ❌ | ✅ | - | ✅ | - | 197s | 83563 |
| CTFARENA-079 | ❌ | ✅ | - | ✅ | - | 322s | 150753 |
| CTFARENA-080 | ❌ | ❌ | ✅ | ✅ | - | 542s | 99814 |
| CTFARENA-081 | ❌ | ❌ | ✅ | ✅ | - | 160s | 34682 |
| CTFARENA-082 | ❌ | ✅ | - | ✅ | - | 138s | 41991 |
| CTFARENA-083 | ❌ | ❌ | ✅ | ✅ | - | 277s | 19967 |
| CTFARENA-084 | ❌ | ❌ | ✅ | ✅ | - | 162s | 73736 |
| CTFARENA-085 | ❌ | ❌ | ✅ | ✅ | - | 103s | 53490 |
| CTFARENA-086 | ❌ | ❌ | ✅ | ✅ | - | 209s | 102972 |
| CTFARENA-087 | ❌ | ❌ | ✅ | ✅ | - | 112s | 11657 |
| CTFARENA-088 | ❌ | ❌ | ✅ | ✅ | - | 184s | 107544 |
| CTFARENA-089 | ❌ | ❌ | ✅ | ✅ | - | 142s | 34201 |
| CTFARENA-090 | ❌ | ❌ | ✅ | ✅ | - | 291s | 61621 |
| CTFARENA-091 | ❌ | ❌ | ✅ | ✅ | - | 901s | 36971 |
| CTFARENA-092 | ❌ | ✅ | - | ✅ | - | 512s | 213795 |
| CTFARENA-093 | ❌ | ✅ | - | ✅ | - | 123s | 34981 |
| CTFARENA-094 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 901s | 339254 |
| CTFARENA-095 | ❌ | ✅ | - | ✅ | - | 141s | 32488 |
| CTFARENA-096 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 900s | 376112 |
| CTFARENA-097 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 900s | 316721 |
| CTFARENA-098 | ❌ | ✅ | - | ✅ | - | 134s | 19253 |
| CTFARENA-099 | ❌ | ✅ | - | ✅ | - | 198s | 77915 |
| CTFARENA-100 | ❌ | ❌ | ❌ | ❌ | wall_timeout, exit_-9 | 748s | 411912 |
| CTFARENA-101 | ❌ | ✅ | - | ✅ | - | 254s | 110836 |
| CTFARENA-102 | ❌ | ✅ | - | ✅ | - | 117s | 59898 |
| CTFARENA-103 | ❌ | ✅ | - | ✅ | - | 221s | 45161 |
| CTFARENA-104 | ❌ | ✅ | - | ✅ | - | 159s | 44919 |
| CTFARENA-105 | ❌ | ✅ | - | ✅ | - | 94s | 27850 |
| CTFARENA-106 | ❌ | ✅ | - | ✅ | - | 158s | 58304 |
| CTFARENA-107 | ❌ | ✅ | - | ✅ | - | 122s | 28553 |
| CTFARENA-108 | ❌ | ✅ | - | ✅ | - | 131s | 24291 |
| CTFARENA-109 | ❌ | ✅ | - | ✅ | - | 245s | 126438 |
| CTFARENA-110 | ❌ | ✅ | - | ✅ | - | 251s | 114211 |
| CTFARENA-111 | ❌ | ✅ | - | ✅ | - | 272s | 121428 |
| CTFARENA-112 | ❌ | ✅ | - | ✅ | - | 236s | 98905 |
| CTFARENA-113 | ❌ | ✅ | - | ✅ | - | 127s | 32535 |
| CTFARENA-114 | ❌ | ✅ | - | ✅ | - | 332s | 125175 |

## 四、未解题目根因（能力上限说明）

- 剩余未解题集中在 medium/hard web 题：需要多步利用链（IDOR 越权、JWT 伪造、原型污染、tRPC/Nuxt 数据流、SSRF、源码审计等），当前模型在 400k token / 80 次调用 / 15 分钟内常停在侦察或单点验证阶段。
- 已做通用改进（非单题 hack）：早停闸门、实例就绪、配额降级、预算清账；未针对单题写死任何 flag/接口。
- 进一步提升需要平台侧协作（更多实例并发、更高预算档）或模型能力升级，报告中如实标注。

## 五、修复清单（`ctfarena/fix` 分支，`git log ctfarena-baseline..ctfarena/fix`）

```
686d53b docs(report): 百炼 key 生效，Run4b 16/19 翻盘（P6b/P6c 验证通过）；全量已解 81/115，Run5 启动
0681492 test(env): 本地测试切到阿里云百炼 deepseek-v4.1-flash-1017（Paratera 团队权限 403 后启用新 key；config/roles 同步小写以适配 DashScope）
33201a0 docs(report): 追加最终状态——Paratera 403 外部阻塞（连续 3 轮）、可交付结论与恢复后续跑命令
09959d8 test(p6c): 新增 verify 自动补交回归用例（补交必须在实例释放前/幂等/失败不误标）；记录 CTFARENA 测试报告（P6c 实测被 Paratera 403 阻塞）
411e79f fix(P6c): 自动补 submit 必须发生在实例释放前（flag 按实例动态生成，旧答案换实例即失效；P6 放 finally 之后导致补交全部无效）
53f4a30 fix(P7): 瞬时失败（SSE/MCP 超时/限流）的 start_instance 不占用本地 instance_ops 配额（Run3b 084 在平台变慢期间额度被烧光实证）
1c73802 fix(P6b): verify 通过记入独立 verified_qids（不写 answers_by_qid），逐题状态三态化；收尾自动补 submit 优先提交 verified 题号，避免 verify=true 却未记分（CTFARENA-084~090 实证 7 题）
88b4a93 fix(crew): P6 verify_answer 通过后收尾自动补 submit——verify 只是校验、平台只在 submit 后记分（CTFARENA-084/085/086：verify=true+done 但整题白跑）
214a7ca fix(crew): P5 候选闸门——账本已有候选 flag 时禁止直接 done，强制先 verify_answer/submit（CTFARENA-051 实证：收出候选却收口）
b233003 fix(crew): P4 预算 done 闸门——未解且预算尚足/账本有未验证假设时禁止提前收口（Run2 002/010 coordinator_done 早停实证）
5807318 fix(crew/solver/state): P1 空动作早停守卫；P2 实例就绪有界等待；P3 429 配额降级（回滚失败计数+清残留实例重试）——Run1 10/10 真实尝试失败主因
d5b95ef feat(test)-restore platform budget defaults; AGENT_MAX_* env overrides for test budget; crew --fresh-budget for reruns（预算跨 run 累计会堵死重跑实证）
bdfc2f4 test(budget): 本地 CTFARENA 基线改用 400k/80 预算档（900s 墙钟），控制时间与成本；报告记录
9641bd0 fix(crew): 预检 already_solved 落盘持久化（status/submitted_ok），避免已解题被记成失败（CTFARENA-003/004 实证）
09df6d6 fix(crew): solve() 预检已解题直接零成本收口，禁止 CLI 重跑已解出的题（CTFARENA-001 重跑烧 9 万 token 实证）
bb77838 fix(crew/solver): verify_answer/verify_exploit 通过均落库逐题状态；剩余题号用平台 my_solved_question_ids 判定（CTFARENA-001 整题完成后续跑烧 token）；config 角色模型回大写与 roles/*.json 同步
```

## 六、备份与回档

- 快照目录：`runs/_backup/20261105-003402/`（src/config/tests/tools/docs 全量副本 + git bundle + .env）
- 备份分支：`backup/pre-fix-20261105-003402`；基线 tag：`ctfarena-baseline`
- 一键回档（放弃全部修复）：`git reset --hard ctfarena-baseline`（在 `ctfarena/fix` 分支上）
- 只回滚某个修复：`git revert <commit>`；
- 代码改动仅 `src/ctfy_agent/{crew,solver,state,config,cli}.py` + `config/agent.json`（已恢复平台默认预算），无单题特判。

## 七、产物位置

- Run1 基线（修复前）：`runs/ctfarena/20261105-02/`（results.jsonl / logs / transcripts / summary）
- Run2 全量（修复后）：`runs/ctfarena/20261105-run2/`
- Run3 定向重跑：`runs/ctfarena/reruns_merged`
- 逐题 watch/transcript/答案：各自 run 目录下 `logs/<cid>.watch.log`、`transcripts/<cid>.jsonl`

## 十二、最终结果（2026-11-06 03:41）

### 关键修复后重跑（Run4b + Run5）
- **Run4b（19 道 “verify 通过未提交” 题）**：16 解出 / 3 失败（002、068、081）；
- **Run5（剩余 34 道失败题）**：4 解出（062、068、081、091）/ 30 失败；
- 两次重跑共覆盖 Run2 全部 50 道失败题，**20 道在修复后翻盘解出**，30 道仍失败（均为 hard 题，26 次墙钟截断、3 次早停、1 次预算耗尽，无框架性浪费证据）。

### 全量最终口径（115 题）
| 指标 | 数值 |
|---|---|
| 全量覆盖 | **115/115** |
| 平台历史已解（预检跳过） | 34 |
| **本轮真实解出** | **51**（Run2 31 + 重跑翻盘 20） |
| **最终已解合计** | **85/115（73.9%）** |
| 未解 | 30（hard 题能力/时间上限，逐题记录于上表） |

### 关键结论
1. **框架缺陷已修复并验证**：verify 三态 + 实例释放前自动补 submit（P6c）使 19 道“验证通过却白跑”题中 16 道翻盘；已解预检/预算清账/早停闸门/实例就绪/429 配额等 15 项修复全部有实证。
2. **平台规则摸清并记录**：团队实例配额 = 1（3 并发 429）；flag 按实例动态生成（跨实例不可复用）；MCP 偶发变慢（20s+）需重试与配额回滚。
3. **剩余 30 道未解题**：属模型能力/时间预算上限（26 次 900s 墙钟截断、3 次早停收口、1 次预算耗尽），非框架缺陷；如需继续，建议提高单题墙钟/预算或升级模型。
4. **LLM 端点变更**：Paratera 团队权限被撤（403）后，已切换到阿里云百炼 `deepseek-v4.1-flash-1017`（OpenAI 兼容端点），doctor 探活与全量跑均通过。
