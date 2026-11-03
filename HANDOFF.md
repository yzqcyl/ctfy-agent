# 交接文档 — ctfy-agent（TPCTF 2026 智能体团队赛道）· 对外交接版

> **读者**：接手整个项目的外部团队/个人。本文件自包含，按 A→G 顺序读即可上手；
> §H 与附录是项目积累的经验教训，一并移交，避免重蹈覆辙。
> 只想粘一段话开新对话 → 用 `docs/OPENING.txt`（本文件浓缩版）。
> **口径规则**：每个关键数字后标注来源（【实测 N】=当天实跑；【审计 N】=当日审计文档；
> 【本地-only】=证据文件在 gitignored 目录，需另行转移才能看到）。

---

## A. 项目是什么 · 现状一句话

### A.1 定位

- **目标**：TPCTF 2026 **智能体团队赛道**，提交物 = **一个容器镜像**（不是本地 CLI）。
- 镜像里是纯 stdlib Python 的多智能体 CTF 解题框架：1 个协调者 + 6 个方向专家
  （web / pwn / rev / crypto / misc / ai），通过平台 MCP 工具解题并当场提交。
- 入口：`python -m ctfy_agent agent`；读 `/ctfy/work/task.json` → 写 `/ctfy/work/result.json` → `exit 0`；
  **stdout 不被平台读**，日志全走 stderr。
- 仓库：`E:\ctfy_project` → GitHub `https://github.com/yzqcyl/ctfy-agent.git`（branch `v3` 为当前线，`master` 为旧线）。

### A.2 现状速览（2026-11-03）

| 项 | 状态 |
|---|---|
| **git 基线** | HEAD `773892d`（v3 线，2026-11-02 三连提交：`fc7dc42` 许可 → `291e1d0` 报告 → `773892d` WIP+融合）；树 ≈3.2 MB；`ctfy_agent` 包 31 模块；docs 74 篇；smoke 套件 34 个文件 |
| **交付镜像** | ✅ v4 已构建推送：`ghcr.io/yzqcyl/ctfy-agent-pub:v4`，digest `sha256:4e153aeb41fb…`（详见 §D.1/附录 D）|
| **平台审核/提交** | ⬜ **未开始**，只能人工走浏览器（`docs/submit-image-runbook.md` + `platform-submit-opsheet.md`）——交接后第一优先级之一 |
| **总入口单测（干净 clone）** | ✅ **163 passed / 0 failed**【实测 11-05，干净 clone 口径】：`bench_stats` 已入库到 `tools/bench_stats.py`，缺口 #11 关闭|
| **总入口单测（原工作区）** | **160 passed / 0 failed**【实测 11-02】：154 基线 + 本批新增 6 条（`instance_guard` / `bounded_run` / `wall_clock` / `input_fingerprint` / `auto_order` / `handoff_and_rejected`）；含 3 个缺陷修复的回归用例——**已全部提交**（§B.4 / §B.5）|
| **最新实测能力** | 六题型 52 题：解出 **51.9%**，剔环境阻塞 **65.9%**，能力口径 **75.6%**；qa 12/12、web 7/11、misc 4/6、crypto 2/8、rev 2/3、pwn 0/1（样本无效）【实测 11-03】|
| **CI** | workflow **已跑单元测试**：总入口单测 + 25 个免凭据套件 + 密钥扫描 + 镜像姿态探针（§B.4 / §B.5，已提交）|

### A.3 五个必须先知道的事实

1. **单模型契约**：一次运行只允许一个 provider 的一个模型，所有槽位同值。代码已双保险
   （`agent_main._build_config` pop 掉角色级 `model` + `llm_factory` 忽略入参 + 非 openai flavor 直接 refused），
   `LLM_API_KEY` 为空必须大声失败、绝不回退自有 key。
2. **平台实例配额**：每队**并发 1 个实例**（第 2 个 0.5s 被拒）；**每题 `start_instance` 硬上限 5 次**
   （用尽当天该题作废，11-03 批次 21% 的题因此未测到）。多题并行在平台侧被堵死。
3. **最新 3 个修复 + CI 测试步已于 2026-11-02 提交**（多问题提交题号、部分解口径、QA 提交通道；`773892d`），
   它们就是 11-03 成绩的代码来源（§B.4 有文件级清单）。
4. **README.md 有 6 处失真**（自称 18 项自测/12 步/30 万 token/方向未做等，全过时），
   一切数字以本文件与 docs/ 实测报告为准。
5. **交接前必须处理安全项**：吊销此前暴露过的 GitHub PAT；轮换 `CTFY_TOKEN` 与模型 Key（§F.2）。

---

## B. 你拿到什么（四层清单）

### B.1 git 基线（HEAD `89739a5`，branch `v3`）

| 内容 | 说明 |
|---|---|
| `src/ctfy_agent/` 29 模块 | 框架本体（§D.1 模块地图）；纯 stdlib（`case_stdlib_only` 强制）|
| `tests/` 34 文件 | 总入口 `smoke.py` + 33 个独立套件（25 个免凭据 / 8 个要真实 `.env`）|
| `config/agent.json` | 7 角色、预算、工具面（§D.2 当前真值）|
| `roles/` 4 文件 | ai/misc 的外包交付格式角色卡（json + system.txt，与 config 零漂移由 smoke 硬断言）|
| `docs/` 74 篇（全部跟踪）| 方案/审计/实测报告/操作单（附录 C 索引）；11-02 补入 6 篇最新报告（§B.4）|
| `Dockerfile` + `.github/workflows/build-agent-image.yml` | 镜像构建（§C.3）；构建期含 ffuf/sqlmap 自检 |
| `tools/` 6 脚本 | container_check / publish_image / verify_image / make_misc_kit / bench_misc_bank / misc_contract_selftest |
| `run.cmd` / `.env.example` / `EDIT-LOCKS.md` / `PROGRESS.md` | 本地入口 / 凭据模板 / 跨会话文件锁协议 / 历史进度存档 |

### B.2 容器镜像（可直接提交的交付物）

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v4`（**提交显式填 `:v4`，别用 `:latest`**）|
| digest | `sha256:4e153aeb41fb0032b1c55cdc4b30dc6ed6596f287ffd079c35a6b32a79fe29ef` |
| 构建 | Actions run `41304971894` @ `ccc6213` 全绿；匿名拉取三层 200（`tools/verify_image.py`）|
| 形态 | 59.2 MB 压缩（上限 400）；uid 10001 非 root；`ENTRYPOINT ["python","-m","ctfy_agent","agent"]`；`ENV LLM_ENABLE_THINKING=0` |
| 内置工具链 | ffuf 2.1.0（Apache-2.0）、SecLists 三表 @758fecf（MIT）、sqlmap 1.10.10（GPLv2+）、**capstone 5.0.3（BSD-3-Clause，v5 起）**，版本/字节数/sha256 三钉死 + 构建期实跑自检；v4 及以前没有 capstone（`elfpeek disasm` 那时退化字节视图）|

### B.3 本地数据（.gitignore 忽略，**不随 git 走，需另行打包转移**）

| 路径 | 内容 | 价值 |
|---|---|---|
| `runs/` | 全部实验工件：`bench-web-report.md`（10-26 基准）【本地-only】、`clusterb-exp-summary.md`、中英 handbook、数百个 `_*.py` 驱动/验尸脚本、全部题目的 transcripts/`*.jsonl`、**`bench_stats.py`（总入口单测依赖它！6 KB）** | 复现一切历史数字 |
| `misc-bank/` | 真实 misc 题库 33 文件 18.1 MB（task/expected/files 附件）| misc 方向回归素材 |
| `inbox/misc-module/` | misc 方向外包交付原件（install.py + patches + 任务书）| 外包流程参考 |
| `.tools/ctfy-src/` | **平台源码**（契约真源，别去网上找文档）| 契约争议仲裁 |
| `dist/` | 两份现成交付包：`all-directions-pack-20261101.zip/.tar.gz`（7 角色卡+工具源码+125 附件，184 文件）、`directions-pack-20261030.zip` | 可直接发给接手人 |
| `.ref/` `.workbuddy/` `_extbank/` `x/` `work/` `js/` 等 | 参考库/会话记忆/实验残留（§F.3 污染表）| 大多可清理 |

### B.4 工作树改动（**2026-11-02 已全部提交**，此处保留内容索引）

**修改（6+1 文件，内容 = 11-02/11-03 实测发现并修复的缺陷）**：

| 文件 | 内容 |
|---|---|
| `.github/workflows/build-agent-image.yml` | **CI 加两步**：总入口单测 + 25 个免凭据套件（修复「CI 不跑测试」缺口）|
| `src/ctfy_agent/platform.py` `solver.py` | 缺陷 A：多问题题提交缺题号回落 `flag` → 422 丢分；修为 verify 记住题号 + `preferred_question_id()`（A/B：CTFARENA-048 未解→correct）|
| `src/ctfy_agent/crew.py` `state.py` | 缺陷 B：部分解被 `get_my_solves` 兜底翻成整题解出；兜底排除 `partial_solve`/`superseded` |
| `src/ctfy_agent/cli.py` `solver.py` `state.py` | 缺陷 C：QA 题提交通道整条不通（`is_qa_only` 映射 + `submit_qa_answer` + `competition_id`）；修后 MCQ 12/12、SECQA-0001 从 57s/33k 变 8.9s/2.1k |
| `tests/smoke.py`（+107 行，纯新增）| 上述修复的回归用例（`case_submit_question_id` / `case_qa_submit` 等）|

**6 份关键报告**（结论被反复引用，**2026-11-02 已入库**）：
`docs/bench-1102-report.md`、`docs/bench-1103-report.md`、`docs/project-completeness-2026-11-02.md`、
`docs/gate2-context-ab-2026-11-01.md`、`docs/p0-calibration-2026-10-31.md`、`docs/eval-mix-live-2026-10-30.md`。

> 处置状态：**已提交**（提交前跑过总入口单测 160/0 与密钥扫描，均通过）。
> 剩余未跟踪项只有调试残留（详见 §B.6），不属于项目源码，未入库是刻意的。

### B.5 本批增补（2026-11-02，P0+P1+P2：跨项目机制融合，**已提交**）

来源：对 `ctf-forge`（另一套 CTF 解题工作台，HI Cyber Con 2026 42/68 题）的机制对标，
评估报告 `docs/merge-ctf-forge-2026-11-02.md`（含互补矩阵、不采纳清单、AGPL 边界）。
**只借鉴机制自行实现，未复制任何源码**（ctf-forge 是 AGPL-3.0）。

| 项 | 内容 | 关掉了什么风险 |
|---|---|---|
| P0-1 实例守卫 | 新 `instance_guard.py` + `solver._recover_instance_slot` + `crew._instance_sweep` + `cli.instance_preflight` + 新 `tools/instance_reap.py` | start 失败时先清残留再重试、**配额剩 1 次绝不重试**；对应 11-03 「21% 的题因 start 5 次上限作废」 |
| P0-2 有界收割 | `tools_local.run_bounded` + `_BoundedSink` + `_kill_tree` | 子进程输出不再全量驻留内存；超时杀**整棵进程树**（旧写法只杀 shell，孙进程继续占 pid） |
| P0-3 墙钟刹车 | `budget.apply_deadline` + `Budget.deadline_at` + `stop_reason_of` + 每步前置 `budget.check()` | 只有 token/calls 拦不住「磨到 session 结束」；现在 `reason=wall_clock` 可单独统计 |
| P1-1 题面指纹 | `Challenge.fingerprint()` + checkpoint 比对 | 题面被改过就丢弃 checkpoint，不拿失效前提续跑 |
| P1-2 合规三件套 | `LICENSE`(MIT) + `THIRD_PARTY_NOTICES.md` + `tools/secret_scan.py` + CI 一步 | 补上 F.2.4 的 LICENSE 缺口与 GPL 分发义务；密钥扫描从"一次性人工"变"每次都跑" |
| P1-3 姿态自检 | `tools/isolation_probe.py` + `container_check --posture` + CI 一步 | 非 root/只读根/断网 下 dry_run 仍 exit 0（v2 曾因 root 被拒收） |

**新增配置键全部默认关**：`deadline_at` / `deadline_minutes`（或 `AGENT_DEADLINE_*`）、
`instance_reap`(false) / `instance_reap_every`(3) / `instance_reap_protect`([]) /
`instance_reap_grace_seconds`(120) / `instance_recovery_grace_seconds`(60)。不配 = 旧行为逐字不变。

**复验**（免令牌免 Docker）：`python tests/smoke.py` → **160/0**；`python tools/secret_scan.py --all` → 干净；
`python tools/isolation_probe.py` → SKIP（无引擎时）；`python tools/container_check.py --local` → 5/5。

| P2 项 | 内容 | 说明 |
|---|---|---|
| P2-1 选题顺序 | `auto._score` + `cli --order {score,id}` | 按 solve 数降序（免费且最准的难度指标）→ 分值 → 难度 → 题号；`--order id` 退回旧行为 |
| P2-2 交接卡 | **新** `src/ctfy_agent/handoff.py` + `crew.solve` 收尾 | 把 summary 里已有的 facts/dead_ends/candidates/tail_action 渲染成五要素 md，写 `<run>/handoff/<cid>.md` |
| P2-4 已判错回显 | `state.note_rejected` + `solver._rejected_hint` | 平台判错时记明文答案（去重、留最近 8 条），下次提交失败回显"这些别再交"（提交预算只有 3 次）|
| P2-3 停机原因枚举化 | **刻意未做** | 现有 reason 串已被 `retry.py::STOP_REASONS`、`agent_main::_STOP_MAP` 与全部历史统计脚本消费，强行收敛会改统计口径，收益只是"好看" |

**P2 效果不理想时怎么退**：`E:\ctfy_backups\20261102-stage1-p0p1\worktree-src.tar.gz` 覆盖回 P0+P1 阶段
（`tar xzf … -C E:/ctfy_project`）；要退到融合之前用 `20261102-pre-merge\`（git bundle + WIP patch + tar）。

### B.6 仓库根目录的调试残留（**刻意未入库**，别误当源码）

11-02 16:00 前后排查具体题目时落在仓库根的一次性产物，与 agent 源码无关：

| 路径 | 是什么 | 为什么留在这 |
|---|---|---|
| `crush`(ELF)、`bundle.js`、`idx.js`、`assets_index.js`、`js/`、`home.html` | 某 web 题的静态资源 | 下载工具以仓库根为 cwd 落下 |
| `message.txt.cz`、`recruitment.eml` | 某 misc 题的题面附件（邮件头含 `X-Pm-Content-Encryption`）| 同上 |
| `agent-artifacts-m55-m56.zip`、`_extbank/` | 历史题包与外部 bank 缓存 | 同上 |
| `%TEMP%/`、`work/`、`x/`、`新建文件夹 (2)/` | 字面量目录名（`%TEMP%` 在 bash 下没被展开）| 测试/手工调试的副作用 |

处置：想清理直接删（都在 `.gitignore` 之外但非源码）；**不要** `git add -A`，会把它们一起提交。
后续若要彻底免疫，把它们加进 `.gitignore`（本轮未改 `.gitignore`，避免影响其他分支）。

### B.7 本批增补（2026-11-02，rev 补强：capstone 进镜像 + relocs/dynamic 视图）

来源：评估 `ctf-forge` 的 rev 能力时发现我们自己的两处短板，详见 `docs/rev-toolbox-plan.md §6`。

**① 缺口 #12 关闭：capstone 终于进镜像。** 这是"承诺与实现不一致"，不是单纯缺功能——
`docs/rev-toolbox-plan.md` 写着"随 v4 重建一起做"，v4 建了但这步漏了；而代码注释、退化提示语、
`config/agent.json` 的 rev/pwn 提示词三处都写着"v4 镜像含 capstone"。结果模型按提示词去调
`view=disasm`，拿到的是字节视图，14 步里白烧 1–2 步。

| 落点 | 改动 |
|---|---|
| `Dockerfile` | 在"删 pip"那段**之前**加 `capstone==5.0.3`：`ARG` 钉版本 + `ARG` 钉 sha256（`a6b7f3ec…1040`，2921407 B），`pip install --require-hashes`，装完跑真实反汇编自检（`push rbp; mov rbp,rsp` 必须解出 2 条） |
| `tools_domain_ext.py` | 三处"v4 起内置"改成"v5 起内置；v4 及以前没装" |
| `THIRD_PARTY_NOTICES.md §3.5` | 新增 capstone 条目（BSD-3-Clause、wheel sha256、义务、以及"缺失时退化路径全绿≠镜像里可用"的提醒） |

**② 新增 `elfpeek view=relocs` / `view=dynamic`（纯 stdlib）。** 之前完全没有重定位和动态段解析，
判断"静态还是动态""哪个 import 对应哪次调用""GOT 在哪""链接哪个 libc"只能靠 `shell` 手写 python。
借鉴 `ctf-forge` 的 `inspect_elf` 字段语义（它 AGPL-3.0，**未复制源码**），实现自研。

- `relocs`：`.rela.plt` 直接给出 `printf → @0x601018` 的 `foo@got` 映射（节内顺序 = PLT stub 顺序）；
  GLOB_DAT 是数据导入；RELATIVE 的 addend = 文件内 vaddr；无重定位表时明示"静态链接"。
- `dynamic`：NEEDED / RUNPATH / RPATH / PLTGOT / INIT / INIT_ARRAY / BIND_NOW（→full RELRO）/
  DT_FLAGS 与 FLAGS_1 位解码（含 PIE 位）。

**③ 顺带修一个 32 位老 bug。** `security()` 的 32 位分支把 `Elf32_Phdr` 解成 (vaddr, paddr, filesz)
却按 (vaddr, filesz, flags) 赋值 → **32 位 ELF 的 NX 恒判 True**（可执行栈也报 NX=True），
`PT_GNU_STACK` 的 flags 从未读到。已改按完整 8 字布局解包。

**验收**：`smoke_rev.py` **19/19**（原 15 + 新增 4：relocs / dynamic / 静态链接提示 / 32 位 NX）、
`smoke_pwn.py` 24/24、`tests/smoke.py` **160/0**；真实 ELF 冒烟（`misc-bank/picture__lsb-2016HCTF/files/1`）
正确列出 3 条 JUMP_SLOT + 1 条 GLOB_DAT + NEEDED=libc.so.6。

**仍未做**：gdb 工具（+30MB，收益不划算）；A3 真机验收（要模型预算）。提示词补 relocs/dynamic 已于
2026-11-02 21:1x 落地，见 §B.8。

### B.8 提示词外包回收 + 落地（2026-11-02 21:1x，**已完成**）

rev / pwn 两段 system 已换成外部交付版 + 我方 3 处微调。全过程证据在
`docs/prompt-pack-2026-11-02/`：`README §八` 是落地记录，`delivered/` 是回收件原样留档
（`agent.json.new` sha256 `d86e6e73…7313a9`）。

| 角色 | 原文 | 交付件 | 落地后 | 硬上限 2600 |
|---|---|---|---|---|
| rev | 2223 | 2573 | **2580** | ✓ |
| pwn | 2235 | 2526 | **2538** | ✓ |

主要变化：① 删掉"v4 镜像含 capstone"的假话，改成条件式"未装 capstone 自动退化字节视图，不是故障，
别 pip install"（v5 建不建都不用回改）；② `view=disasm` 写明 offset 是**文件偏移**（取 `view=sections`
的 offset 列，`symbols.value`/`info.entry` 是 vaddr，直接传必报"超出文件长度"）；③ 补 `relocs`/`dynamic`
两行；④ 新增"何时用工具、何时写脚本"的分工行（单点结论→单视图；全量扫描→一次脚本；同一假设最多一次脚本）；
⑤ 删与 `UNAVAILABLE_RULE` 重复的"DNS 失败=本机无隧道"。

我方在交付件上又改了 3 处（理由见 README §8.2）：rev 硬约束把"别用 shell 引号硬拼"改成
"别在 python3 -c 里 open().write()"（引号守卫只在 Windows 生效，容器里 `-c` 可用，原措辞把
"禁止用 -c 写文件"误写成了"禁止 -c"）；rev/pwn 的 `BIND_NOW=full RELRO` 都加"通常"
（`BIND_NOW` 而无 `PT_GNU_RELRO` 只是 partial）；`07 §八` 的 ±15% 补口径澄清（硬门是脚本的 1900–2600）。

**验收**：`check_prompt_assembly.py` 8/8 × 2 角色；`tests/smoke.py` **160/0**（换入候选跑一次、
落最终版再跑一次，都绿）。**不宣称 solve rate 提升**——评测池 rev 3 道 / pwn 1 道，无法 A/B。

**已知未修（单独一项）**：`【二进制/附件先落盘】`尾段让模型用 `http_request(...)` 和 `unpack_archive`，
但 rev / crypto / pwn 的 `local` 里都没有这两个工具（web 有前者没后者），调了会返回 `[未知工具]`
并计一步（`solver.py:700`）。538 份 transcript 里 rev/pwn 撞这个的记录是 **0 次**（模型靠【0 取料】
那句正确的 `download_challenge_attachment` 绕开了），所以是潜伏缺陷。该段出现在 5 个角色
（web/rev/crypto/pwn/misc），修它要动 4 个角色，留待单独批准。

---

## C. 十分钟上手

### C.1 本地 CLI

```powershell
# 任何 Python 3.12 皆可（stdlib-only）；本机沿用过 DSH 运行时也可
Copy-Item .env.example .env   # 填 CTFY_TOKEN（平台→设置→API 令牌）与模型 Key
.\run.cmd doctor              # MCP 握手 + 工具清单 + 配置自检（平台实测 3.4.6 / 96 工具）
.\run.cmd challenges          # 题单（练习库 id 见 §D.4）
.\run.cmd crew  CTFARENA-001  # 协调者 + 专家跑一题（生产路径）
.\run.cmd demo                # 无令牌无 Key 也能看全链路（假 MCP + 脚本化 LLM）
```

Windows 坑：先 `$env:PYTHONIOENCODING="utf-8"`；**本机有代理时必须 `$env:NO_PROXY="127.0.0.1,localhost"`**
（否则 127.0.0.1 的测试请求被代理变 502，制造假 FAIL【审计 11-02】）。

### C.2 测试

```powershell
python tests/smoke.py          # 总入口（免凭据）
```

- **干净 clone 期望 163 passed / 0 failed**（11-05）：`bench_stats.py` 已挪进 `tools/` 入库（缺口 #11 关闭）；
  同时修掉 `TEMP` 未设导致假 ffuf/sqlmap 脚本路径相对化的 3 个 CI 红（fuzz_functional / sqli_functional / misc_ext）、
  以及 `deadline_clock` 用机器本地时区显示导致的 wall_clock 红（改为固定 UTC+8 显示）。
- 33 个独立套件：25 个免凭据（`smoke_rev 15` / `smoke_pwn 24` / `smoke_crypto 27` / `smoke_webai_gap 12` /
  `smoke_ctxopt` / `smoke_http_*` / `smoke_ai_*` …），8 个要真实 `.env`
  （`smoke_ai_role / brake / clustera / clusterb_crew / ctxopt / p1 / p2 / p21`）——CI 里显式 SKIP。
- 改动后必须保持全绿再提交。

### C.3 容器：契约检查 / 构建 / 发布

```powershell
python tools/container_check.py --local      # 五条契约路径全 exit 0（dry_run/no_task/bad_run_mode/no_creds/no_mcp）
python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v4   # 匿名拉取校验（不启 docker）
python tools/publish_image.py --tag v5       # 发新版（需 GH_TOKEN；见下）
```

- 构建走 **GitHub Actions 手动 dispatch**（`build-agent-image.yml`）：平台审核按 **digest** 钉死，
  **不要覆盖已提交的 tag**，发新版本用新号（v5、v6…）。
- 镜像瘦身与烤入工具链的细节都在 `Dockerfile` 注释里（含「为什么 sqlmap 要归档+raw 双下载」这类实测坑）。

### C.4 环境变量总表

| 组 | 变量 | 说明 |
|---|---|---|
| 平台注入（容器）| `CTFY_PLATFORM_URL` `CTFY_TOKEN` `CTFY_WORK_DIR` `CTFY_EVAL_CHALLENGE_ID` `CTFY_EVAL_COMPETITION_ID` `CTFY_EVAL_MAX_TURNS`（缺失=无预算）`CTFY_EVAL_DRY_RUN` | MCP 地址从 `$CTFY_WORK_DIR/mcp.json` 或 `PLATFORM_URL + /api/v1/mcp/` 拼；**`CTFY_MCP_URL`/`CTFY_TARGET_URL` 在用户镜像里不存在** |
| LLM 网关（容器）| `LLM_FLAVOR`(只认 openai) `LLM_API_KEY` `LLM_BASE_URL` `LLM_MODEL` | **空 key = 没人出资 = 必须 refused，禁止回退自有 key**；角色级 `model` 会被剥掉（单模型契约）|
| 本地（.env）| `DEEPSEEK_API_KEY` `DEEPSEEK_BASE_URL` `DEEPSEEK_MODEL` + 可选 `AGENT_*`（步数/预算/单价…）| 见 `.env.example`；`AGENT_MAX_TOKENS_PER_CHALLENGE` 等全部可覆盖 |

---

## D. 架构与配置真值

### D.1 模块地图（src/ctfy_agent，29 模块 ≈16.2k 行）

| 层 | 模块（行数）| 职责锚点 |
|---|---|---|
| 入口/编排 | `agent_main`(562) `crew`(1599) `solver`(1647) `retry`(432) `auto`(98) `cli`(708) | B1 适配层（task.json→result.json）；协调者+专家；专家主循环+集群-B 编排；集群-B 零耦合编排器（Orchestrator/Blackboard/classify_failure）|
| 平台/传输 | `platform`(524) `mcp_client`(351) `httputil`(104) `llm`(177) | MCP 封装 + `MODEL_TOOLS` 白名单 + 运行期参数修复；JSON/SSE 双兼容；重定向护栏；OpenAI 兼容客户端（超时归一可重试）|
| 状态/预算 | `state`(406) `budget`(203) `ledger`(706) `checkpoint`(160) `progress`(265) `companion`(161) `ctxcompress`(373) `traffic`(484) `artifacts`(164) | 状态机+原子落盘+transcript 锁；token/调用/金额三层刹车+尝试切片；确定性账本（同参数拦截+逐字证据校验）；断点续跑；环境失败断路器；PLAN/REFLECTION 消费；上下文压缩/事实板/滑窗 |
| 工具 | `tools_local`(2481) `tools_domain_ext`(2118) `tools_misc_ext`(796) `tools_webscan`(411) `tools_websec`(426) `tools_websql`(250) | 共 **25 个本地工具**（见 D.2）；全部沙盒锚定、输出 ≤6000 字符（`AGENT_TOOL_TEXT_MAX` 可调）|
| 知识 | `method_cards`(313) | 25 张题型方法卡（AI 21 + 领域 8，含交叉），按 tags 命中注入 |

### D.2 角色与工具面（HEAD 真值；本地工具合计 25 个）

| 角色 | 步数 | shell | 平台工具 | 本地工具 |
|---|---|---|---|---|
| coordinator | 14 | — | read/instance/oracle/**submit** | —（delegate/note/done 内置；提示词 = `crew.COORDINATOR_PROMPT`）|
| web | 14 | ✅ | 10 个直接工具名（题面/附件/提示/start_instance/verify_*）| http_request, http_map, http_burst, fuzz, authdiff, jsrecon, sqli, jwt_forge, shell, write_file, decode_blob（11）|
| ai | 14 | ✅ | 同 web | http_request, http_map, chat, shell, write_file, decode_blob（6）|
| pwn | 14 | ✅ | read/instance/oracle | shell, write_file, elfpeek, hexpeek, pwngen, pwnfind, tcp, tcp_batch, netcheck（9）|
| rev | 14 | ✅ | read/instance/oracle | shell, write_file, elfpeek, hexpeek, tcp, tcp_batch, netcheck（7）|
| crypto | 14 | ✅ | read/instance/oracle | shell, write_file, hexpeek, rsatool, aesblock, prng, tcp, tcp_batch, netcheck（9）|
| misc | 10 | ✅ | read/instance | http_request, http_map, shell, write_file, decode_blob, unpack_archive, hexpeek, tcp, tcp_batch, netcheck（10）|

- 模型：7 角色全部 `DeepSeek-V4.1-Flash-1017`（单模型契约，入口会 pop）；温度 0.2；单轮 4096；
  题预算 1,200,000 token / 100 次调用；上下文 32k；`submit_limit` 3；并发钳制 ≤3（QA#26）。
- 双层门禁：`crew._role_specs`（模型看不看得到）+ `solver._execute`（能不能真执行）；只登记在 `local` 不算数。
- 4 条纪律由 `crew._role_system` 自动追加（PLAN/REFLECTION、逐字 evidence、report 唯一交付、不可达纪律），
  **写提示词时别重复**。

### D.3 编排与实验开关（全部默认关 = 生产是单路串行）

- **集群-B**（`clusterb_k`）：同题 k 次串行 + 温度/策略差异化 + 黑板继承。**10-29 实测定论 k=3 有害**
  （493,615 tok 未解 vs k=1 313,076 tok 解出；切片切断累积上下文），保持默认 k=1【实测 10-29】。
- **集群-A**（`cluster_a_concurrency`）：无状态题 N 路并行。默认关；真机 A/B 留正式赛。
- 其他：缓存折价 `budget_cache_discount`（11-01 A/B：输入成本 −44%）、工具输出上限参数化等，`config.extra` 里都不存在 = 冷代码。

### D.4 平台契约要点（`.tools/ctfy-src` 逐字读出 + 实测）

- `result.json` 核心 = `answers`（按 question id，平台代交）；写完 `exit 0`；`solved` 平台自算。
- `task.json`（schema v2）：`run_mode` ∈ instance/qa/defence/engagement/awd。**当前只支持 instance**
  （非 instance 写空 result + exit 0 = 静默 0 分）；QA 路径已修复并提交（§B.4）。
- 硬约束：`internet_policy=deny`、`tcp_access=tls_sni`、单模型、实例并发 1 / 每题 start 5 次、
  同分先解出者靠前、3 段 session（11/15、11/21、11/22 各 10:00–20:00 CST）、agent 自主并发 ≤3。
- 竞赛 id：TPCTF=`c0d8b4e3dd3b42c095d63d7dfa66431f`；练习库 CTFARENA=`4394a10aa5d24f97a1ec3301ca718a45`、
  CYBENCH=`d35cc057773c44f79eec192b54551327`、AGENTBENCH=`9f613f261b68469b9c4da805197983b2`、
  AGENT-DEFENSE=`d4917609895644e1bcf92b3c9b3bc70f`、SAMPLE=`e4e78ff318dc4844beac13f9ea7409aa`。
- 网关两个实测事实【实测 11-02】：① `enable_thinking:false` **被网关忽略**（reasoning 照常返回计费，
  镜像里那条 ENV 在此网关无效果）；② 缓存真实生效（单题命中 88.8–90.6%，`budget.py` 解析嵌套字段口径正确）。

---

## E. 实测能力（历轮基准，**勿重推**；先看口径再引用数字）

### E.1 时间线

| 轮 | 样本/口径 | 结果 | 报告 |
|---|---|---|---|
| 10-26 | 16 道 web @**150k** token（口径偏悲观）、Kimi-K2.7 | 解出 5/16=31.2%（easy 5/9）；A/B 换 Pro 模型 0/3 vs 0/3、慢 2.27×、覆盖更窄 → **换模型无效** | `runs/bench-web-report.md`【本地-only】|
| 10-31 | 132 题综合（bigbank）| **77 解出 58.3%**；重叠 35 题复测 28.6%→48.6% | `docs/bigbank-report-2026-10-31.md`（已入库）|
| 11-02 | 未解池随机 12 题 @400k | 平台确认 5/11=45%；剔环境阻塞 5/8=63%；能力口径 6/8=75%；均 396s/308k tok；缓存 90.6%；≈¥0.15–0.22/题 | `docs/bench-1102-report.md`（已入库）|
| **11-03** | **六题型 52 题 @默认预算** | **51.9% / 剔阻塞 65.9% / 能力 75.6%**；qa 12/12（均 12s、2.8k tok）；web 7/11=64%；misc 4/6=67%；crypto 2/8=25%（3 题最后一步被预算截断）；rev 2/3；pwn 0/1（池中 3/4 需 VPN，样本无效）；easy 92% / medium 61% / hard 33%；均 277s/276k tok | `docs/bench-1103-report.md`（已入库）|

### E.2 已证伪路线（别再花预算）

1. **换更强模型**：10-26 A/B（0/3 vs 0/3、更慢更窄）。
2. **集群-B k=3**：10-29 归因实验（有害，见 D.3）。
3. **重试轮当默认策略**：18 题 1 解 / 9.05M token 每解（`docs/retry-round-2026-10-31.md`）。
4. **提示词级劝导**（O-D）：无效；**工具级 advice**（O-E 把撞墙请求砍 8 成）与缓存折价才有效。

### E.3 失败主因（10-26 验尸口径，仍适用）

失败题 attempts 全为 0——不是「交错了」而是「没产出候选 flag」：预算耗尽 / 协调者空转 / 实例起不来（上游限流）。
提分方向 = **补方向领域知识与方法卡**（如 web 访问控制绕过 playbook），不是加并行或调参。

---

## F. 已知缺口与风险（交接必读）

### F.1 缺口清单（11-02 审计 10 条 + 本次新增，按风险排序）

| # | 缺口 | 状态（11-03）|
|---|---|---|
| 1 | 只支持 `run_mode=instance`；qa/defence/engagement/awd 会静默 0 分 | 部分：QA 已修并提交（§B.4）；defence/engagement/awd 未动 |
| 2 | CI 不跑任何单测 | **已修并提交**：workflow 加 总入口单测 / 25 个免凭据套件 / 密钥扫描 / 镜像姿态探针 |
| 3 | 33 个测试套件不在总入口 | 部分：25 个免凭据套件接进 CI（同上，已提交）|
| 4 | README 6 处失真（自称 18 项/12 步/30 万 token/方向未做…）| 未修；**以本文件为准** |
| 5 | 本地跑题污染仓库根（`js/`、`work/capture.pcap`、`x/`、`%TEMP%/`）| 未修；本地跑题请显式锚定 run 目录/沙箱 |
| 6 | XBOW 50 题 + 部分 CYBENCH 实例 `*.internal` 需 VPN，本机不可达 | 环境限制；这些库的 0% 不是能力结论 |
| 7 | 网关忽略 `enable_thinking:false`（思考 token 照计费）| 网关侧，代码不可修；预算按含思考估算 |
| 8 | 多问题题提交回落 `flag` → 422 丢分 | **已修并提交**（A/B 验证 CTFARENA-048 转 correct）|
| 9 | 部分解被兜底翻成整题解出（完成度虚高）| **已修并提交** |
| 10 | QA 提交通道不通 | **已修并提交**（MCQ 12/12）|
| **11** | ~~总入口 `case_bench_stats` 依赖未入库的 `runs/bench_stats.py` → 干净 clone 必红 1 条~~【11-05 已修】| ✅ 已挪进 `tools/bench_stats.py` 并改 import；干净 clone 163/0 |
| 12 | capstone 未进镜像（`elfpeek disasm` 退化字节视图）| **已修（2026-11-02）**：Dockerfile 已加 `capstone==5.0.3`（sha256 钉死 + 构建期反汇编自检，位于删 pip 之前），v5 建出来即生效；v4 仍无（§B.7）|

### F.2 安全与合规（交接前逐项打勾）

1. **吊销此前暴露过的 GitHub PAT**（遗留未处理）。
2. **轮换凭据**：`CTFY_TOKEN`（平台令牌 30 天有效可吊销）与模型 Key；`.env` 不进 git 但在本地——**打包转移时剔除**。
3. 密钥扫描：HEAD 全树无 `sk-/ghp_/gho_/AKIA/xoxb-` 命中【实测 11-03】；`.env.example` 全占位符。
4. **无 LICENSE 文件**：接手方需自定代码许可；镜像分发的第三方义务——ffuf Apache-2.0 / SecLists MIT / sqlmap **GPLv2+**（GPL 传播需提供对应源码，上游 pin 见 Dockerfile）；`tools_local.http_map` 的 AI 组件指纹来源为 MIT 技能卡（ctfy-ai-infra）。
5. GHCR 包随仓库转移时注意：镜像在 `ghcr.io/yzqcyl/` 名下，换主需重推并重过平台审核（digest 会变）。

### F.3 仓库卫生（可清理清单）

根目录未跟踪杂物（**多为本地跑题泄漏，见缺口 #5**）：`js/`、`bundle.js`、`assets_index.js`、`idx.js`、`home.html`、
`work/capture.pcap`、`x/`、`%TEMP%/`、`crush`、`message.txt.cz`、`recruitment.eml`、`agent-artifacts-m55-m56.zip`（853 KB 构建产物）、
`新建文件夹 (2)/`（他人 6.8 MB tarball）。→ 建议交接打包前整批删除（先扫一眼无独有数据）。

---

## G. 交接操作清单（按序执行）

1. ☐ 转移 git 仓库访问权（v3 为当前线）；`git clone` 后核对 HEAD `773892d`。
2. ☑ **处置未提交 WIP**（§B.4）：2026-11-02 已 review 并提交（`773892d`；160/0 与密钥扫描均通过）。
3. ✅ 已把 `runs/bench_stats.py` 挪进 `tools/` 修掉缺口 #11，干净 clone 单测 163/0。
4. ☐ 本地起环境：`.env` 填新令牌 → `run.cmd doctor` → `python tests/smoke.py`（记得 NO_PROXY）。
5. ☐ **吊销旧 GitHub PAT、轮换 CTFY_TOKEN/模型 Key**（§F.2）。
6. ☐ 拿走本地数据：`runs/`（transcripts+报告+脚本）、`misc-bank/`、`inbox/`、`.tools/ctfy-src/`、`dist/` 两个交付包。
7. ☐ 决定 LICENSE 与第三方合规口径（§F.2.4）。
8. ☐ （可选）清理根目录杂物（§F.3），重写 README（缺口 #4）。
9. ☐ 平台提交镜像：按 `docs/submit-image-runbook.md` 走浏览器 4 步（显式 `:v4`）；或先重建 v5
   （带上 WIP 修复 + capstone，走手动 dispatch workflow），再提交 v5。
10. ☐ 通读 §H 禁忌与附录 A/B（工具箱与补短板记录），再动提示词——**改提示词要单独走审批**（原团队规矩）。

---

## H. 经验与禁忌（别重做 / 别踩）

1. 不要重新推导 §E 的基准数字与 A/B 结论——直接引用，报告里有勘误记录。
2. 不要用偏离默认的预算口径跑批再下「模型不行」结论（150k 那轮就是这么偏的）。
3. 大规模跑题**先确认 0 实例**再开跑（复用死实例 = 白烧 token；10-26 有 156k 的教训）；
   也别为探配额反复启停（每题 5 次上限，触上限当天该题作废）。
4. 统计一律按**结果行**（transcripts jsonl 每行一事件），不按文本出现次数（拦截文案被回显会翻倍，10-26 实测 35→23）。
5. 不要提交 `:latest` / 覆盖已审核 tag；平台按 digest 钉死。
6. **不要擅自 commit / revert 他人未提交改动**（B.4 的 WIP 已于 2026-11-02 提交，此后新增改动仍按此条）；改共享文件前查 `EDIT-LOCKS.md` 登记锁。
7. 不要断言 CTFARENA-035 的凭据路线是死路（~1202 次全 401 只是候选覆盖不足，tags 指明方向对）。
8. 未经批准不要改角色提示词内容（web/crypto/rev 的调教是资产）；改 config 的 roles 必须同步 `roles/*.json`
   （零漂移契约有 smoke 硬断言）。
9. 干净 clone 单测已 163/0（缺口 #11 关闭）；另注意 `tests/smoke_http_cache.py` 在 Windows 上偶发 WinError 10053（连接复用），Linux CI 不复现。
10. 本地跑题务必锚定沙箱/run 目录，防根目录污染与多题串数据（§F.3 就是这么来的）。

**与原作者协作偏好**（继续有效）：只认实验数据，写数字先核实；方案先批准再实施，提示词改动单独批准；
密钥打码；在意成本——实验设预算上限、报告给「钱花在哪」的结构化拆解；回答中文、表格化、标口径与置信度。

---

## 附录 A. rev / pwn / crypto 工具箱（2026-10-30，已提交）

| 方向 | 新增能力（全部挂既有注册表，零 crew 改动）|
|---|---|
| rev | `elfpeek` 新视图：disasm（capstone，v5 起镜像内置）/ bytecode（.pyc marshal+dis+常量池、PyInstaller CArchive/PYZ 提取）/ gadgets v2 / strings utf16 / info 补入口段 / **relocs（PLT-GOT 映射、静态链接判定）** / **dynamic（NEEDED/RUNPATH/BIND_NOW/PIE 位）** |
| pwn | `pwngen`（cyclic De Bruijn / build 拼装 hex·raw·pad·int 小端·落盘）、`pwnfind`（回显 3~8 字节反查偏移、倒序、hex/文本）、`elfpeek view=lookup`（符号偏移+相近建议+ret2libc 提示）|
| crypto | `rsatool`（small_e/common_modulus/wiener/fermat/pollard_pm1/pollard_rho/hastad/crt/factor）、`aesblock`（纯 stdlib AES-128/192/256 ecb/cbc/ctr/xor/bitflip/pkcs7，FIPS-197+NIST 向量过）、`prng`（MT19937 状态恢复预测、LCG 参数恢复）|

第二轮审计修 10 处（`[toolbox-fix 2026-10-30]` 标识）；专项套件 `smoke_rev 15 / smoke_pwn 24 / smoke_crypto 27`。

## 附录 B. web / AI 补短板（M6，2026-10-30，已提交）

AI：`verify_exploit` 自校验、`allow_shell=true`+shell/decode_blob、步数 12→14、提示词补【工具面】。
Web：侦察预算（http_map ≤2 次/轮、侦察 ≤1/3 步数）、evidence 条款细化（20–200 字符连续原文）、
SQLi/上传靶场方法卡、multipart 原生支持（O-F，`files/form` 参数）。
本地无法修复（登记）：003/004/012 平台占位或 VPN-only；capstone 已随 v5 进 Dockerfile（缺口 #12 关闭）。

## 附录 C. docs/ 索引（分类；★=接手先读的高价值篇目，均已入库）

- **契约/规则**：`report-ctfy-rules.md`（平台规则调研）、`b1-adapter-plan.md`（容器适配+平台源码硬事实）、`submit-image-runbook.md` + `platform-submit-opsheet.md`（提交操作单）、`OPENING.txt`
- **方案**：`plan-v1`（总纲）、`plan-concurrency`（集群 A/B）、`plan-cluster-a`、`plan-misc`、`extending-agent.md`（**新方向接入规范**）、`authoring-*`/`why-authoring`（出题线）
- **审计**：`security-audit/fixes`、`audit-2026-10-25`、`audit-agent-2026-10-30`、`toolbox-audit-2026-10-30`、`audit-budget-exhausted-2026-10-31`、★`project-completeness-2026-11-02`（**全项目实跑审计，接手先读**）
- **实测报告**：`bigbank-report-2026-10-31`（+CSV）、`cybench-retest-2026-11-01`、`clusterb-p1-result`、★`bench-1102-report`、★`bench-1103-report`、★`eval-mix-live-2026-10-30`、★`gate2-context-ab-2026-11-01`、★`p0-calibration-2026-10-31`
- **工具/提示词**：`domain-tools.md`、`misc-handover.md`、`misc-module-brief.md`、`web-playbook-v3.md`、`web-ai-gap-plan.md`、`ai-plan.md`、`note-zhipu-model-choice.md`
- 另有 `runs/bench-web-report.md`、`runs/handbook-{zh-CN,en}.md`【本地-only】

## 附录 D. 镜像版本史（digest 钉死，勿覆盖）

| tag | digest（前 12 位）| 基线 | 状态 |
|---|---|---|---|
| v4 | `sha256:4e153aeb41fb` | `ccc6213`（run 41304971894）| **当前提交目标**；+ctxopt/方法卡/工具链/`LLM_ENABLE_THINKING=0` |
| v3 | `sha256:e658b9f6a466` | `5a7f8c5`（run 40837543846）| 历史；集群-B 接线 + web 工具链 |
| v2.1 | `sha256:6406286f857c` | `429d280` | 历史；非 root 修复 |
| v2 | `sha256:73b39430873a` | `21488518` | 历史；**曾被协议检查拒（root）**，教训保留 |
| v1 | `sha256:a68ebe6e…` | — | 最早留档 |

---

*本版由交接线重写（2026-11-03），替代旧「给新对话 agent」内部版；旧版的有效内容（契约、基准、禁忌、附录）已全部并入或更新。数字若有出入，以本文件标注的【实测/审计】口径为准。*
