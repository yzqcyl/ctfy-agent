# docs 索引（2026-11-12 整理）

> 规则：**`docs/` 根只放"当前有效"的文档**；被取代但仍有取证价值的历史文档统一进
> `docs/archive/`（保留 git 历史，不删）。找东西先看本页。

## 当前权威文档

| 文档 | 用途 | 什么时候看 |
|---|---|---|
| [capability-report-2026-11-12.md](capability-report-2026-11-12.md) | **能力评估**：各题型能力范围、真实题库成绩、缺口清单、验证方案 | 想知道"agent 现在能干什么/差什么" |
| [HANDOFF-2026-11-10-r2.md](HANDOFF-2026-11-10-r2.md) | 主交接文档（持续更新）：当前状态、机制、踩坑、未完项 | 接手/续接第一份 |
| [HANDOFF-2026-11-10.md](HANDOFF-2026-11-10.md) | 上一版交接（r2 的 §3–§5 仍以它为准：容器契约、预算、5 轮口径） | 查契约/预算细节 |
| [submit-image-runbook.md](submit-image-runbook.md) | 镜像构建/提交/验收操作单 + **范围红线**（不带 GUI/多平台适配器）+ v7.3 待带项 | 出镜像、验镜像 |
| [audit-rules-v2.5-2026-11-10.md](audit-rules-v2.5-2026-11-10.md) | 比赛规则 v2.5 逐条审计（15 处风险点 + 已修状态） | 正赛前合规复核 |
| [audit-qa-2026-11-11.md](audit-qa-2026-11-11.md) | 问答/规则类审计（A1–A7 等，含状态） | 查"这条到底修了没" |
| [audit-code-2026-11-12.md](audit-code-2026-11-12.md) | **全项目代码审计**（安全/质量/冗余/算法，两轮只读）：S1–S8、C1–C5，含处置建议；§六 是对外部审计的逐条对照 | 安全复核、准备改动前 |
| [audit-external-2026-11-12.md](audit-external-2026-11-12.md) | 外部（另一 agent）审计报告原文，两轮只读；处置结论见 `audit-code-2026-11-12.md` §六 | 想看外部视角的原始表述 |
| [domain-tools.md](domain-tools.md) | 本地域工具参考（参数/输出/陷阱） | 改工具或调提示词时 |
| [pylibs-bridge.md](pylibs-bridge.md) | 第三方库接入（镜像内置清单 + 本机外挂 + Windows 限制矩阵） | 动库/z3/pwntools 时 |
| [ui-design-2026-11-10.md](ui-design-2026-11-10.md) | GUI 设计规范（本地开发用，**不进提交镜像**） | 改 GUI 时 |
| [global-token-ledger.md](global-token-ledger.md) | 跨题/跨进程 token 总账口径 | 查用量/预算来源 |
| [plan-hardening-2026-11-10.md](plan-hardening-2026-11-10.md) | 硬化计划（P0/P1/P2 项与状态） | 排优先级 |
| [plan-prelim-2026-11-15.md](plan-prelim-2026-11-15.md) | 资格赛/初选赛方案（正式赛内容，当前挂起） | 恢复正式赛工作时 |
| [mcp-tools.json](mcp-tools.json) | 平台 MCP 工具目录快照（生成物，`python -m ctfy_agent.cli tools` 可重生成） | 查工具 schema |
| [OPENING.txt](OPENING.txt) | 新对话开场浓缩包（粘给新会话用） | 开新会话 |

## docs/archive/ —— 历史文档（已被取代，勿据此判断现状）

共 79 个文件 + `prompt-pack-2026-11-02/`（外包交付的提示词包）。它们记录的是
2026-10-24 ~ 2026-11-09 期间的审计/实验/计划/外包交付，**结论多数已被上面的当前文档吸收或推翻**。
典型类别：

- `audit-*`（10-25 ~ 11-03 各轮审计）：滚动修复记录，状态以 `audit-qa-2026-11-11.md` 为准；
- `plan-*` / `*-plan.md`（v1/p3/cluster-a/concurrency/toolbox 等）：多数已实施，实施结果看 HANDOFF；
- `*-report.md` / `bench-*` / `eval-*`：历史实测数据（v6 及更早镜像），当前数据见能力评估报告；
- `authoring-*` / `outsourcing-*` / `why-authoring.md` / `b1-adapter-plan.md` / `prompt-pack-2026-11-02/`：
  出题与外包借力线，当前**挂起**（用户指令：先把非正式赛的工程做扎实）；
- `CTFARENA_*` / `bigbank-*` / `report-large-scale-test.md`：平台批量实测证据；
- `pending-fixes-2026-11-09.md`：清单已全部关闭（文首有 2026-11-12 收口说明）。

> 归档不等于删除：`git log --follow docs/archive/<name>` 可追溯；需要复活某份文档时
> `git mv docs/archive/<name> docs/<name>` 并更新引用即可。