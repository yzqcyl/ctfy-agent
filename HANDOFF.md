# 交接文档 — TPCTF 2026 智能体赛道

> 给新对话的 agent：先读完 **§0** 再动手。§0 是当前真实状态，其余章节是仍然有效的背景与禁忌。
> 所有结论都有实测依据；**不要重新推导已验证过的东西**（会浪费 token 和实例配额）。
> 只想粘一段话开新对话 → 用 `docs/OPENING.txt`（本文件的自包含浓缩版）。

---

## 0. 当前状态（2026-10-27）

| 项 | 状态 |
|---|---|
| **交付物（容器镜像）** | ✅ **v2 已完成并推送**：`ghcr.io/yzqcyl/ctfy-agent-pub:v2`，digest `sha256:73b39430873a1bf3d213e2881722a7aa69d78d1a24932790e932043306cf09e5`（provenance 已核验）。v1 冻结在 `sha256:a68ebe6e619b95191cc5168c9e35f2e92788970cfca823093ba8d1100d66ec4b`，仅留档。**`:latest` 现在也指向 v2 的 digest，提交时务必显式填 `:v2`。** |
| **平台审核 / 提交** | ⬜ **未开始**。只能由用户走浏览器 4 步，步骤见 `docs/submit-image-runbook.md`。这是当前唯一卡在"人"身上的事。 |
| **大规模基准测试** | ✅ 完成。报告：**`runs/bench-web-report.md`**（16 道归档 + 3 道中断 + A/B 对照 + 并行求解器评估，含勘误记录）。 |
| **模型 A/B 对照** | ✅ 结论：**换更强模型不涨分**（同题 Pro 0/3 vs K2.7 0/3，速度 2.27×，覆盖面反而更窄）。 |
| **并行求解器可行性** | ✅ 已评估（报告 §12）：**模型路线已被证伪，多策略并行未被测量**，且必须先解决"预算 ×N"与"环境失败乘 N"两个硬前提。**建议先不做。** |
| **800k 复测（arm C）** | ✅ SAMPLE-009 @800k 三臂（干净 Crew / solo / B1+闸门+收敛）全部 **0 候选 0 提交 budget_exhausted** → 编排/模型/调参均非瓶颈；**瓶颈=flag 发现（access-control 面）**。 |
| **代码版本** | HEAD `f4e1d3b`（v3）。`462e1bf`=B1 无进展刹车+http_map 接线（M5.6）；`f4e1d3b`=候选闸门+收敛提示（安全网）。v2 镜像同源 `1aeee61` 不变。 |
| **实例** | 0 个（已清空）。 |
| **单测基线** | `python tests/smoke.py`：**v2 83/83**；**当前工作区 88/88 全绿**（新增 `no_progress_brake`、`http_map`、`http_map_wiring`、`smoke_gate`）。 |

**一句话**：v2 镜像待用户提交；v3 代码已收口（B1+安全网）并 smoke 88/88，但**三臂实测 + SAMPLE-009 手动探测证明失败根因是 flag 发现（access-control 面），非编排/调参可解**；v3 镜像重建阻塞于本机无 Docker。

---

## 1. 任务与契约要点

- 目标：TPCTF 2026 **智能体团队赛道**的提交物 = **一个容器镜像**（不是本地 CLI）。纯 stdlib Python，模型走 DeepSeek/Paratera 的 OpenAI 兼容接口。
- 入口：`python -m ctfy_agent agent`；读 `/ctfy/work/task.json`（CTFY-0001 schema v2）→ 写 `/ctfy/work/result.json` → `exit 0`。
- **stdout 对用户镜像不读**（平台自有镜像才有 legacy 兜底）→ 日志一律 stderr。
- 解题在**运行期**通过 MCP `submit_answer` 完成，不是赛后判分。
- 合规赛道：**Web / Pwn / AI 安全**；当前全部实测都在 Web。
- 工作目录：`E:\ctfy_project`。

---

## 2. 仓库地图（关键锚点）

| 文件 | 作用 | 关键位置 |
|---|---|---|
| `src/ctfy_agent/agent_main.py` | **B1 适配层**（镜像入口） | 读 task.json → env 组 Config → dry_run 分支 → 非 instance 拒跑 → Crew（**关 stop_instance**）→ 原子写 rollup；启动即写 provisional result.json |
| `src/ctfy_agent/crew.py` | 多智能体：协调者 + web/rev/crypto/misc/ai 专家 | `_brief`、`_prior_digest`、`_delegate_task`、`COORDINATOR_PROMPT`/`_TOOLS`、`_delegate`、`_role_system`、`solve` |
| `src/ctfy_agent/solver.py` | 专家主循环、工具分派、上下文滑窗 | `_ledger_gate`/`_ledger_after`（去重闸）、`_check_report`（证据契约）、`_execute`、`solve`、`run_role`（专家侧注入）、`_role_context`、**无进展刹车（`_NO_PROGRESS_*`）** |
| `src/ctfy_agent/ledger.py` | 确定性账本 + 证据校验 | `normalize_args`、`attempt_key`、`looks_failed`、`claims_discovery`、`Ledger`、`verify_evidence`、`digest` |
| `src/ctfy_agent/companion.py` | PLAN/REFLECTION 有状态伴生文本 | `parse_companion`、`PlanTrack`（推进信号只认账本新增尝试） |
| `src/ctfy_agent/ctxcompress.py` | 工具结果瘦身 + 关键事实常驻 + 两级滑窗 | v2 新增 |
| `src/ctfy_agent/tools_local.py` | 本地工具 http_request/shell/note/report + `jwt_forge`/`http_burst`/`http_map` | `http_map` = crawl 站点成图（链接/表单/字段/接口线索）+ probe 常见端点 |
| `src/ctfy_agent/tools_misc_ext.py` | misc 外包：decode_blob / unpack_archive / hexpeek | 纯 stdlib |
| `src/ctfy_agent/platform.py` | MCP 客户端 | 97 个工具暴露；`MODEL_TOOLS` = 单 agent 真白名单 |
| `src/ctfy_agent/llm.py` | OpenAI 兼容客户端（urllib，非流式） | 只有 `/chat/completions`，**不认 anthropic flavor** |
| `config/agent.json` | 角色/模型/预算 | coordinator+web=Kimi-K2.7、rev=Kimi-K3、ai=Kimi-K3、crypto=DeepSeek-V4-Pro-0813、misc=GLM-Z1-AirX；`max_tokens_per_challenge=800000`、`max_llm_calls=80`。**容器里角色 model 会被 `agent_main._build_config` 覆盖为 `LLM_MODEL`** |
| `tests/smoke.py` | 离线回归（总入口，跑完打印 `N passed, N failed`） | **v2 提交版 83/83**；当前工作区 **86/86**（新增 `http_map`、`http_map_wiring`、`no_progress_brake`）。改动后必须保持全绿 |
| `tests/smoke_context.py` | 上下文压缩/事实/滑窗/证据逐字性 12 组断言 | 并行会话有改动 |
| `tests/smoke_http_session.py` | 302 上 Set-Cookie 入 jar 回归 | — |
| `tests/smoke_webmap.py` | `http_map` 站点成图/probe/白名单 + 角色挂载接线 | 专抓「工具存在但没下发给模型」这类漏接线 |
| `tests/smoke_brake.py` | 无进展刹车：输出重复 3 步提示、5 步止损；内容有变化不误伤 | 039/043 退化循环的回归 |
| `src/ctfy_agent/method_cards.py` | 题型方法卡 | web 50 题词表 + AI 17 张 + 80 个 AI tag |
| `roles/` + `tests/fixtures/` | 角色提示词与离线夹具 | `roles/misc.json` **不会被框架读**，必须并入 config |
| `docs/submit-image-runbook.md` | **提交镜像的操作手册**（digest、4 步、快照命令） | 提交前必读 |
| `docs/b1-adapter-plan.md` | B1 方案 + 平台源码硬事实 + 容器内 G1–G8 | — |
| `docs/report-ctfy-rules.md` | 官网规则调研；标准全文在 `runs/site/standards/` | — |
| `.tools/ctfy-src/` | **平台源码**（契约真源，别去网上找文档） | 见 §5 |
| `PROGRESS.md` | 耐久 checkpoint（历史细节都在这里） | §§3.1–3.13 |
| `runs/bench-web-report.md` | **本轮大规模实测报告（最新事实）** | 见 §4 |

---

## 3. 已完成的能力线（浓缩，细节见 PROGRESS）

- **框架**：MCP 握手、多智能体、预算三层刹车、实例生命周期、转录、单测。
- **A0 题型信息送达**：`tags/difficulty/solves_count` 入库并送达三处提示；`list_hints/view_hint` 放行。M1 真机验证。
- **A1 确定性 Ledger**：归一化 args 去重，**crew 跨 delegate 共享**；真实转录回放拦 18 次重复（≈15.5 万 prompt token）。
- **A2 输出契约 + 逐字证据**：`report` 必填 `evidence`（工具输出逐字子串）；`PLAN:`/`REFLECTION:` 由 `PlanTrack` 有状态消费；`delegate` 改 `role+objective+done_when`。
- **全量代码审计**：5 个缺陷已修（详见 `docs/audit-2026-10-25.md`）。
- **misc 外包全流程**：3 个工具 + 提示词 + 夹具，离线验收（`docs/review-misc-module.md`）。
- **B1 适配层 + v2 镜像**：见 §0 / §5。
- **v2 新增工具**：`jwt_forge`（none/HS256-512/RS256，secret 只进账本指纹）、`http_burst`、`http_request` P0 修复、`ctxcompress`。

---

## 4. 实测基准（2026-10-26 晚，**本轮核心，勿重推**）

完整报告：`runs/bench-web-report.md`；原始数据：`runs/bench-*/`、`runs/ab-pro-*/`。

### 4.1 口径（先看这个，否则数字会误读）

- 单题预算 **150,000 token**（环境变量 `AGENT_MAX_TOKENS_PER_CHALLENGE=150000`），**生产默认是 800,000**——这是理解全部结果的前提。
- 模型 = `config/agent.json` 的角色模型（Kimi-K2.7 / K3 / Pro-0813），全部 `category=web`。
- 代码 = 提交 `1aeee61`（v2 同源），**不含**并行会话的未提交改动。

### 4.2 总体

| 指标 | 数值 |
|---|---|
| 归档 | **16 道**（另 3 道中断） |
| 解出 | **5（31.2%）**；easy 5/9=56%、medium 0/4、hard 0/3 |
| 平均耗时 / 消耗 | **290.4 s** / **99,313 token** 每题 |
| 吞吐 | **12.4 题/小时**（串行，含实例启停） |
| token 合计 | K2.7 臂 **2,130,710** + Pro 臂 **437,299** = **2,568,009** |
| **失败题 attempts** | **全部为 0**——没有一道是"提交了但 flag 错"，失败都在"没产出候选 flag" |

### 4.3 卡点（这是报告最值钱的部分）

| 卡点 | 次数 | 说明 |
|---|---|---|
| **预算耗尽** | 6 | medium/hard 平均 159k，正好撞 150k 上限 |
| **协调者空转** | 2 | 只写 PLAN 不发工具调用 → 被判完成，1 步归零（非截断） |
| **实例起不来** | 2 | 上游 `Rate limited by upstream API`，且**失败后不硬停**，空转烧完整轮 |
| 协调者自认完成但未提交 | 1 | — |

工具层摩擦：`shell` 报错 **67%**（Windows 不认 POSIX 写法）、`start_instance` **61%**、`http_burst` **25%**、`report` 被拒 **13 次**（证据必须逐字子串）、账本拦截 **23 次**（K2.7 19 + Pro 4）。

**环境级阻塞**：**CTFARENA 全库 50 道 web 全部起不来**（同一个上游限流），整库缺席；SAMPLE/CYBENCH/AGENTBENCH/AGENT-DEFENSE 正常。

**无效样本**：SAMPLE-006 复用了已死实例（23 次 404、0 次 2xx，156k token 纯浪费）。剔除后 **5/15 = 33.3%**。

### 4.4 模型 A/B（回答"是不是 K2.7 能力不足"）

同代码、同配置、同预算，只把 6 个角色换成 `DeepSeek-V4-Pro-0813`，跑同 3 道 K2.7 失败题：

| 题 | K2.7 | Pro | 速度比 |
|---|---|---|---|
| SAMPLE-007 | ✗ 335 s | ✗ 849 s | 2.53× |
| SAMPLE-009 | ✗ 464 s | ✗ 1,196 s | 2.58× |
| AGENTBENCH-002 | ✗ 352 s | ✗ 597 s（LLM 504） | 1.70× |

**结论：0/3 vs 0/3，慢 2.27×，覆盖面更窄**（SAMPLE-007：K2.7 探 38 条路径，Pro 仅 10 条，其中 8 条被 K2.7 包含，Pro 独有 2 条）。→ **瓶颈在预算与机制，不在模型 IQ。换模型不解决问题。**

### 4.5 并行求解器（多 LLM 同题并行）评估结论

- **"多 LLM（换模型）"已被 A/B 证伪**；
- **"多策略并行"没有被直接测量过**，唯一硬支撑是"失败主要是策略塌缩"（同一 POST 原样重发 5 次全被账本拦、反复换措辞重派同一思路）；
- 两个硬前提：①预算必须 **×N**（否则等于拆预算，必亏）；②环境失败（实例限流、旧实例、504）对 N 路**同时生效**，只是把浪费乘以 N；
- 实现上**不是改配置能解决的**：`Crew` 是单线程顺序编排，需要改编排层 + 预算/账本并发安全；建议先做 **2 路 × 4–6 道 medium** 的小实验；
- 并行会话正在加的**串行换路线干预**（`STRATEGY_SIMILARITY_LIMIT`）能以 1× 成本吃到大部分多样性收益，**它上线后并行的边际收益更小**。

### 4.6 数据可信度（坑）

- 150k 口径使 medium/hard 偏悲观；CTFARENA 缺席；A/B 样本仅 3 道。
- **统计时警惕重复计数**：拦截标记串会被账本重渲染与结论回显重复计入（"35 次账本拦截"就是这么来的，精确值是 23）。验尸一律按"结果行"计数，不按文本出现次数。
- 本次审核已修正 3 处初版数字（报告 §11 有记录）：账本拦截 35→23、"Pro 找到 K2.7 漏掉的端点"→ 不成立、"{v} 落 host 段两人都撞过"→ 仅 Pro。

---

## 5. 平台契约（从 `.tools/ctfy-src` 逐字读出，硬点）

- **result.json**：写 `$CTFY_WORK_DIR/result.json` 后 `exit 0`（非 0 一律失败）。核心字段 `answers`（按 question id，平台代交）；`solved` 由平台按提交记录重算，写它不加分；其余 `turns/tool_calls_made/input_tokens/output_tokens/stop_reason/writeup` 可选。dry run 写 `{"schema_version":2,"solved":[],"fully_solved":false,"stop_reason":"dry_run"}`。
- **task.json**（schema_version=2）：`run_mode` ∈ instance|qa|defence|engagement|awd；含 `challenge/competition_id/questions/gated_questions/scope/rules_of_engagement/deliverable/platform_url/max_turns/result{path,stdout_fallback}/target_instance`。
- **环境变量**：`CTFY_PLATFORM_URL`、`CTFY_TOKEN`、`CTFY_EVAL_CHALLENGE_ID`、`CTFY_EVAL_COMPETITION_ID`、`CTFY_EVAL_MAX_TURNS`（缺失=无预算）、`CTFY_WORK_DIR`、`CTFY_EVAL_DRY_RUN`。**`CTFY_MCP_URL`/`CTFY_TARGET_URL` 在用户镜像里不存在**——MCP 地址从 `$CTFY_WORK_DIR/mcp.json` 或 `CTFY_PLATFORM_URL + /api/v1/mcp/` 拼。
- **LLM 网关**：`LLM_FLAVOR`(openai|anthropic) + `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`。**`LLM_API_KEY` 为空必须大声失败，禁止回退自有 key**。
- **赛事硬约束**（实测）：`internet_policy=deny`（容器无外网）、`max_instances_per_team_effective=1`、3 段 session（11/15、11/21、11/22 各 10:00–20:00 CST）、同分先解出者靠前。
- 竞赛 id：`TPCTF`=`c0d8b4e3dd3b42c095d63d7dfa66431f`；练习库：CTFARENA=`4394a10aa5d24f97a1ec3301ca718a45`、CYBENCH=`d35cc057773c44f79eec192b54551327`、AGENTBENCH=`9f613f261b68469b9c4da805197983b2`、AGENT-DEFENSE=`d4917609895644e1bcf92b3c9b3bc70f`、SAMPLE=`e4e78ff318dc4844beac13f9ea7409aa`。

---

## 6. 待办与阻塞

1. **等平台审核 + 提交 v2**（用户）：`docs/submit-image-runbook.md`；提交时显式 `:v2`，别用 `latest`。
2. **v3 镜像重建/push 阻塞于本机无 Docker**。`container_check.py --local` 静态验收已 5/5 绿；装 Docker 后 `python tools/container_check.py` 构建+验收，通过再 push `:v3`（或走 GitHub Actions）。
3. **CTFARENA 50 道 web 需换时间窗重测**（上游限流是暂时的，不是我们代码问题）。
4. **GitHub PAT 吊销**（此前暴露过，仍未处理）。
5. 失败根因=flag 发现（access-control 面），非编排/预算/模型可调。若要提分，方向是**补 web 安全领域知识**（Werkzeug debug 模式 / 访问控制绕过 playbook），不是加并行路数或调参。

---

## 7. 本地环境与操作要点（踩过坑）

- Python：`$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe`
- 先 `$env:PYTHONIOENCODING="utf-8"`；跑批量再设 `$env:PYTHONUNBUFFERED="1"`（否则后台 job 看不到进度）。
- 跑题：后台 job 跑 `cmd /c run.cmd crew <ID>`；**先确认转录文件出现**再判定跑过。
- 单测：`python tests/smoke.py`，v2 基线 **83 passed**，当前工作区 **88 passed / 0 failed**（含 B1 刹车 / http_map / smoke_gate 新增用例）；改动后必须全绿。
- 无 Docker / 无 WSL / 非管理员（镜像构建走 GitHub Actions）。
- `web_search` 不可用；`raw.githubusercontent.com` 被 DNS 拦；`git clone --filter=blob:none` + `git show HEAD:path` 取单文件可用。
- 凭据在 `.env`（`CTFY_TOKEN`、`DEEPSEEK_API_KEY`），**引用时打码**；临时目录里复制 `.env` 后**用完必须删**。
- **实例**：配额 1，串行；`runs/_instances.py`（`--stop` 清实例）；`start_instance` 必须带 `competition_id`；失败常见 "Rate limited by upstream API"；**跑前务必确认 0 实例**（旧实例会污染：SAMPLE-006 复用死实例白烧 156k）。
- **基准/分析脚本**（全在 `runs/`，只读）：`_bench_web.py`（批量驱动）、`_bench_report.py`（分类统计）、`_final_stats.py`、`_consolidate.py`（两臂汇总）、`_cluster_eval.py`（并行求解器评估）、`_tools_stats.py`、`_start_probe.py`（零 LLM 探实例）、`_model_probe.py`（探模型可用性）、`_audit_*.py`（数字审核）。
- 转录 `runs/<run>/transcripts/<ID>.jsonl`：**一行一个工具调用+结果**（`role`=专家名、`tool`、`args`、`result`、`usage`、`step`）；统计一律按行，不按文本出现次数。

---

## 8. 禁忌（别重做）

1. 不要重新推导 A/B 结论、基准数字、卡点分类——都在 `runs/bench-web-report.md`，直接引用。
2. 不要用 150k 预算再跑一遍就去解释"模型不行"——那是预算掐断，不是能力结论。
3. 不要在大规模测试里复用已存在的实例（先 `_instances.py --stop`）。
4. 不要提交 `:v1` 或 `:latest`；不要移动 v1 的 digest。
5. 不要统计时按文本出现次数数拦截/报错（会翻倍）。
6. 不要擅自 commit / revert 并行会话的未提交改动。
7. 不要重复穷举 CTFARENA-035 的凭据（~1202 次全 401 已试；但**不要断言凭据路是死路**——tags 指明方向对，是候选覆盖不足）。
8. 未经用户单独批准，不要改提示词内容。

---

## 9. 与用户的协作偏好

- **只认实验数据**；写数字前用脚本/行号核实，不凭印象。
- 方案先批准再实施；**提示词改动单独批准**。
- 密钥打码。
- 用户在意成本：实验要设预算上限，能提前停就停，报告要给"钱花在哪"的结构化拆解。
- 验尸/统计警惕**同一事实多重计数**（拦截文本被汇报引用、echo 重复），先归类再报数。
- 回答用中文，简洁 + 表格 + 明确口径与置信度。
