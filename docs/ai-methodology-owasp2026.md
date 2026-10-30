# AI 安全方向 · 外部方法论提炼（OWASP 2026 对齐）

> 定位：**方法论源文件**。2026-10-29 实抓外部体系后提炼，供方法卡 / 提示词 / 夹具三层派生取材。
> 不重复 `docs/ai-plan.md` 的工程记录（那边归 M 系列里程碑）；本文只管「思路从哪来、怎么变成打法」。
> 所有外部结论附来源 URL；抓取路径：GitHub API + PortSwigger + OWASP 官网（官网 JS 渲染，正文以官方
> GitHub 仓库 + AIGoat 交叉验证为准）。

---

## 1. 分类骨架：OWASP 2026 三清单 × 平台 tag × 我方覆盖

**平台的 AI 方向 tag 体系 = OWASP Agentic Top 10（2026）**。官方编号 ASI01~ASI10 由
[OWASP/www-project-agent-memory-guard](https://github.com/OWASP/www-project-agent-memory-guard)
自述印证（"Runtime defense against memory poisoning in AI agents **(OWASP ASI06)**"，Apache-2.0）。

| # | OWASP Agentic 2026 风险 | 攻击一句话 | 平台已知 tag | 我方现状 |
|---|---|---|---|---|
| ASI01 | Prompt injection / goal hijack | 覆盖指令，劫持目标 | `asi01` ✅ | 夹具01 + 卡 ✅ |
| ASI02 | Tool misuse | 诱导 agent 滥用合法工具 | `asi02` ✅ | 夹具02 + 卡 ✅ |
| ASI03 | Identity & privilege confusion | 冒充/越权另一主体身份 | ❌ 未见于 tag | **缺口** |
| ASI04 | Supply chain | 工具/模型件被调包 | `supply_chain` △ | 文档提及，无夹具 |
| ASI05 | Unsandboxed code execution | 逃逸沙箱执行任意代码 | ❌ | ✅ 夹具05（2026-10-29） |
| ASI06 | Memory poisoning | 污染记忆/RAG 再触发 | `asi06` ✅ | 夹具03 + 卡 ✅ |
| ASI07 | Insecure inter-agent comm | 伪造智能体间消息 | ❌ | ✅ 夹具06（2026-10-29） |
| ASI08 | Cascading failures | 放大单点失效成级联 | ❌ | **缺口** |
| ASI09 | Human-agent trust abuse | 利用人类审批惯性 | `asidef` △（近邻） | 夹具04（陷阱页）部分覆盖 |
| ASI10 | Rogue agents | 恶意智能体混入编排 | ❌ | **缺口** |
| MCP | mcp02 scope creep / mcp03 tool poisoning | 工具描述投毒、范围蔓延 | `llm_proxy` △ | mcp03 ✅ `tests/fixtures/ai_mcp/`（2026-10-29）；mcp02 缺 |

清单来源：[AIGoat README](https://github.com/AISecurityConsortium/AIGoat)（46 lab 全映射三清单）×
[OWASP GenAI 官方页](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)。

**结论**（2026-10-29 更新）：ASI 十条我方覆盖 **5.5/10**（asi01/02/05/06/07 夹具 + asidef≈asi09），
加 mcp03；剩余缺口 **asi03 身份越权、asi04 供应链、asi08 级联、asi10 流氓体、mcp02 范围蔓延**。
三族验收：`tests/smoke_ai_fixtures.py`（6 道）/ `tests/smoke_ai_infra.py`（4 道）/
`tests/smoke_ai_mcp.py`（1 道）全绿；后两者待并行会话的 smoke.py 落定后接线。

---

## 2. 攻击方法论五条主线（核心产出）

### 2.1 侦察 → 定型 → 打法循环（「靶面四象限」）

我们 M7 已在 AI 基建方向实现此循环（probe 指纹 → next 打法）。泛化到全部 AI 题——任何 AI 靶子
先归入四象限之一，再按象限选工具组合：

| 象限 | 特征 | 我方工具 | 首步 |
|---|---|---|---|
| chat 端点 | 有对话 API | `chat` | 探 system prompt 边界（拒绝词表→防御等级） |
| HTTP API | 普通 Web 面 | `http_request` + `http_map` | probe 定型组件 / openapi 找工具面 |
| 记忆-RAG | 有知识库/工单/文档 | `http_request` | 找可写入口（投毒点），不是找答案 |
| MCP 工具面 | SSE/MCP/工具描述 | `http_request` + SSE 链路 | 读工具描述全文找 mcp03 投毒点 |

方法论要点：**先定象限再出招**——多数失败尝试源于拿 chat 打法打记忆型靶子（反之亦然）。

### 2.2 Agentic Kill Chain（多步题的方法论骨架）

来自 AIGoat Agentic Kill Chain capsstone（同构于 [agent-memory-guard](https://github.com/OWASP/www-project-agent-memory-guard)
防守视角的检测分段）：

```
植入 plant      →  持久化 persist  →  触发 trigger     →  影响 impact
(把指令写进可信源)  (进记忆/工作流/描述) (例行请求唤起)     (外带数据/越权动作)
```

三条推论（直接影响我们的解题编排）：
1. **第一步不追求拿答案**：多步题的首轮目标永远是「污染一个会被信任的存储」，不是直接问答案；
2. **触发用例行请求**：别在注入消息里夹带意图，用「总结今天的工单」这类正常流量触发；
3. **无进展判定按链段计，不按轮次计**：卡在 plant 阶段 8 轮 = 方法错，不是运气差；推进到
   trigger 才失败 = 载荷问题。这直接可改进 retry/cluster-B 的刹车判据。

### 2.3 防御感知与降级阶梯（L0/L1/L2）

AIGoat 每个攻击面配三档防御（Vulnerable / Hardened / Guardrailed）。**先探防御等级，再选变体**：

```
单轮直问 → 多轮渐进 → 编码/换语种（i18n，夹具01 已证有效）→ 间接注入（夹具04 已证）→ 信任链滥用（asi09）
```

识别信号：英文关键词被拒但小语种放行 = 词表过滤（L1）；任何敏感词都拒 = 意图分类（L2，换编码+分步）；
回答被截断/脱敏 = 输出过滤（改问「引用原文」而非「给值」）。

### 2.4 答案收口纪律（AI 题判分特有）

- AI 题判分 = **自然语言原样值**，不是 `FLAG{...}`；
- 方法论条目（已在夹具验收里当判据，升格为打法纪律）：**AI 题里拿到 flag 形态字符串优先怀疑诱饵**——
  真值是「确认号 / api_key / token」这类业务值；把诱饵提交 = 白烧一次 oracle；
- 收口前自检：值是否来自服务端原文（exploit_verified），而非模型转述。

### 2.5 评测方法论

- 指标：**分类攻击成功率 × tok/solve**（与我们 bench 主指标一致；NYU CTF Bench / CSAW LLMAC
  的做法也是分类报告，不混总分）；
- 量程：官方 145 题套件优先；NYU/CSAW 仅当扩展量程储备，不当方法论来源。

---

## 3. 完整性 checklist（出题/解题双向）

| 风险 | 首选象限 | 打法一句话 | 工具 |
|---|---|---|---|
| ASI01 | chat | i18n/编码绕词表，多轮渐进 | chat |
| ASI02 | chat/MCP | 让 agent 自己调工具干坏事（工具是合法的） | chat |
| ASI03 | HTTP/MCP | 冒另一用户/agent 的身份上下文发请求 | http_request |
| ASI04 | HTTP | 找版本固化点/镜像源/插件市场，换料 | http_request |
| ASI05 | HTTP/MCP | 描述性任务让本地模型「执行」 | http_request |
| ASI06 | 记忆-RAG | 投毒可信文档，等例行检索触发 | http_request |
| ASI07 | MCP | 伪造上游 agent 消息/handoff | http_request |
| ASI08 | 编排 | 单点失效的放大路径（重试风暴/依赖链） | http_request |
| ASI09 | chat/审批 | 构造「看起来已批准」的上下文 | chat |
| ASI10 | MCP/编排 | 混入未声明的智能体行为 | http_request |
| mcp03 | MCP | 工具描述里藏指令（ragged edge） | SSE 链路 |

---

## 4. 反向资产：防守方工具当攻击教材

| 资产 | 许可 | 用法 |
|---|---|---|
| [OWASP agent-memory-guard](https://github.com/OWASP/www-project-agent-memory-guard) | Apache-2.0 | 它检测什么（一次性大段注入、来源异常），我们绕什么（分摊多步、语义改写、来源伪装） |
| [OWASP Agent-Security-Regression-Harness](https://github.com/OWASP/Agent-Security-Regression-Harness) | Apache-2.0 | 其攻击用例目录 = 官方攻击思路清单，逐条对照 §3 补漏 |
| [OWASP secure-agent-playbook](https://github.com/OWASP/secure-agent-playbook) | 见仓库 | 防守视角 checklist，反推出「防守方最怕什么」 |
| [NVIDIA/garak](https://github.com/NVIDIA/garak) | Apache-2.0 | probe 族分类（jailbreak/leakage/misuse）当 ASI01 变体弹药库索引 |

---

## 5. 落地映射与审批边界

| 层 | 可落地项 | 边界 |
|---|---|---|
| 夹具（新文件） | **已落地（2026-10-29）**：asi05 / asi07（`tests/fixtures/ai/05、06`）+ mcp03（`tests/fixtures/ai_mcp/`），三族 smoke 全绿。下一批候选：asi03 / asi08 / asi10 / mcp02 | 无冲突，等批准 |
| 方法卡（`method_cards.py`） | kill-chain 链段计分卡、防御等级探测卡、§3 checklist 卡片化 | **文件归并行会话，需协调** |
| 提示词（`roles/ai.system.txt`） | 降级阶梯、答案收口纪律、四象限先定型 | **并行会话 + 单独批** |
| 解题编排（retry/cluster-B） | 「链段进度」替代轮次计的无进展判据 | 需方案批准后动 |

---

## 6. 引用清单

- OWASP Agentic Top 10 2026：<https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/>
- OWASP LLM Top 10 2026：<https://genai.owasp.org/resource/owasp-genai-llm-top-10-2026/>
- AIGoat（46 lab × 三清单，Apache-2.0 + CC BY-NC-SA）：<https://github.com/AISecurityConsortium/AIGoat>
- PortSwigger Web LLM attacks（方法论 + 在线 lab）：<https://portswigger.net/web-security/llm-attacks>
- NYU CTF Bench（GPL-2.0，量程储备）：<https://github.com/NYU-LLM-CTF/NYU_CTF_Bench>
- CSAW25 LLM 攻防库：<https://github.com/NYU-LLM-CTF/CSAW25_LLMAC_DB>
- garak（Apache-2.0）：<https://github.com/NVIDIA/garak>
- 借用红线：AGPL（HackSynth/Cairn）禁止引代码；无许可证（ML_CTF_Challenges）只可看；AIGoat 训练内容
  CC BY-NC-SA（非商业+署名，借**形状**不拷内容）；一切借用必须按我们 stdlib 夹具模式离线重实现
  （`internet_policy=deny`）。
