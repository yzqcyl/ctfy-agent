# Linux 环境批量验证（2026-11-18 15:46–16:01）

> 目的：验证「换到 Linux 执行环境是否真能提升 PWN/REV」——用**和 Windows 批次不同的 4 道 Easy 题**
> （2 PWN + 2 REV），直接跑在比赛机（39.107.43.191）的 `ctfy-agent:v7.3` 容器里。
> 命令：`docker run --user 0 -v /root/ctfy_practice/repo:/work -w /work -e PYTHONPATH=/work/src --entrypoint python ghcr.io/yzqcyl/ctfy-agent-pub:v7.3 -u tools/run_batch.py --manifest runs/batch-linux-easy/manifest.json --run-id batch-linux-easy-2026-11-18 --profile ctf2`

## 1. 结果：0/4 解出（但**环境零故障**）

| 题 | 类别 | 结果 | 用时 | token | 记录 | 主要卡点 |
|---|---|---|---|---|---|---|
| warmup（0CTF 2016，ret2syscall） | PWN | 未解 | ~4.2 min | 263k | 78 | 预算耗尽、report 被拒×1、ledger 拦截×4、pwnsession 参数错 |
| [HarekazeCTF2019]baby_rop | PWN | 未解 | ~4.5 min | 250k | 182 | 预算耗尽、report 被拒×1、ledger 拦截×2 |
| CrackMe | REVERSE | 未解 | ~2.7 min | 204k | 194 | ledger 拦截×1（未撞预算，主动收口） |
| brichfire | REVERSE | 未解 | ~3.7 min | 253k | 197 | 预算耗尽、ledger 拦截×2 |

合计 **970,282 token / 15.2 分钟**（≈64k token/分钟）。

## 2. 环境问题的确解决了（对比 Windows 批次）

| 指标 | Windows 批次（easy 4 题） | Linux 容器批次（4 题） |
|---|---|---|
| shell 非零退出 | 14 次 | **0 次** |
| 缺工具/编码坑 | python3/tee/tar、GBK 崩溃 | **0 次**（ELF 分析、capstone、pwntools、gdb 都可用） |
| 答案/推理类失败 | 有 | 仍然有 |

**结论：环境不是现在的瓶颈。** Linux 下工具链一次没报错，`warmup` 甚至完成了完整静态分析
（找到 `pop esp` gadget、确认偏移 36、实测 core dump 验证栈迁移可行），但仍在预算耗尽前没打通。

## 3. 现在的真实瓶颈（按影响排序）

1. **单题 token 消耗太快**：250k 在 3–4 分钟内烧完（≈64k/min）。
   原因：PWN 每题要写/跑多个 exploit 脚本，每次 shell 输出 + 反汇编窗口都进上下文。
   → 建议 Easy 直接 500k，或给 PWN/REV 单独 cap（按类别而非难度）。
2. **ledger 重复拦截 9 次**：同一 `(tool,args)` 重复直接拒绝，agent 常靠「原样重试」推进，被拦后还要花步数换路。
   → 提示词加一条「被 ledger 拦截后禁止同参数重试，必须换参数或换路」已有，但执行不到位；
   可考虑把「拦截」直接回灌成「上次结果 + 建议换的参数」。
3. **report evidence 必须是逐字片段**：2 次被拒（agent 概括了输出）。
   → 让 report 工具自动附带「最近一次工具输出原文」，或放宽为「子串匹配 + 相似度阈值」。
4. **pwnsession 参数拼错（port=0）**：一次失败 + 被 ledger 记成重复，浪费步数。
   → 参数校验前置（缺 port 直接报「缺少 port，请从 access_url 里取」）。
5. **BUUCTF 的 Easy 标签不可靠**：`warmup`（0CTF 2016）实际是 ret2syscall 技巧题，
   baby_rop 要 ret2libc 泄露；按标签选题不如按「已解人数」选题。

## 4. 赛前建议（按性价比）

| 优先级 | 动作 | 成本 |
|---|---|---|
| P0 | Easy/PWN/REV 单题 cap 提到 **500k**（`taskplan.DIFFICULTY_CAPS` 或按类别 cap） | 1 行 |
| P0 | 预算到 **80% 强制收口提交候选**（现在都是 100% 耗尽才收口） | ~20 行 |
| P1 | report 工具自动带最近工具输出原文（或放宽 evidence 校验） | ~30 行 |
| P1 | `pwnsession` / `tcp` 参数校验前置，缺 host/port 直接报错并给 access_url | ~15 行 |
| P2 | 批次选题按 solve_count 排序（不用 difficulty 标签） | 已实现，改选法即可 |

> 注：本批用的是**比赛镜像本身**（v7.3），说明镜像在 Linux 下行为正常；
> 这批数据也反过来验证了「Windows 沙盒 → Linux 容器」确实消除了工具链损耗。
> 练习数据留在比赛机 `/root/ctfy_practice/`（可删），本地副本 `runs/_ssh/linux/`。

## 5. 已按用户要求实施（2026-11-18 下午，P0/P1 全部落地）

用户决定：**单题预算全部开到 800k**，其余按上面 P0/P1 清单执行。

| 项 | 改动 | 位置 |
|---|---|---|
| 预算全开 | easy/medium/hard **全部 800k**，fallback 也 800k；near-miss 加注仍**封顶 800k** | `src/ctfy_agent/taskplan.py` |
| P0 强制收口 | ≥80% 起每轮注入「只能 submit、禁止开新面」硬规则；≥90% 或提示 2 轮仍未交 → **代码直接提交账本候选**（受平台 submit_limit 约束，交过即停） | `progress.BudgetPhase.ratio/must_submit` + `crew.py`（`_FORCE_SUBMIT_HINT`、`force_submit_rounds`） |
| P1 report | 整段 evidence 对不上时，按**行/引号/反引号片段**切开再逐条溯源；`ledger.verify_evidence` 对 ≥24 字符证据加**相似度 ≥0.92 兜底**（短证据仍严格逐字，避免放过概括） | `solver._evidence_chunks`、`ledger.verify_evidence` |
| P1 pwnsession | `host/port` 前置校验；缺/错值时错误文案直接写「从实例回执 `access_url` 取 host:port，别传 0/空串/整条 URL」 | `tools_domain_ext._require_endpoint`（tcp / pwnsession 共用） |

回归：`tests/smoke.py` **180 passed / 0 failed**；受影响单测 `smoke_taskplan` / `smoke_progress` /
`smoke_pwnsession` / `smoke_evidence` / `smoke_time_budget` 全绿；全量 `tests/smoke*.py` 77 个文件中
仅 5 个失败，且**全部在 HEAD（未打补丁时）同样失败**（并发改动：`config/agent.json max_tokens=12288`、
`tools/linux_bootstrap/*` 触发 secret-scan、离线平台桩、Windows 临时目录权限），与本次改动无关。

### 5.1 复测（800k 版本，run-id `batch-linux-easy2-2026-11-18`）

同一批 4 题在比赛机容器里重跑，**结果仍是 0/4，但暴露了三个新事实**（见 `runs/_ssh/linux2/`）：

| 题 | 结果 | 用时 | token | attempts | 关键观测 |
|---|---|---|---|---|---|
| warmup | 未解 | ~14 min | 800,184 | 0 | **ledger 重复拦截 42 次**（每次参数不同，逐条 streak 断路器抓不到） |
| baby_rop | 未解 | **32 s** | ~0 | 0 | 平台/网关瞬断 `Remote end closed connection without response` → 整题报废 |
| CrackMe | 未解 | ~7 min | 794,069 | 0 | 预算耗尽；ledger 拦截 22 次 |
| brichfire | 未解 | ~11.5 min | 802,343 | 0 | 预算耗尽 |

结论：
1. **800k 单题预算本身不是充分条件**：三题都烧穿 800k 且 `attempts=0`——账本里始终没有候选值，
   `80% 强制提交`没有候选可交（护栏本身工作正常，但没有输入）。
2. **ledger 重复拦截是主要浪费源**：warmup 42 次、CrackMe 22 次，每次拦截都仍消耗一次模型调用。
3. **瞬断会让整题在 30 秒内报废**：baby_rop 只因一次网关断连就丢掉整题。

### 5.2 针对上述三条的补丁（同轮提交）

| 问题 | 补丁 |
|---|---|
| 平台瞬断 | `cli.ensure_challenge_meta` 对 `get_challenge` 瞬断重试 3 次（2s/5s）；`solve_many` 单题瞬断隔 10s **整题重试一次** |
| ledger 累计打转 | `crew` 新增**累计拦截刹车**：≥6 次注入「换信息维度/换专家」提示，≥14 次强提示，≥22 次升级为 action_loop 前最后警告 |
| 无候选可提交 | 保持 80% 护栏；同时靠 ledger 刹车把预算留给「真正能产出候选」的路线 |

回归：`tests/smoke.py` 180/0；`smoke_clusterb_crew` / `smoke_platform_seed` / `smoke_taskplan` /
`smoke_progress` / `smoke_budget_phase_rerun` / `smoke_submit_ledger` / `smoke_evidence` 全绿。
