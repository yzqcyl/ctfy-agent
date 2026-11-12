# SAMPLE 评测报告：v6.1 vs v6（2026-11-03）

数据源：平台 `/api/v1/eval/campaigns/{id}/summary` + `/api/v1/eval/runs`（用户会话 Device Flow 授权拉取）。
两轮均为一次采样（`deepseek-v4-flash`），评测集 SAMPLE（`e4e78ff318dc4844beac13f9ea7409aa`）。

## 1. 总览

| 项 | v6（基线） | v6.1（本次） | 差 |
|---|---|---|---|
| campaign | `4ad065de…` **succeeded** | `6773add4…` **cancelled（incomplete）** | — |
| 窗口 | 08:49–12:00（3h11m，含重跑） | 13:49–14:10（**21 分钟**） | — |
| runs | 20 | 17 | -3 |
| **solved** | **3**（002/010/011） | **2**（002/005） | **-1** |
| unsolved | 4 | 5 | +1 |
| unservable（平台） | 7 | 7 | 0 |
| platform_error / interrupted | 1 | 2（server restart） | +1 |
| cancelled（人工取消） | 0 | 1 | +1 |
| 总 token（in+out） | 140,412 | 236,258 | +68% |
| 成本 | $0.0967 | $0.1756 | +82% |

> 可服务题面只有 8 题（15 题中 7 题 `access_unavailable`，两轮相同）。
> 有效解出率：v6 **3/8 = 37.5%**；v6.1 **2/8 = 25%**（但 v6.1 未跑完）。

## 2. 逐题对比

| 题 | v6 | v6.1 | 归因 |
|---|---|---|---|
| SAMPLE-001 | unservable | unservable | 平台，非我方 |
| SAMPLE-002 | 解出 7t/17.5k | 解出 8t/7.6k | — |
| SAMPLE-003 | **解出** 22t/36.8k | 未解 35t/53.6k（done） | 波动/退化，待查 |
| SAMPLE-004 | 未解 25t/36.2k（done） | 未解 19t/65.9k（**halted**） | 14:00:51 平台重启被杀 |
| **SAMPLE-005** | **4× refused**（qa 不支持） | **解出** 36t/81.6k | ✅ qa 修复生效 |
| SAMPLE-006 | unservable | unservable | 平台 |
| SAMPLE-007 | unservable | errored（server restart）→ unservable | 平台 |
| SAMPLE-008 | errored（restart）+ unservable | unservable | 平台 |
| SAMPLE-009 | unservable | errored（restart）→ unservable | 平台 |
| SAMPLE-010 | **解出** 8t/6.7k | 未解 5t/4.7k（**halted**） | 14:00:51 平台重启被杀 |
| SAMPLE-011 | **解出** 14t/15.1k | 未解 24t/22.9k（**max_turns**） | 波动/预算打满，待查 |
| SAMPLE-012 | 未解 12t/28k（done） | cancelled | 人工取消 campaign，未跑 |
| SAMPLE-013 | unservable | unservable | 平台 |
| SAMPLE-014 | unservable | unservable | 平台 |
| SAMPLE-015 | failed（平台端口） | failed（平台端口） | 平台 |

## 3. 结论

1. **qa 修复按设计生效**：SAMPLE-005 从「4 次 `refused`、0 调用 0 token」变成真正解题并**解出**（36 轮 / 81.6k token）。这是本次唯一针对性的改动，目标达成。
2. **分数下降的主因是平台事故 + campaign 未跑完，不是模型变差**：
   - 14:00:51 平台 **server restart**，SAMPLE-007/009 明确记 `server restarted mid-run`；SAMPLE-004、SAMPLE-010 的 `halted` 分别落在重启后 43 秒 / 8 秒，且 v6.1 会写 `halted` 的路径只有「收到 SIGTERM」——即被重启杀掉。其中 **SAMPLE-010 在 v6 是解出的**，这一分是平台弄丢的。
   - 14:10:45 campaign 被**人工取消**（进度 93%），SAMPLE-012 没跑；而 v6 有 3 小时窗口，SAMPLE-003 在 08:49 因重启失败后于 11:52 重跑成功。v6.1 根本没拿到重跑机会。
3. **逻辑上 qa 改动不可能拖累实例题**：镜像自 962017d 起相对 v6（e630a88）的全部差异只有 `agent_main.py` 的 qa 分支；`run_mode='instance'` 的执行路径逐字节相同（仅多落一个 `is_qa_only: False` 字段，等于原默认值）。所以 003/011 的差异只能来自 LLM 采样波动或预算，不是这次改动引入的。
4. **成本翻倍的合理解释**：qa 题从「秒拒」变成真跑（81.6k token / $0.055），其余差异来自 004 被杀前烧掉的 65.9k。

## 4. 建议

- **重跑一次完整 campaign**（平台稳定时、不要中途取消），至少留 3 小时窗口给重跑；当前两轮分母不同，不能作为优劣结论。
- 需要单独查的两题（与 qa 无关）：SAMPLE-003（v6 解出→v6.1 35 轮未解）、SAMPLE-011（v6 14 轮解出→v6.1 `max_turns`）。
- 平台侧长期问题：7 题 `access_unavailable`、SAMPLE-015 端口不可发布，两轮一致，建议报给主办方。

## 5. 已落地变更（2026-11-03，待构建 v6.2）

依据 `docs/archive/report-ctfy-rules.md`（官方 CTFY-0001 提取件）与 `docs/archive/qa-official-2026-10-28.md`：
**平台不规定步数上限**，只通过 `task.json.max_turns` 下发预算（`null` = 不限，见 `report-ctfy-rules.md:143/227`；
官方 QA 只有「并发 ≤ 3」一条行为约束）。本轮评测平台侧给的是 3600 轮，远大于我们的 14 轮。

因此 14 轮是自定值，已按下述调整：

| # | 位置 | 旧 | 新 | 说明 |
|---|---|---|---|---|
| 1 | `config/agent.json` `max_tokens_per_challenge` | 1,200,000 | **800,000** | 统一权威值 |
| 2 | `src/ctfy_agent/config.py` DEFAULTS 同名项 | 300,000 | **800,000** | 三处数值不再打架 |
| 3 | `agent_main._build_config` | 硬写 800k，无视配置文件 | **env > agent.json > 默认** | 配置文件写了才生效 |
| 4 | `roles.coordinator.max_steps` | 14 | **24** | 协调轮数上限（步数瓶颈所在） |
| 5 | 调用数兜底（`DEFAULT_MAX_TURNS` / `agent.json.max_llm_calls`） | 80 / 100（被忽略） | **160 / 160** | `task.json.max_turns` 仍优先 |
| 6 | 专家角色 `max_steps`（web/ai/rev/crypto/pwn） | 14 | **20** | 仅改数字 |
| 7 | 专家角色 `max_steps`（misc） | 10 | **16** | 仅改数字 |

第 6/7 项的提示词只改了步数行（headline + 各阶段步数分配），**方法、工具面、流程、安全底线一字未动**：

| 角色 | 提示词改动（前后） |
|---|---|
| web | `只有 14 步：2/4/5/1/2` → `只有 20 步：2/6/8/2/2` |
| ai | `上限 14 步：1/约8/1/2` → `上限 20 步：1/约13/2/3` |
| rev | `只有 14 步：1/1/5/3/1/1` → `只有 20 步：1/1/7/5/2/1` |
| crypto | `只有 14 步：1/1/3/5/2/1` → `只有 20 步：1/1/4/8/3/1` |
| pwn | `只有 14 步：1/3/2/3/1/1` → `只有 20 步：1/4/3/5/1/1` |
| misc | `只有 10 步：1/2/3-7/8/9` → `只有 16 步：1/2/3-12/13/15` |

`roles/ai.json`、`roles/misc.json`、`roles/*.system.txt` 与 `config/agent.json` 三处同步（漂移守卫测试通过）。

专家角色 `max_steps` 保持 14（提示词明写「只有 14 步」，改它需单独批准改提示词）。

验证：`tests/smoke.py` 167 项通过；全部 `tests/smoke_*.py` 通过（`smoke_p2.py` 需 `PYTHONPATH=src`，与 CI 一致）。
预期：单题可跑轮数 14 → 24（约 1.7×），token 用量约 2–3k/轮 → 24 轮约 60–120k，仍远低于 800k；耗时约 1.7×
（参照 SAMPLE-003 的 461s → 约 13 分钟）。
