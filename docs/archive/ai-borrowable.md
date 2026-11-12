# 外部借鉴调研：同类赛道的冠军方案与学术界 CTF agent（2025-11）

> 目的：为 TPCTF 2026 Agent Track 找「已被验证有效的 agent 工程范式」，判断哪些能落进
> 现有 `ctfy_agent`（stdlib-only、单镜像、crew 架构）。

## 0. 调研边界（先说清楚可信度）

- `web_search` 不可用（未配 API key）；DuckDuckGo / raw.githubusercontent 直连被拒。
- 实际可达：**GitHub REST API**（搜索/内容）、**arXiv API**、**厂商博客直链**（theori.io）。
- 因此本调研以「AIxCC 决赛队伍公开方案 + 顶会/预印本论文 + 开源仓库 README/架构文档」为主，
  未做社交媒体/论坛扫读。TPCTF 自身没有公开的往届 agent 赛道冠军方案（该赛道 2026 才开）。

## 1. 来源清单

| 来源 | 地位 | 许可 | 价值 |
|---|---|---|---|
| [Theori《Building Effective LLM Agents》](https://theori.io/blog/building-effective-llm-agents-63446) | DARPA AIxCC **决赛第 3 名**（RoboDuck）的 agent 设计复盘 | 博客（文字可引用） | ★★★★★ 全是第一手工程经验 |
| [Theori RoboDuck 架构](https://theori.io/blog/aixcc-and-roboduck-63447) + [crs-architecture.md](https://github.com/theori-io/aixcc-afc-archive/blob/main/docs/crs-architecture.md) | 同上，pipeline 级架构 | 仓库 AGPL-3.0 | ★★★★ 流程编排/去重/打分 |
| [Team-Atlanta/aixcc-afc-atlantis](https://github.com/Team-Atlanta/aixcc-afc-atlantis) | AIxCC **冠军** CRS | MIT（README 声明） | ★★★ 可读实现 |
| [fuzzingbrain（第 4 名）](https://github.com/fuzzingbrain/afc-crs-all-you-need-is-a-fuzzing-brain) + [论文 2509.07225](https://arxiv.org/abs/2509.07225) | AIxCC 决赛第 4 名 | **Apache-2.0** | ★★★★ 「发现必须动态验证」+ 任务级 budget |
| [EnIGMA（arXiv 2409.16165）](https://arxiv.org/abs/2409.16165) | CTF agent SOTA（NYU CTF / InterCode / CyBench 三项 SOTA） | 代码 MIT（SWE-agent v0.7） | ★★★★★ 交互式工具 + 幻觉观察 |
| [Cybench（arXiv 2408.08926）](https://arxiv.org/abs/2408.08926) | CTF 评测框架，40 题 4 赛区 | 代码开源 | ★★★ 子任务分解式评测 |
| [NYU CTF Bench（arXiv 2406.05590）](https://arxiv.org/abs/2406.05590) | 200+ 题 CTF 基准 | 开源 | ★★★ function-calling 工作流 |
| [HPTSA（arXiv 2406.01637）](https://arxiv.org/abs/2406.01637) | 规划者 + 子代理打真实 0day，提升 4.3× | 论文 | ★★★★ 「规划者启动子代理」的证据 |

## 2. 可借机制逐条对照

### 2.1 卡死干预（intervention）——最高优先
- **出处**：Theori 明确写：「若能客观衡量进度，检测到 agent 卡住就注入一条让它重新思考的干预消息」；
  举例：PoV 连续失败后，要求它「给出 3 个假设，逐个用 debugger 子代理验证」；某些模型（o4-mini）
  拿到信息却不肯终止 → 上下文超长时**无条件注入「请收口」**。
- **我方现状**：039 回归的根因正是这个——连续 8 次同一个 `GET /playground?message=`，账本只回
  「已试过」，模型照旧重发，烧到 260k 上限。
- **落地**：solver 里做确定性检测（同 `(tool, args)` 连续 ≥2 次 / 同端点 3 次无状态变化）→ 注入
  「必须换方法（GET↔POST）、换载荷；下面附本次响应尾部原文」；步数过阈值且无候选 → 注入「现在必须
  report 现状或提交候选」。
- **成本**：纯 Python，约 40 行 + 3 条用例；无额外 LLM 调用。

### 2.2 精修工具集（curate the toolset）——高优先
- **出处**：Theori 说这是「开发中最耗时的部分」：通用 `bash` 能到目标但死路多、上下文污染、多步低效；
  于是提供 `read_definition` / `find_references` / `read_source(±50 行)` 等窄工具。
  EnIGMA 同结论：专用交互式工具（debugger、server connection）让性能显著提升。
- **我方现状**：`http_request`（含 grep/offset）、`chat`、`jwt_forge` 已是窄工具；但**没有表单级工具**，
  于是模型在 039 上把 GET 当 POST 用、在 038 上连抓 6 次表单页抄字段。
- **落地**：新增 `http_form`：GET 表单页 → 解析 `<input/textarea/select>` 字段名与 action/method →
  按给定值 POST（未给的用默认值）→ 自动跟随 302/cookie → 返回 status + 关键区域。
  一次调用替代「读表单 + 手写编码 + POST」3–6 步。
- **成本**：约 120 行（stdlib urllib + html.parser）+ 4 条用例。

### 2.3 结构化输出 +「强制思考字段」
- **出处**：Theori 两种做法：XML 标签（模糊解析 + 校验错误回喂）或 terminate 工具；并强调
  「故意放几个不打算用的字段，能逼 agent 想清楚」（例：`conditions` 字段用于减少误报）。
- **我方现状**：report 只有 text + evidence（+ 可选 answer），证据契约已很严格，但缺「可复现性」字段。
- **落地**：report 增 `repro`（在哪个请求/第几回合复现）与 `why`（为什么这串就是要的值）两个短字段，
  只做校验提示、不阻断；目的为逼模型自检，降低「拿个像 flag 的串就报」。
- **成本**：约 20 行；注意别引入 `evidence`/`report` 等保留字面（提示词契约）。

### 2.4 按模型适配（rules / tool_choice / 收口）
- **出处**：Theori：①常见错误 → 直接加规则，重要规则用 `<important>` 强调；②能客观衡量进度就干预；
  ③部分模型不会稳定调工具 → `tool_choice=required` + terminate 工具，否则小模型会「凭空捏造工具调用与结果」。
- **我方现状**：提示词已有规则块；但「模型只输出文本不调工具」「上下文过长仍不收口」没有硬约束。
- **落地**：solver 检测「连续 2 次无工具调用的纯文本输出」→ 注入「必须调用工具（或用 report 收口）」；
  上下文超 80% 预算且无候选 → 注入收口提示。等价于 `tool_choice=required` 的软实现（不依赖 provider 能力）。
- **成本**：约 25 行。

### 2.5 验证后再提交（verified-only output）
- **出处**：fuzzingbrain 的硬规则——「未让真实构建崩溃的输入一律不报」；EnIGMA 的
  *soliloquizing*（模型自造观察）现象说明「未经环境确认的结论」是系统性风险。
- **我方现状**：已有证据契约（evidence 必须是工具输出逐字子串），但**提交前不复验**。
- **落地**：协调者 submit 前，若候选 flag 来自被压缩过的页面 → 用 `http_request` 复取一次确认串仍在
  （1 次调用，约 2–4k token）。可选，默认开。
- **成本**：约 20 行。

### 2.6 打分筛选（scoring / triage）
- **出处**：RoboDuck 有专门「vulnerability scoring」组件：单次 LLM 分类给候选打分，决定是否投入后续
  patch/PoV；fuzzingbrain 用 Suspicious-Point 分区 + 打分。
- **我方判断**：**不建议照搬**。每加一次 LLM 调用就多 3–8k token，而我们的候选空间小（一题 1~3 个候选），
  用确定性规则（"出现 FLAG{...} 且 status 200" → 直接提交）已经够。

### 2.7 子代理当工具 / 规划者-子代理
- **出处**：Theori「重复子任务暴露成工具，主 agent 只看输入输出，天然压缩上下文」；HPTSA「规划者启动
  子代理」在真实 0day 上提升 4.3×。
- **我方现状**：crew 已有 coordinator + 专家分工（等价 HPTSA 的两层），**缺**「专家内部再开窄子 agent」。
- **判断**：框架改动大（tools 层不支持嵌套 LLM 调用），且与我们「单镜像 stdlib」约束冲突小但收益不确定；
  **列为可选**，等 2.1–2.4 验证完再评估。

### 2.8 子任务分解式评测
- **出处**：Cybench 给每题写 subtask 并分别评分；NYU CTF Bench 提供 function-calling 自动化工作流。
- **我方现状**：`tests/fixtures/ai/` 有 4 个离线夹具（单测级），没有「阶段级」评测。
- **落地**：给 4 个夹具加 stage 断言（侦察→注入→取证各阶段是否达成），用于回归时定位「哪一步退化」。
- **成本**：约 60 行测试，不影响运行时。

## 3. 不采纳及原因

| 方案 | 原因 |
|---|---|
| RoboDuck 的 fuzzing/静态分析管线 | 与「AI 安全题型（web/AI 靶场）」不匹配，且引入大量非 stdlib 依赖 |
| Atlantis / Buttercup 代码 | AGPL-3.0（Buttercup）与体积/依赖约束；且它们是 CRS 不是 CTF 解题 agent |
| 单次 LLM 打分筛选 | 每候选多一次调用，成本不划算（见 2.6） |
| 完整子 agent 嵌套 | 框架改动大，收益未验证（见 2.7），留待后续 |

## 4. 推荐落地顺序与预算

| 序号 | 内容 | 代码量 | 验证成本 |
|---|---|---|---|
| ① | 2.1 卡死干预 + 2.4 收口/强制工具 | ~65 行 + 用例 | 039 复测 1 次（约 150–260k） |
| ② | 2.2 `http_form` 窄工具 | ~120 行 + 用例 | 随 039 一起验证 |
| ③ | 2.3 report 结构化字段 + 2.5 提交前复验 | ~40 行 | 随 039 一起验证 |
| ④ | 2.8 夹具阶段断言 | ~60 行测试 | 0（离线） |
| ⑤ | 2.7 子 agent 嵌套 | 评估后定 | — |

预期收益（基于 039 失败 run 的动作分布）：把「8 次重复 GET + 6 次表单抓取」这两类浪费挤掉，
单题循环浪费可从 ~60% 降到 ~10%，即 039 若解出应在 100–140k 而不是 266k。

## 5. 与既有 M5.5 的关系

M5.5 解决的是「**上下文太贵**」（per-call prompt 9.6k→7.5k，038 135k→104k）；
本轮要解决的是「**动作序列浪费**」（打转、重复、GET/POST 混淆）——两者正交，
叠加后才有望把失败 run 的 260k 天花板压到 150k 以下。
