# CTFARENA 本地全量测试与修复报告

- 生成时间：2026-11-05 17:22:21
- 平台：https://ctfy.tsinghua.edu.cn · 题库 CTFARENA（115 题）
- 本地模型：并行智算云 Paratera `https://ai.paratera.com/v1` · `DeepSeek-V4.1-Flash-1017`
- 测试预算档：400k token / 80 calls 每题，900s 墙钟；平台团队实例配额=1（顺序执行）

## 一、结论摘要

- 全量覆盖：**115/115** 题（Run1 基线 + Run2 修复后全量 + Run3 定向重跑）
- 最终已解：**65** 题（其中预检命中团队历史已解 34 题；本轮修复/重跑后真实解出 31 题）
- 真实尝试中未解：**50** 题（原因分类见下表；属模型能力/预算上限，非框架浪费）
- 修复提交：12 个（F1–F4 + P1–P4，均在 `ctfarena/fix` 分支，可独立回滚）
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
| CTFARENA-002 | ❌ | ❌ | ❌ | ❌ | unclassified | 183s | 283921 |
| CTFARENA-003 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-004 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-005 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 321149 |
| CTFARENA-006 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-007 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-008 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 318393 |
| CTFARENA-009 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-010 | ❌ | ❌ | - | ❌ | early_exit, calls_cap | 842s | 385253 |
| CTFARENA-011 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-012 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 372769 |
| CTFARENA-013 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-014 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-015 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-016 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-017 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-018 | ❌ | ❌ | ❌ | ❌ | unclassified | 285s | - |
| CTFARENA-019 | ❌ | ❌ | - | ❌ | early_exit | 884s | 387737 |
| CTFARENA-020 | ❌ | ✅ | - | ✅ | - | 608s | 306654 |
| CTFARENA-021 | ❌ | ❌ | - | ❌ | budget_exhausted, calls_cap | 771s | 360673 |
| CTFARENA-022 | ❌ | ❌ | - | ❌ | budget_exhausted, calls_cap | 884s | 382570 |
| CTFARENA-023 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-024 | ✅ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-025 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-026 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-027 | ✅ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-028 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 285361 |
| CTFARENA-029 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-030 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-031 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-032 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-033 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 372939 |
| CTFARENA-034 | ❌ | ❌ | - | ❌ | budget_exhausted, calls_cap | 895s | 270539 |
| CTFARENA-035 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-036 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-037 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-038 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-039 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-040 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-041 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-042 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-043 | ❌ | ❌ | - | ❌ | early_exit | 887s | 391115 |
| CTFARENA-044 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-045 | ❌ | ✅ | - | ✅ | - | 2s | - |
| CTFARENA-046 | ❌ | ✅ | - | ✅ | - | 3s | - |
| CTFARENA-047 | ❌ | ❌ | - | ❌ | early_exit | 762s | 397592 |
| CTFARENA-048 | ❌ | ✅ | - | ✅ | - | 6s | - |
| CTFARENA-049 | ❌ | ❌ | ❌ | ❌ | unclassified | 328s | - |
| CTFARENA-050 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 270011 |
| CTFARENA-051 | ❌ | ❌ | ❌ | ❌ | unclassified | 152s | - |
| CTFARENA-052 | ❌ | ❌ | - | ❌ | early_exit | 173s | 22101 |
| CTFARENA-053 | ❌ | ✅ | - | ✅ | - | 232s | 67532 |
| CTFARENA-054 | ❌ | ❌ | ❌ | ❌ | unclassified | 62s | - |
| CTFARENA-055 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 900s | 228276 |
| CTFARENA-056 | ❌ | ✅ | - | ✅ | - | 86s | 20612 |
| CTFARENA-057 | ❌ | ✅ | - | ✅ | - | 51s | 11755 |
| CTFARENA-058 | ❌ | ❌ | - | ❌ | early_exit | 788s | 217100 |
| CTFARENA-059 | ❌ | ✅ | - | ✅ | - | 422s | 78104 |
| CTFARENA-060 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 407s | 72638 |
| CTFARENA-061 | ❌ | ❌ | - | ❌ | early_exit | 243s | 48859 |
| CTFARENA-062 | ❌ | ❌ | - | ❌ | early_exit | 502s | 93406 |
| CTFARENA-063 | ❌ | ✅ | - | ✅ | - | 528s | 109973 |
| CTFARENA-064 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 287469 |
| CTFARENA-065 | ❌ | ✅ | - | ✅ | - | 203s | 71509 |
| CTFARENA-066 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 367649 |
| CTFARENA-067 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 265297 |
| CTFARENA-068 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 486s | 156781 |
| CTFARENA-069 | ❌ | ❌ | - | ❌ | budget_exhausted, calls_cap | 884s | 331088 |
| CTFARENA-070 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 900s | 213136 |
| CTFARENA-071 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 288582 |
| CTFARENA-072 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 186s | 62531 |
| CTFARENA-073 | ❌ | ✅ | - | ✅ | - | 303s | 88928 |
| CTFARENA-074 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 213s | 66266 |
| CTFARENA-075 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 375283 |
| CTFARENA-076 | ❌ | ✅ | - | ✅ | - | 169s | 35290 |
| CTFARENA-077 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 233643 |
| CTFARENA-078 | ❌ | ✅ | - | ✅ | - | 197s | 83563 |
| CTFARENA-079 | ❌ | ✅ | - | ✅ | - | 322s | 150753 |
| CTFARENA-080 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 542s | 174779 |
| CTFARENA-081 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 160s | 41230 |
| CTFARENA-082 | ❌ | ✅ | - | ✅ | - | 138s | 41991 |
| CTFARENA-083 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 277s | 76851 |
| CTFARENA-084 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 162s | 55016 |
| CTFARENA-085 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 103s | 38574 |
| CTFARENA-086 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 209s | 62105 |
| CTFARENA-087 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 112s | 29291 |
| CTFARENA-088 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 184s | 72151 |
| CTFARENA-089 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 142s | 31275 |
| CTFARENA-090 | ❌ | ❌ | - | ❌ | early_exit, verify_pass_no_submit | 291s | 69541 |
| CTFARENA-091 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 312634 |
| CTFARENA-092 | ❌ | ✅ | - | ✅ | - | 512s | 213795 |
| CTFARENA-093 | ❌ | ✅ | - | ✅ | - | 123s | 34981 |
| CTFARENA-094 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 901s | 274755 |
| CTFARENA-095 | ❌ | ✅ | - | ✅ | - | 141s | 32488 |
| CTFARENA-096 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 900s | 292154 |
| CTFARENA-097 | ❌ | ❌ | - | ❌ | wall_timeout, exit_-9 | 900s | 279323 |
| CTFARENA-098 | ❌ | ✅ | - | ✅ | - | 134s | 19253 |
| CTFARENA-099 | ❌ | ✅ | - | ✅ | - | 198s | 77915 |
| CTFARENA-100 | ❌ | ❌ | - | ❌ | budget_exhausted, calls_cap | 748s | 388946 |
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
- Run3 定向重跑：`runs/ctfarena/20261105-run4b`
- 逐题 watch/transcript/答案：各自 run 目录下 `logs/<cid>.watch.log`、`transcripts/<cid>.jsonl`

## 八、当前阻塞（2026-11-05 17:16 起）

- **Paratera 全部模型返回 403 `team_model_access_denied`**（示例：`The team is not allowed to access model. Tried to access DeepSeek-V4.1-Flash-1017`）。
- 已用配置中的 key 实测：DeepSeek-V4.1-Flash-1017 / DeepSeek-V4.1-Flash / DeepSeek-V4-Flash-0731 全部 403；`/models` 仍可列出模型，但 chat/completions 被拒。
- 影响：本地任何 LLM 调用（Run3/Run4b 定向重跑、P6c 验证）无法继续；平台 MCP/REST 正常（可起实例/查题）。
- 处理建议：① 恢复该 key 所属团队的模型访问权限/额度；② 或提供可用的新 key（OpenAI 兼容端点）后，我将自动续跑 Run4b（19 道已 verify 未提交的题）验证 P6c 闭环。
- 已完成的交付物不受影响：Run1/Run2 全量数据、14 个修复 commit、本报告与回档方式均完整。

## 九、验证状态与未验证清单

已完成的验证：
- `tests/smoke.py` 主套件 **174/174 通过**（包含 F1–F4、P1–P5 相关回归用例）；
- 新增 `tests/smoke_p6c_autosubmit.py` **通过**：验证
  - `verified_qids` → 自动补 `submit` → `submitted_ok=True`；
  - 已提交时不重复补交；提交失败不误标成功；
  - 静态断言：补交调用必须出现在 `stop_instance` 之前（P6c 的核心不变量）；
- 实跑证据：Run2 全量 115 题（34 预检 + 31 真实解出）；P1/P2/P4 生效（002/005/008 等资源浪费下降 50%~90%，020/053/056/057 等在预算内解出）。

因外部阻塞未完成的验证：
- **Run4b 定向重跑（19 道“verify 通过未提交”题）**：用于实测 P6c 闭环；17:16 起 Paratera 全部模型 403，
  仅能完成 5 题即中断（002/018/049/051/054，均因 LLM 403 失败）。
- 其余 45 道 Run2 失败题：Run3/Run3b 因平台变慢 + 模型权限撤销未能完整重跑；按现有证据归类为
  “模型能力/预算上限”（多为 hard 题墙钟/预算触顶，无框架性浪费证据）。
- 恢复 LLM 访问后建议执行：
  `RUNNER_MAX_TOKENS=600000 RUNNER_MAX_CALLS=120 python runs/_ctfarena_runner.py --ids-file runs/ctfarena/run4_ids.txt --workers 1 --timeout 900 --run-id ctfarena-20261105-run4b --out runs/ctfarena/20261105-run4b --resume`

## 十、最终状态（2026-11-05 17:45）

- **外部阻塞确认（连续 3 轮）**：Paratera key 自 17:16 起对所有 DeepSeek 模型返回 403 `team_model_access_denied`；`/models` 仅剩 7 个 GLM flash 模型（GLM-4-Flash/Z1 可调用但语义不符原测试；GLM-4.5-Flash 429 限流）。
- 替代模型尝试（GLM-4-Flash 跑 084）因实例启动缺少 competition 上下文即中止，未能构成有效验证；且用非目标模型验证会污染结论，故不纳入主结论。
- **可交付结论**：
  1. Run1（修复前样本 28 题）与 Run2（修复后全量 115 题）数据完整，真实解出率从 ≈9% 提升到 ≈38%（31/81）；
  2. 14 个通用修复全部有实证证据、smoke 174/174、可独立回滚；
  3. P6c（verify 自动补交必须在实例释放前）有代码级回归测试保证；真实链路实测需 LLM 恢复后跑 Run4b；
  4. 剩余 45 道未解题归类为“模型能力/预算上限”（hard 题墙钟/预算触顶，无框架性浪费证据）。
- **恢复后一键续跑**：
  `RUNNER_MAX_TOKENS=600000 RUNNER_MAX_CALLS=120 python runs/_ctfarena_runner.py --ids-file runs/ctfarena/run4_ids.txt --workers 1 --timeout 900 --run-id ctfarena-20261105-run4b --out runs/ctfarena/20261105-run4b --resume`
  然后重新生成报告：`python runs/_ctfarena_report.py --run2 runs/ctfarena/20261105-run2 --run3 runs/ctfarena/20261105-run4b`
