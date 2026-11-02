# 跨项目机制融合评估：ctf-forge → ctfy-agent（2026-11-02）

> 状态：**仅评估与方案，未改任何代码**。按项目规矩（HANDOFF §H.10「方案先批准再实施」），
> 待批准后按 P0→P1→P2 分批落地；提示词类改动单独批准。
> 被评估对象：`C:\Users\Lenovo\AppData\Local\Temp\ctf-agent-run\-agent-ctf\ctf-forge`（v7，2026-11-01）。

---

## 0. 结论摘要

1. **两者不是同类**：ctfy 是「平台契约 + 多智能体 + 账本/证据 + 镜像交付」的**参赛系统**；
   ctf-forge 是「单题沙箱 + 单一工具循环 + 独立验证」的**解题工作台**，靠外部 Agent（Codex/人）驱动。
2. **主线不要换**：ctfy 在编排、预算、证据、交付上全面领先；ctf-forge 在**进程/沙箱的工程严谨度**
   与**竞赛调度方法论**上更成熟。
3. **可取的只有 6 项**，其中 3 项是 ctfy 的**真实缺陷**（不是风格差异）：
   - 实例配额浪费（11-03 有 21% 的题因 `start_instance` 5 次上限作废）→ 直接丢分；
   - 子进程输出无界缓冲 + 无进程组杀（`capture_output=True` 全量进内存）；
   - 无墙钟预算（只有 token/calls/cost，session 20:00 到点前没有收口机制）。
4. **红线**：ctf-forge 为 **AGPL-3.0**（且 vendored BoxPwnr 亦 AGPL）。**只借鉴机制、自行实现，
   不复制任何源码/注释/文档原文**，避免 ctfy 被动变成 AGPL 衍生作品（ctfy 目前无 LICENSE）。

---

## 1. 体检基线（本次实测）

| 项 | 结果 | 口径 |
|---|---|---|
| `tests/smoke.py` 总入口 | **154 passed / 0 failed** | 【实测 11-02 18:42】本机工作区（含 6 文件 WIP） |
| git HEAD | `75bea86`（branch `v3`），上一提交 `89739a5` | 【实测】 |
| 未提交 WIP | 6 改 + 6 份未跟踪报告（= HANDOFF §B.4 清单，一致） | 【实测】`git status` |
| 归档 | `E:\ctfy_backups\20261102-pre-merge\`（bundle + patch + tar + SHA256） | 【实测】本文件同期产出 |
| 与 HANDOFF 的偏差 | 无实质偏差；HANDOFF 说 HEAD `89739a5`，实际多一个 `75bea86`（交接文档重写本身） | 【实测】 |
| 代码规模 | src 29 模块 16,190 行；ctf-forge 约 4,100 行（src+tests） | 【实测】`wc -l` |

---

## 2. 被评估对象：ctf-forge 是什么

- **定位**：给「外部 Agent（Codex/任意 LLM 助手）」用的 CTF 解题工作台——Agent 出决策 JSON，
  程序负责 Docker 执行、观察、候选验证、报告。也支持 DeepSeek + LangGraph 无人值守（**无 Key，未实测**）。
- **战绩**：HI Cyber Con 2026（rCTF，68 题/12 类）**42 题 / 2461 分 / 61.8%**，主代理 + 10 路子代理常驻。
  注意口径：这是**外部 Agent 实战成绩**，不是模型 Pass@k；原始赛题与回执已按作者要求删除，**不可复算**。
- **工程证据**：81 项 pytest、ruff/mypy 全过、**14/14 真实容器隔离探针**、10/10 合成 ELF 脚本集成。
- **形态**：单类别（ELF 逆向）+ 平台 REST（rCTF）拉题/提交；**没有**多角色、账本、方法卡、上下文工程。

---

## 3. 能力互补矩阵

| 能力 | ctfy 现状 | ctf-forge 做法 | 取谁 |
|---|---|---|---|
| 多智能体编排 | 协调者 + 6 专家 + 集群 A/B（`crew.py` 1599 行） | 无（单 agent 单循环） | **ctfy** |
| 去重/证伪 | `ledger.py` 归一化 args 键 + 假设簇 + 逐字证据（706 行） | 输出哈希 `seen` 集合判停滞 | **ctfy**（更细） |
| 上下文工程 | `ctxcompress` + 事实板 + 滑窗 + 段重置 + 缓存折价 | 8000 字符头尾截断 + 按工具定制摘要 | **ctfy**（更完整）；仅借「按工具定制摘要」的点子 |
| 平台契约/交付 | task.json→result.json→镜像→三道门（已完成 v4） | rCTF REST，宿主侧联网 | **ctfy** |
| 预算刹车 | token / 调用数 / 金额，三层 + 尝试切片 | token / 工具数 / 模型数 / **墙钟** / **停滞步** | **两者都要**：ctfy 缺后两项 |
| **沙箱隔离** | 由平台容器提供；本地 CLI 直接用宿主 `shell=True` | 一题一容器：`--network none`、`--read-only`、`--cap-drop ALL`、uid 10001、mem/cpu/pids、超时销毁 | **ctf-forge**（仅借鉴探针，不引入嵌套 docker） |
| **子进程收口** | `subprocess.run(capture_output=True)` 全量进内存后切片 | 双线程流式 drain + `max_bytes` + 超时 `kill` + 销毁容器 | **ctf-forge**（真缺陷） |
| 验证独立性 | 平台回执 + `verify_answer` 免费 oracle 预检 + 提交幂等 | host 答案文件 sha256 不变性 + 平台唯一权威 | **ctfy**（oracle 预检更强）；借「输入/答案指纹不变性」 |
| 断点续跑 | 协调者 `checkpoint`（messages/reports/ledger/plan） | `session.json` 原子写 + **输入 sha256 变更即拒绝续跑** | 两者互补：ctfy 补「输入指纹」 |
| 并发保护 | `state.lock`（msvcrt/flock）+ 转录线程锁 | 每任务 `operation.lock`（`O_EXCL`） | **ctfy**（够用） |
| 报告产物 | transcripts jsonl + 各 bench 报告 | JSON 权威 + MD 伴生 + `history/` 版本化 | 近似；ctfy 批处理口径更细 |
| **竞赛调度方法论** | 有 `solves_count`/`difficulty` 入库，但**无调度代码** | 实例池中央调度、solve 数优先、委派卡、HANDOFF 五要素、错误提交预算 | **ctf-forge**（方法论可代码化） |
| 合规/发布闸门 | 一次性密钥扫描；**无 LICENSE**；第三方义务未落文档 | `THIRD_PARTY_NOTICES` + 哈希 + 只审计 staged 文件 | **ctf-forge** |
| 自检 | `container_check.py` 五条契约路径 + `verify_image.py` | 14 项隔离负向探针（含 `docker inspect` 主机侧配置核验） | **ctf-forge**（补一项） |

---

## 4. 融合清单（按「收益 ÷ 风险」排序）

### P0 — 直接买分 / 修真实缺陷（建议立刻做，全部纯代码、零提示词改动）

| # | 点 | 落点 | 具体做法 | 验收 |
|---|---|---|---|---|
| P0-1 | **实例守卫三件套** | 新 `tools/instance_reap.py` + `cli.cmd_crew/cmd_auto` 预检 + `crew.solve` 每 N 轮清扫 | ① 开跑前 `list_instances`，非本题实例一律 `stop_instance`（对应 HANDOFF §H.3「先确认 0 实例」）；② 每 3 轮自动扫一次，释放已解/已放弃/失联实例；③ 起实例前若本题 `tool_counts.start_instance` 已达 4，直接降级为「不收口的长跑」并写入 summary | 单测：fake MCP 造 2 个残留实例 → 断言开跑前被清；`start_instance` 计数达 4 时不再调用 |
| P0-2 | **子进程有界收割 + 进程组杀** | `tools_local.py::_run_bounded()`（替换 `shell`/`tcp`/`run` 类调用的 `subprocess.run`） | 双线程流式 drain（头/尾各留 `MAX_BODY`），超时 `kill` 整个进程组（POSIX `killpg` / Windows `taskkill /T`），返回 `status=TIMEOUT` | 单测：`yes | head -c 50000000` 不再 OOM；`sleep 99` 超时后无残留子进程 |
| P0-3 | **墙钟刹车** | `budget.py` 新增 `deadline_at` / `run_deadline_minutes`；`BudgetPhase` 同源注入；`crew.solve`/`run_role`/`_solve_once` 每步前置判定 | 超时走既有「合成报告 + 收口」路径（`_rescue_report`），`stop_reason="wall_clock"`；默认关闭（`extra.deadline_minutes=0`），容器里按 session 结束时间由 `CTFY_EVAL_*` 或平台时长推算 | 单测：`deadline_minutes=0.02` → 必在 3s 内退出且产出 report |

> P0 的三项都不改提示词、不改 config 语义（新增键默认关），失败可整文件回滚。

### P1 — 工程卫生与合规（低成本，建议同批）

| # | 点 | 落点 | 做法 | 验收 |
|---|---|---|---|---|
| P1-1 | 输入/附件指纹不变性 | `state.py` / `checkpoint.py` | run 首次 `get_challenge` 后落 `challenge_fingerprint = sha256(description + attachments[name,size])`；`--resume` 前不一致则拒绝续跑并提示 `--fresh` | 单测：改 description 后 resume 被拒 |
| P1-2 | 合规三件套 | 根 `LICENSE` + `THIRD_PARTY_NOTICES.md` + `tools/secret_scan.py` | LICENSE 需用户选型（见 §6）；NOTICES 记录 ffuf 2.1.0(Apache-2.0)/SecLists(MIT)/sqlmap 1.10.10(GPLv2+) 的来源、commit、sha256 与分发义务；扫描器只扫**显式传入的文件清单或 `git diff --cached`**，不依赖 `.gitignore` | 扫描器在故意植入 `sk-xxxx` 的临时文件上必命中；CI 加一步 |
| P1-3 | 镜像隔离自检 | `tools/isolation_probe.py`，并入 `container_check.py` 作第 6 项 | 容器内 4 项负向探针：uid≠0、无法写 `/`、无法写 `/ctfy/work`、外联 `1.1.1.1:443` 必须失败、镜像内无 `*_API_KEY` 环境变量 | 本地 `--local` 时降级为「跳过并标注」，真容器里必须 5/5 |

### P2 — 方法论代码化（价值高但需先确认口径，建议单独一轮）

| # | 点 | 做法 | 说明 |
|---|---|---|---|
| P2-1 | 批次调度器（solve 数优先） | `auto.py` 选题打分为 `solves_count` 降序 × 剩余时间权重 × 已有进展；对应 ctf-forge「solve 数是免费且最准的难度指标」 | 仅影响**本地批量/实验**；生产由平台派题，不适用 |
| P2-2 | 单题接力卡 | 把已有 `facts/dead_ends/tail_action` 渲染成 `runs/<run>/handoff/<challenge>.md`（五要素：已确证/已否证/卡点/下一步命令/产物路径） | 供集群-B 继承与人工接力；**不改提示词**，只多一个产物 |
| P2-3 | 停机原因枚举化 | `stop_reason` 收敛为有限集（`flag_accepted/partial_solve/budget/tool_timeout/wall_clock/no_progress/llm_error`），由单一 `stop_reason()` 裁判 | 只影响报告口径与统计脚本 |
| P2-4 | 诱饵 flag 制度 | ctfy 已有 `verify_answer` 免费预检 + 提交幂等（重复答案被拦），**基本覆盖**；仅补「已拒绝值清单」在 `submit` 拒绝时回显「这 X 个值已判错，别重交」 | 低优先级，多数题 0 次错交 |

---

## 5. 明确**不**融合（连同理由一起留档）

| 项 | 不采纳理由 |
|---|---|
| 一题一 Docker 容器（`--network none` 等）作为生产形态 | 平台容器**就是**沙箱；镜像内无 docker socket，嵌套 docker 必然失败。仅本地开发有意义，成本 >> 收益 |
| 工具面「argv-only，禁用 shell」 | ctfy 的 `shell=True` 是给模型的操作自由度（web/rev 实测用到）；改成 argv 白名单会让 25 个工具的表达力倒退。改为 P0-2 的「有界收割 + 进程组杀」即可 |
| LangGraph + SQLite checkpoint | ctfy 已有 `checkpoint.py`（含 ledger/plan 序列化），引入框架会增加非 stdlib 依赖，违反 `case_stdlib_only` |
| 外部 Agent 驱动（solve/observe/step/report） | 赛事单模型契约要求全自主；外部驱动只对本地调试有意义，不入镜像 |
| `capabilities` JSON + AGENTS.md 自描述 | ctfy 的入口是平台契约（task.json/result.json），不需要第二套自描述协议 |
| 单工具 30 秒上限 | ctfy 各工具有自己的超时（http 20s、burst 分片、shell 可配），硬压 30s 会伤 `http_map`/`fuzz` |

---

## 6. 需要用户决策的两件事（已定）

1. **LICENSE 选型**（ctfy 目前无 LICENSE 文件，HANDOFF §F.2.4 未决）：**已定 MIT**（2026-11-02 用户决定）。
   理由：与镜像内 Apache-2.0(ffuf)/MIT(SecLists) 相容；sqlmap 的 GPLv2+ 以独立程序形式分发，
   义务写在 `THIRD_PARTY_NOTICES.md`，不传染自有代码。
2. **P0-3 墙钟预算的默认值**：**已定默认关闭**（`0`/不配 = 不限）。生产里靠平台 `max_turns`，
   本地批量与实验按需设 `deadline_minutes`；容器侧可用 `AGENT_DEADLINE_AT` 由外部注入。

---

## 7. 执行与回滚

- 归档（已完成）：`E:\ctfy_backups\20261102-pre-merge\`
  - `repo-all.bundle`（全历史，`git bundle verify` 通过）
  - `wip-tracked.patch`（当前 6 文件未提交改动）
  - `worktree-src.tar.gz`（src/config/tests/tools/roles/docs 快照）
  - `SHA256SUMS.txt` / `RESTORE.md`（三种回滚路径）
- 分批落地顺序：**P0-1 → P0-2 → P0-3 → P1**；每批后跑 `tests/smoke.py`，必须保持 154/0 且新增用例通过。
- 每批单独出 patch（`git diff > E:\ctfy_backups\<阶段>.patch`），保证可按批回滚。

---

## 8. 落地记录（P0 + P1 已完成，2026-11-02）

**测试基线：154 passed → 158 passed / 0 failed**（新增 4 条用例）；相关独立套件
（smoke_progress / smoke_local_tools / smoke_http_cache / smoke_evidence / smoke_rev 15/15）同步复跑通过。
`tools/container_check.py --local` 五条契约路径仍全绿。

### 8.1 改动清单

| 批次 | 文件 | 改动 | 新增用例 |
|---|---|---|---|
| P0-1 | **新** `src/ctfy_agent/instance_guard.py`（~200 行） | `reap()`/`start_remaining()`/`list_all()`/`summarize()`；保守停机（保护名单 + 宽限期 + 拿不到时间戳一律不动） | — |
| P0-1 | `solver.py::_recover_instance_slot` | start 失败且像限流 → 先清残留再重试一次；**剩余配额 <2 时绝不重试**（避免烧光当天额度） | `case_instance_guard` |
| P0-1 | `crew.py::_instance_sweep` + 轮循环 | 每 N 轮（默认 3）清扫，`extra.instance_reap=true` 才启用（生产容器不需要，平台自己回收） | 同上 |
| P0-1 | `cli.py::instance_preflight` + `--reap/--no-reap` | `crew` 默认只警告（`--reap` 才动手）；`auto` 每题开跑前默认清扫（串行批跑） | 同上 |
| P0-1 | **新** `tools/instance_reap.py` | 宿主侧独立清扫器：默认 dry-run，`--keep/--protect/--grace/--reap` | — |
| P0-2 | `tools_local.py::run_bounded` + `_BoundedSink` + `_kill_tree` | 双线程流式收割（头 3000 / 尾 9000 字节，内存恒定）；超时杀**整棵进程树**（POSIX killpg / Windows taskkill /T） | `case_bounded_run` |
| P0-3 | `budget.py` | `deadline_at` + `seconds_left` + `deadline_clock` + `stop_reason_of()`；`BudgetExceeded.kind` 机器可读；`apply_deadline(extra)`（支持 `deadline_at` ISO/epoch 与 `deadline_minutes`，写错格式当没设） | `case_wall_clock` |
| P0-3 | `crew.py` / `solver.py` / `progress.py` / `agent_main.py` | 构造 Budget 时挂墙钟；专家与单 agent 每步前置 `budget.check()`（不依赖客户端实现）；预算提示行显示剩余分钟；`wall_clock → cut_off` 映射 | 同上 |
| P0-3 | `config.py` | `AGENT_DEADLINE_AT` / `AGENT_DEADLINE_MINUTES` 环境变量注入（容器里没有命令行） | 同上 |
| P1-1 | `state.py::Challenge.fingerprint` + `checkpoint.py` | 题面指纹（id+description 的 sha256 前 32 位）；指纹不符即丢弃 checkpoint；旧格式（无该字段）放行 | `case_input_fingerprint` |
| P1-2 | **新** `LICENSE`（MIT）、**新** `THIRD_PARTY_NOTICES.md`、**新** `tools/secret_scan.py` | MIT 授权；ffuf/SecLists/sqlmap 的来源+校验值+分发义务；扫描器只认高置信形态、默认排除 `tests/`、支持 `secret-scan: allow` 行内豁免 | 见 §8.3 手工验证 |
| P1-2 | `.github/workflows/build-agent-image.yml` | 新增「密钥扫描（--all）」一步 | — |
| P1-3 | **新** `tools/isolation_probe.py` | 六项姿态探测：non_root / source_readonly / work_writable / egress_blocked / no_dotenv / dry_run_exit_zero；无 docker 或引擎未起 → SKIP（`--strict` 才算失败） | 见 §8.3 |
| P1-3 | `tools/container_check.py` | 新增 `--posture` 开关（默认关）调用上面的探针 | — |
| P1-3 | `.github/workflows/…` | 镜像校验后加一步姿态自检 | — |

### 8.2 与 §4 方案的两处偏离（都为了不引入新的失败面）

1. **姿态自检没有硬接成 container_check 的第 6 项**，而是做成 `--posture` 开关 + CI 单独一步。
   原因：本机无可用 Docker 引擎，无法验证「硬接」后的 docker 路径；而 `container_check` 是
   交接文档里既定的验收入口，不能拿没验证过的代码去改它的默认行为。
   → CI 里是真的会跑的（runner 有 docker）；本地想跑就 `--posture`。
2. **扫描器默认排除 `tests/`**。第一版把 `smoke_p1` 的 `sk-l…` 假 key、AI 夹具的 `ASI-…`、
   `smoke_rev` 的字节码常量全判成泄漏（8 处假阳性）——这正是 ctf-forge 记的坑「扫描器误报多到没人看」。
   `--all` 默认跳过 `tests/`，要连测试一起审就加 `--no-exclude`。

### 8.3 怎么复验（不需要令牌、不需要 Docker）

```powershell
$env:PYTHONIOENCODING="utf-8"; $env:PYTHONPATH="src"; $env:NO_PROXY="127.0.0.1,localhost"
python tests/smoke.py                        # 期望 158 passed / 0 failed
python tools/instance_reap.py                # 无令牌会报配置不完整（预期行为，不会乱停实例）
python tools/secret_scan.py --all            # 期望「干净 ✓」
python tools/isolation_probe.py              # 期望 [SKIP] Docker 引擎未就绪
python tools/container_check.py --local      # 期望五条契约路径全绿
```

### 8.4 新增配置键（全部默认关，不配=旧行为逐字不变）

| 键（`config/agent.json` 或 `AGENT_*` 环境变量） | 默认 | 作用 |
|---|---|---|
| `deadline_at` / `AGENT_DEADLINE_AT` | 无 | 墙钟截止（ISO 或 epoch） |
| `deadline_minutes` / `AGENT_DEADLINE_MINUTES` | 无 | 从现在起 N 分钟后收口 |
| `instance_reap` | false | 是否在协调者循环里定期清扫实例 |
| `instance_reap_every` | 3 | 每几轮扫一次 |
| `instance_reap_protect` | [] | 永不清扫的 challenge_id 列表（并发批跑用） |
| `instance_reap_grace_seconds` | 120 | 只清扫存活超过 N 秒的实例（防误杀兄弟进程刚起的） |
| `instance_recovery_grace_seconds` | 60 | start 失败自愈路径的宽限期 |

> 仍待办：P2（auto 选题按 solve 数排序、单题交接卡落盘、停机原因枚举化、已拒值回显）——
> 尚未实施，等本批效果确认后再动。

---

## 9. 落地记录（P2 第三批，2026-11-02 第二批之后）

**测试基线：158 passed → 160 passed / 0 failed**（新增 `case_auto_order` / `case_handoff_and_rejected`）。

| # | 项 | 落点 | 改动 | 风险 |
|---|---|---|---|---|
| P2-1 | 批次选题按 solve 数降序 | `auto.py::_score/_select` + `cli --order {score,id}` | 排序键 = (solve 数降序, 分值降序, 难度升序, 题号)；缺 solve 数按"中等"处理 | 低：只影响 `auto` 的选题顺序，`--order id` 可退回旧行为；bench 脚本走的是自己的 shell，不受影响 |
| P2-2 | 单题交接卡 | **新** `src/ctfy_agent/handoff.py` + `crew.solve` 收尾 | 把 summary 里已有的 facts / dead_ends / candidates / tail_action 渲染成五要素 md，写到 `<run>/handoff/<cid>.md` | 极低：只多一个产物，写盘异常被吞掉，不改摘要 |
| P2-4 | 已判错答案回显 | `state.note_rejected` + `solver._rejected_hint` | 平台判错时记明文答案（去重、只留最近 8 条），下次提交失败时回显"这些别再交" | 低：多几十字上下文，换来不重复错交（提交预算只有 3 次） |
| P2-3 | 停机原因枚举化 | **刻意未做** | 现有 reason 串（`no_more_actions/flag_accepted/partial_solve/budget_exhausted/attempt_cap/wall_clock/llm_error/step_limit/superseded/completion_burned`）已被 `retry.py::STOP_REASONS`、`agent_main::_STOP_MAP` 与全部历史统计脚本消费 | 强行收敛会改统计口径，收益只是"好看"——按"没有证据的改动不做"放弃 |

**P2 的验收方式**（没有真实跑题预算时的最高标准）：

```powershell
python tests/smoke.py            # 期望 160 passed / 0 failed
python tools/secret_scan.py --all # 期望干净
python tools/container_check.py --local  # 期望 5/5
```

**若 P2 效果不理想**：P0+P1 的完整存档在 `E:\ctfy_backups\20261102-stage1-p0p1\`
（`worktree-src.tar.gz` 覆盖回该阶段 + `diff-vs-HEAD.patch` 留痕），回滚只需
`tar xzf worktree-src.tar.gz -C E:/ctfy_project`，不依赖 git 状态。
