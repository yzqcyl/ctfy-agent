# 待修复清单（2026-11-09 记录，等下一个测试跑完后一起修）

> 来源：SQLILABS 69 题本地跑批 + 平台测评 5d8a campaign 的复盘结论。
>
> **√ 修复状态（2026-11-09 已实施并回归验证）：P1(1)(2)(3)、P2a、P2b 已落地；P1(4)(5) 与 P3 仍待办。**

---

## P1 · v6.10：MCP 握手超时与重试策略（托管测评 25–75 分钟空窗的根因之一）

**现象（实测）**：平台容器内 `https://ctfy.io/api/v1/mcp/` TLS 握手 / SSE 读超时；每个 run 在
`agent_started → 首次 LLM 调用` 之间空转 **25–75 分钟**（6 个 run 实测：75.1/60.0/35.0/58.9/24.9/26.6 min），
LLM `queue_ms` 全为 0，可排除模型侧。空窗占 run 时间 ~70%。

**代码位置**：
- `src/ctfy_agent/agent_main.py` L237-258：`config.timeout = AGENT_TIMEOUT or 240`（LLM 与 MCP 共用）
- `src/ctfy_agent/mcp_client.py` L275-306：`initialize`/`list_tools` 各 `retries=2`（3 次尝试 × 240s ≈ 12 min/方法）
- `src/ctfy_agent/agent_main.py` L586-596：外层 `for attempt in range(4)`（最坏 4 × 28 min ≈ 112 min）

**修复方案（已论证，勿踩坑）**：
1. **分离超时**：`initialize` / `tools/list` / `notifications/initialized` 用 30–45s；
   **`tools/call` 与 LLM 请求必须保留 240s**（实测 `submit` 单次 242s，缩短会误杀）。
2. **保留总重试窗口**：短超时 + 多次尝试 + 退避，**总窗口保持 60–90 min**。
   ⚠️ 关键：不能简单"快速失败"——SAMPLE-001/002 是靠熬过 60–75 分钟平台故障窗才连上并解出的；
   若按同样 4 次外层 × 短超时（总窗口 ~20 min）实施，这两题会变成 `halted` 失败。
3. **stderr 加时间戳**：`[warn]` 行补 `HH:MM:SS`，本次复盘靠 transcript 反推，很费劲。
4. **无进展收口**：连续 N 轮 ledger 无新增有效尝试 → 主动 cut_off（SAMPLE-003 重跑 173 轮 / 计费 800k 才停）。
5. **顺带查**：SAMPLE-003 重跑 173 次 LLM 调用 > 配置 `max_llm_calls=160`，调用数上限口径未全局生效（token 预算闸正常，800,519/800,000 精准触发）。

**√ 已实施（1/2/3）**：
- `config.py` 新增 `connect_timeout=40.0` / `connect_deadline=5400.0`（env `AGENT_CONNECT_TIMEOUT` / `AGENT_CONNECT_DEADLINE`）。
- `mcp_client.py`：`initialize/list_tools/notify` 走 `connect_timeout`；`tools/call` 保持 `timeout`（240s）；[warn] 加时间戳。
- `agent_main.py`：外层建连改 deadline 窗口（默认 90 min）+ 封顶退避 + 抖动，替代旧 `range(4)`。
- `platform.py`/`cli.py`：`CtfyPlatform`/`make_platform` 透传 `connect_timeout`。

**⏳ 待办（4/5，需单独设计，风险更高）**：无进展收口、`max_llm_calls` 全局口径。

**收益预估**：平台恢复后 ~1–2 min 内重连（现在最坏等完 28 min）；永久坏容器 20–30 min 交还槽位
（现在 84–121 min）；同时消除 006 类 `no_model` 误判的时间成本。

---

## P2a · `challenges` 命令分页漏题（实锤）

**现象**：MCP `list_competition_challenges` 的 `limit` **默认 50**；`cmd_challenges` 不传 limit，
SQLILABS 实有 69 题只拉到 50 题（051–069 漏掉），用户提醒才发现。

**修复**：`replace src/ctfy_agent/cli.py cmd_challenges` 增加 `--limit`（默认 0=全量）与 offset 循环；
或 `platform.list_challenges()` 默认 `limit=200` 并按 `len(page)==limit` 继续翻页。

**√ 已实施**：`cmd_challenges` 按 `limit=200` 翻页取全量 + `--limit/--offset` 参数。

**验证**：`challenges --competition <sqlilabs> --run-id sqlilabs` 打印 **69 道**（修复前 50 道）。

---

## P2b · 同题残留实例导致 `start_instance` 死锁（实锤）

**现象**：平台上存在当前题（SQLILABS-051）的 ready 实例时，`start_instance` 返回
`Rate limited by upstream API, please retry later`（实际是**配额 1 被占**，错误信息误导）；
`instance_preflight` 只清"不属于本题"的残留，不清"同题残留" → agent 陷重试环空烧 token。

**修复**：
1. preflight 发现"同题 ready 实例"→ **直接复用**（把 service host 传给 crew），或超龄则 stop；
2. `looks_rate_limited` 命中且 `list_instances` 为空 → 说明是配额而非限流，带 `competition_id` 退避重试；
3. 错误文案建议平台区分 `quota_exhausted` / `upstream_rate_limited`。
4. 注意：`start_instance` **必须带 `competition_id`**（多队账号不带会 403 `competition_required`）。

**√ 已实施（1/2 的部分）**：
- `solver._find_live_instance`：改用 schema 的 `challenge_id` 精确过滤（`q` 兜底）+ `instance_guard.instance_challenge_id` 兼容 dict 字段。
- `solver._recover_instance_slot`：reap 停掉 0 个（槽位被同题实例占着）时，直接认领该同题实例复用，不再返回 None。

---

## P3 · 平台侧待上报（非我们代码）

1. 容器出网到 `ctfy.io` TLS 握手超时 / SSE 读超时（同一 agent 代码本地直连 `ctfy.tsinghua.edu.cn` 握手 0.30s，全速跑完 69 题）。
2. `eval_run_no_model_credential` 误判：agent 因 MCP 连不上而 `halted` 退出 0、0 次模型调用 →
   平台归因为"你的 key 过期/被吊销"；同 key 同批解出 5 题。建议读 `result.json` 的 `stop_reason`/`writeup` 再判定。
3. `user_cap=2` 并发上限（20 题 campaign 有 11 题整场没轮到）。
4. 服务器重启丢 run（`node_does_not_know_run`，2 次 interrupted + 重排）。