# 提交 agent 镜像 — 最终状态与操作单

## ✅ v7.3 已构建、匿名可拉取（2026-11-12）——**提交用这个**

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v7.3` |
| digest（index） | `sha256:7afd38ed80170196c78be563072447a10f25aa21a4fbf0364716b8f2a6c14bde` |
| digest（amd64） | `sha256:c356c0f7fc7280b6ed99a71f4b77c1a8df6203ef893760db4c47e5e2d8aaac36` |
| 构建 | GitHub Actions run `43215294520`（branch `ctfarena/fix`、sha `e454453`）→ 12 步全绿 |
| 本次变更（相对 v7.2） | ① 补装 **z3-solver 5.4.1**（v7.2 漏装 → 官方镜像里 `z3solve` 必失败）；② apt 装 **gdb 16.3 + patchelf 0.18**（PWN 调试短板）；③ 新增 **`lattice_math.py`**（LLL + Coppersmith 单变量小根）并接线 `rsatool op=lll/coppersmith`（CRYPTO 短板）；④ 修 `smoke_libs` 在无 `tools/pylibs` 的干净检出里误判失败（CI 专属坑） |
| 单测 | `tests/smoke.py` 180 passed / 0 failed；独立套件（含 `smoke_lattice` 30 项、`smoke_libs`、`smoke_secret_scan` 9 项）全绿；`secret_scan --all` 扫 211 文件干净 |
| 体积/层数 | 19 层，251.7 MB（平台上限 400 MB） |
| 运行契约 | Entrypoint `python -m ctfy_agent agent` / `User=app`（非 root）/ `PYTHONPATH=/ctfy/src` / `WorkingDir=/ctfy` |
| 匿名拉取 | ✅ token / manifest / config 全 200；`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v7.3` → "平台可匿名拉取，且运行契约符合 CTFY-0001 v2" |

> ### ✅ v7.3 构建必带清单（全部已在 run `43215294520` 日志中核验）
> 1. **z3-solver**：日志 `z3 selftest ok 5.4.1`；
> 2. **gdb + patchelf**：日志 `GNU gdb (Debian 16.3-1) 16.3` / `patchelf 0.18.0`；
> 3. **格攻击**：`smoke_lattice.py` 在 CI 独立套件步骤 PASS（LLL+Coppersmith 真跑过，不只是 import）；
> 4. **外挂库**：日志 `ext-libs selftest ok: Crypto 4.4.0 | gmpy2 2.3.2 | sympy 1.14.0 | pwntools 4.15.0`；
> 5. **体积**：实测 **251.7 MB**（原估 230~240，gdb 调试符号比预估大），门禁 400 MB 仍有余量。

> ### 🚫 范围红线（用户 2026-11-12 定，之后每次重建都必须遵守）
> 提交镜像**不得打包**两类内容：
> 1. **可视化界面**：`src/ctfy_agent/gui/` 下的 `app.py / worker.py / settings.py / limits.py / onboard.py`；
> 2. **多平台连接适配器**：`gui/platform_adapter.py`、`gui/platform_worker.py`、`gui/profiles.py`、`gui/toolmap.py`（「切到其他平台」的档案与适配器）。
>
> ✅ **允许进镜像**：本机 Windows↔WSL 的**跨平台库连接能力**（`pylibs.py` 的 `CTFY_PYLIBS`/`CTFY_PYLIBS_STRICT` 路径搜索、`tools/fetch_pylibs.py` 取库脚本、Linux 侧库目录约定）。镜像本身是 Linux，这些能力不外连、不依赖 host，属于允许保留的通用能力（用户 2026-11-12 确认「2 可以进」）。
>
> 当前 v7.3 **已满足**：`.dockerignore` L22 整包排除 `src/ctfy_agent/gui/`，`Dockerfile` L277 有构建期断言（`find_spec('ctfy_agent.gui') is None`，误加回即构建失败）。`tools/` 与 `tests/` 本就不随 `COPY src/` 进入镜像，无需额外处理。v7.3 沿用同一红线（未改 `.dockerignore` 与断言）。

---

## 历史：v7.2 已构建、匿名可拉取（2026-11-11）——已被 v7.3 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v7.2` |
| digest（index） | `sha256:060a8fdefbe85d70a35f95f77a9075d3da6f5019a35f4b2085032baa04d0317d` |
| digest（amd64） | `sha256:a8ca4635a0ebcb38df21a1fb9316ec5192414c9a6ffd65aa8318e0cc42fe5f99` |
| 构建 | GitHub Actions run `42982226027`（branch `ctfarena/fix`、sha `a39f3e9`）→ 14 步全绿 |
| 已知缺口（v7.3 修） | 未装 z3-solver（`z3solve` 恒报缺库）；无 gdb/patchelf；无格攻击工具 |
| 体积/层数 | 19 层，197.7 MB |

---

## 历史：v6.3 已构建、匿名可拉取、平台 admission=admitted（2026-11-04）——已被 v7.2 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v6.3` |
| digest | `sha256:c64388d2227444f0b817fab53c9b5c41955ad8b4dccddc07806570e3c08da50b` |
| 构建 | GitHub Actions run `41742653277`（branch `v3`、sha `87fb0e7`）→ 全步 success |
| 平台校验 | harness `d0ef391d-…/ctfy-agent-pub-v6.3`：conformance `passed`、probe `passed` + `hardened=true`、`admission.state=admitted`，digest 与上表一致 |
| 本次变更（v6.2 SAMPLE 实测 10/15 后逐题归因） | ① **多问题答案逐题留档**：`Challenge.answers_by_qid` + `mark_submitted(question_id=…)`，`result.json.answers/solved` 覆盖全部已通过题号（旧实现只写一个 `default_question_id`，SAMPLE-001 把 slug 当 flag 交）；② **submit 工具补 `question_id` 参数** + **提交节流**：同题号 ≥2 次拦截、通过一问后必须先 delegate 拿新答案、已通过的值禁止投给别的题号（实测 001 提交 70 次 / 009 提交 44 次）；③ **预算折价口径**：开 `budget_cache_discount`（缓存命中按 1/10 计，800k ≈ 3.5M 原始 token），`max_llm_calls` 160→200，`result.json` 仍报真实 token（另记 `prompt_tokens_billed`），`cut_off` 不再写死 usage/tokens=0；④ **pwn 黑盒纪律**：先穷尽 attachments/实例附件/hints，真黑盒只准 2 步结构探测，禁止千词级命令名爆破（003/012 实测 4000+ 词零命中且连接约 80 条即断） |
| 单测 | `tests/smoke.py` **170 passed / 0 failed**（新增 `v63_multi_answers` / `v63_submit_guards` / `v63_budget_and_pwn`）；全部 34 个 `tests/smoke_*.py` 全绿 |
| 体积/层数 | 17 层，匿名传输 **62.7 MB** |
| 运行契约 | Entrypoint `python -m ctfy_agent agent` / `User=app` / `PYTHONPATH=/ctfy/src` / `WorkingDir=/ctfy` |
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v6.3`）|
| SAMPLE 复跑 | campaign `b38c86c0d8e24643adcf9291ba75179c`（15 题，harness v6.3 × DeepSeek V4 Flash）|

### v6.2 实测基线（对照用，2026-11-03）

15 题 → **panel 口径 10/15**（平台 summary 计 9/15 已解 + 007 在补跑批次解出）：
解出 002/004/005/006/007/008/010/011/013/014；未解 001(slug✓/flag✗)、003(pwn 黑盒)、009(flag✓/root✗ checker)、012(pwn 黑盒)、015(平台 503 无可用端口段)。

---

## 历史：v6.2 已构建（2026-11-03）——已被 v6.3 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v6.2` |
| digest | `sha256:183e0d1ec8d28c7305ce9f3d79d02816133f31d3b79bcb8f7335dd398e26aab9` |
| 构建 | GitHub Actions run `41656534124`（branch `v3`、sha `2b381c1`）→ **14 步全绿** |
| 本次变更 | ① 单题预算统一 **800k token**（agent.json / config.DEFAULTS / `_build_config` 三处此前打架，生效值本是硬写的 800k）；② **提高步数**：协调者 14→**24 轮**、web/ai/rev/crypto/pwn 14→**20 步**、misc 10→**16 步**；③ 提示词只改步数行（总步数+各阶段分配），方法/工具面/流程/安全底线未动；④ `roles/*.json`、`roles/*.system.txt`、`config/agent.json` 三处同步 |
| 依据 | 官方规则提取件确认**平台不规定步数上限**：`docs/archive/report-ctfy-rules.md:143/227/342`（`task.json.max_turns` 是预算，`null`=不限）、`qa-official-2026-10-28.md:20`（只有「并发 ≤ 3」）。v6.1 实测瓶颈是自定的 14 轮（SAMPLE-011 在 24 turns 撞 `step_limit`，token 仅用 22.9k/800k）|
| 单测 | `tests/smoke.py` **167 passed / 0 failed**；全部 35 个 `tests/smoke_*.py` 全绿（`PYTHONPATH=src`）|
| 体积/层数 | 17 层，匿名传输 **62.7 MB** |
| 运行契约 | Entrypoint `python -m ctfy_agent agent` / `User=app` / `PYTHONPATH=/ctfy/src` / `WorkingDir=/ctfy` |
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v6.2`）|

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v6.1` |
| digest | `sha256:66bb6119bfe696cc163fdc8be358c5734c0c4d0f0dd0f231b30e26ffc673b7d4` |
| 构建 | GitHub Actions run `41645836289`（branch `v3`、sha `962017d`）→ **14 步全绿** |
| 本次唯一变更 | 容器入口放开 `run_mode=qa`：qa 置 `qa_mode=True` + `auto_start_instance=False`，落库 `is_qa_only=True` → 提交走 `submit_qa_answer`（此前 qa 一律 `refused`，SAMPLE 的问答/静态分析题直接 0 分）；`defence/engagement/awd` 仍显式拒跑 |
| 单测 | `tests/smoke.py` 全绿；新增 `tests/smoke_agent_qa.py` 4/4 |
| 体积/层数 | 17 层，匿名传输 **62.7 MB** |
| 运行契约 | Entrypoint `python -m ctfy_agent agent` / `User=app` / `PYTHONPATH=/ctfy/src` / `WorkingDir=/ctfy` |
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v6.1`）|

### 下一步：跑 SAMPLE（浏览器，人工）

1. 平台「配置本次测评」→ 镜像地址填 `ghcr.io/yzqcyl/ctfy-agent-pub:v6.1`（**显式 tag，别填 latest**）→ 点「重新检查」
2. 模型/provider/密钥沿用上次（DeepSeek V4 Flash + OpenAI + 自有 key）
3. 提交审核 → 通过后「测评我的 Agent」→ 选 SAMPLE（questions/suite）跑
4. 结果观察点：SAMPLE-005（ZIP Bomb Inspector）等问答/静态分析题**不应再出现** `stop_reason=refused` / `unsupported run_mode='qa'`；`result.json` 应有非空 `answers`。web/pwn/rev 等实例题行为不变

---

## 历史：`v5` 已构建（2026-11-05）——已被 v6.1 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v5` |
| digest | `sha256:037edd2d607a2bd6b02b1949476d79520189ffbeff73a4706cda04d6b984796f` |
| 构建 | GitHub Actions run `41597493596`（branch `v3`、sha `4fae989`）→ **14 步全绿** |
| 代码基线 | commit `4fae989`：v4 之后 8 个提交（capstone 入镜像、instance_guard、handoff、进度/预算/工具链审计修复、`tests/smoke_rev.py`）|
| 思考模式 | ✅ `LLM_ENABLE_THINKING=0` 在镜像 `Env`（第 11 条）|
| rev/pwn 工具箱 | ✅ capstone 5.0.3（wheel sha256 钉死，构建期 `capstone selftest ok: 5.0.3 2 insns`）|
| 非 root | ✅ `User="app"`（uid 10001、`HOME=/home/app`、`PYTHONPATH=/ctfy/src`）|
| 入口 | `Entrypoint=["python","-m","ctfy_agent","agent"]`、`WorkingDir=/ctfy`、`Cmd=None` |
| 体积 | 17 层，匿名传输 **62.7 MB**（平台上限 400 MB）|
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v5`）|
| 平台姿态自检 | ✅ `non_root / source_readonly / work_writable / egress_blocked / no_dotenv / uid / dry_run_exit_zero / dry_run_wrote_result` **8/8 PASS**（`tools/isolation_probe.py`，CI 内跑）|
| 单测基线 | `tests/smoke.py` **163 passed / 0 failed**（干净 clone 口径；另有 33 个独立套件在 CI 全跑）|
| CI 门禁 | 密钥扫描 `tools/secret_scan.py --all` + 独立套件 + B1 契约 + 平台姿态，四道全绿 |

### 本次为让 CI 全绿修掉的 7 个缺陷（都是"只会在干净 clone / UTC / 容器姿态下暴露"）

1. `tests/smoke.py` 用 `os.environ["TEMP"]` 取临时根 → Linux 无该变量，退化成相对路径，假 ffuf/sqlmap 在 sandbox 里路径拼错（3 个用例红）→ 改用 `tempfile.gettempdir()`。
2. `budget.deadline_clock` 用 `time.localtime` → CI(UTC) 把 20:00+08:00 显示成 12:00 → 固定按 UTC+8 显示（容器与选手一致）。
3. `runs/bench_stats.py` 不在 git 里（缺口 #11）→ 挪进 `tools/bench_stats.py`。
4. `tools/isolation_probe.py` 对带 ENTRYPOINT 的镜像直接追加 `python -I -c ...` → 被当成 `agent` 子命令参数 → 改 `--entrypoint python`。
5. 同一探针用 `mkdtemp`（0700）挂 `/ctfy/work`，容器内 `app`(uid 10001) 写不进 → 放宽 0o777 模拟平台 staging 目录。

> 4、5 是"探针自身的 bug"，不是镜像问题：修完后 8 项姿态检查全 PASS，说明镜像在 **只读根 + 断网 + 非 root** 下能正常写出 `result.json` 并 `exit 0`。

### 下一步（浏览器，人工）

> **逐屏操作单（含预期结果与失败排查）：[docs/archive/platform-submit-opsheet.md](platform-submit-opsheet.md)**

1. 平台「配置本次测评」→ 镜像地址填 `ghcr.io/yzqcyl/ctfy-agent-pub:v5`
2. 点「重新检查」→ 注册 / 协议检查 / 试运行 / 审核 全绿
3. 提交审核 → 通过后用「测评我的 Agent」跑一遍

---

## 历史：`v4` 已构建、匿名可拉取（2026-11-01）——已被 v5 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v4` |
| digest | `sha256:4e153aeb41fb0032b1c55cdc4b30dc6ed6596f287ffd079c35a6b32a79fe29ef` |
| 构建 | GitHub Actions run `41304971894`（branch `v3`、sha `ccc6213`）→ 全绿 |
| 代码基线 | commit `ccc6213`：v3 之后 38 个提交（工具链扩展：domain_ext +1562 行 / local +772 行、方法卡、progress、提示词与附件落盘、上下文优化 ctxopt）|
| 思考模式 | ✅ `LLM_ENABLE_THINKING=0` 已写入镜像 `Env`（与本地实测同口径，防 reasoning 烧穿 max_tokens）|
| 非 root | ✅ `User="app"`（uid 10001、`HOME=/home/app`、`PYTHONPATH=/ctfy/src`）|
| 入口 | `Entrypoint=["python","-m","ctfy_agent","agent"]`、`WorkingDir=/ctfy`、`Cmd=None` |
| 体积 | 16 层，匿名传输 59.2 MB（解压 ~150 MB，平台上限 400 MB）|
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v4`）|
| 单测基线 | `tests/smoke.py` **152 passed / 0 failed**（含 ctxopt 8 条）|

### 历史：v4 当时的下一步（已被 v5 取代）

---

## 历史：`v3` 已构建、匿名可拉取（2026-10-29）——已被 v4/v5 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v3` |
| digest | `sha256:e658b9f6a466209813d1278bfa379b38e0275cf5fe0f0d8f41459f423bbf8a56` |
| 构建 | GitHub Actions run `40837543846`（branch `v3`、sha `5a7f8c5`）→ 全绿 |
| 代码基线 | commit `5a7f8c5`：集群-B 接线（`clusterb_k` 默认不启用）+ LLM 读超时修复 + P2 web 工具链（ffuf / SecLists / sqlmap） |
| 非 root | ✅ `Config.User="app"`（uid 10001、`HOME=/home/app`、`PYTHONPATH=/ctfy/src`） |
| 入口 | `Entrypoint=["python","-m","ctfy_agent","agent"]`、`WorkingDir=/ctfy`、`Cmd=None` |
| 体积 | 16 层，匿名传输 59.1 MB（解压 149.4 MB，平台上限 400 MB） |
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v3`） |
| CI 契约 | ✅ 五条路径全 `exit 0`（dry_run / no_task / bad_run_mode / no_creds / no_mcp） |
| 构建内自检 | ✅ ffuf（`ffuf selftest ok`）、sqlmap（产出 injectable 判定）、302 跳转探针（`decoy_hits=0`） |
| `latest` | 历史值；现 `latest` 已随 v5 构建指向 v5 —— 提交请**显式填 `:v5`** |

### 与 v2.1 的差异（净改进，无行为回归）

- `config/agent.json` 不加 `clusterb_k` → k=1 路径与 v2.1 逐字等价（10-29 实测：k=3@511k 花 493,615 tok 未解出，k=1@511k 用 313,076 tok 达成，见 `docs/archive/clusterb-p1-result.md`）。
- 新增 LLM 读超时兜底：单次模型挂起不再让整题报废（`llm.py` 把裸 `TimeoutError` 归一为可重试 `LLMError` + 1 次重试；容器默认读超时 120→240s）。
- 新增 web 工具链：ffuf 2.1.0 + SecLists 三张表 + sqlmap 1.10.10（版本/字节数/sha256 全钉死，构建期实测跑通）。

### 历史：v3 当时的下一步（已被 v4 取代）

### ℹ️ 思考模式：v4 起内置关闭（2026-11-01 拍板，覆盖 10-30 的「不关」决策）

**现行决策（2026-11-01）**：v4 镜像 `ENV LLM_ENABLE_THINKING=0`，与本地全部实测同口径。
- 直接效果：请求带 `enable_thinking: false`，避免 reasoning 把 `max_tokens=8192` 烧穿 → `content` 空 / `finish_reason=length`。
- 适用：11-15 初赛（自备百炼 key，OpenAI 兼容端点认这个字段）。
- **回滚方式**：11-21 正式赛若换官方智谱且该字段被拒（智谱关思考是 `thinking:{"type":"disabled"}`，与百炼字段不同），删掉 Dockerfile 里那行 `ENV LLM_ENABLE_THINKING=0` 重建即可，代码无需改。
- 历史决策（2026-10-30，已被覆盖）：曾拍板「思考不关、保持端点默认，v4 也不做关闭机制」，依据是 GLM-5.3 系强制思考、关闭收益有限。现按 11-01 用户拍板改为关闭，先保证与实测数据可比。

**观测点（出现再议）**：评估/实跑 `result.json` 出现空回答、`halted`，且转录显示 `finish_reason=length` → 再议单调用 `max_tokens` 余量（8192→16384）。

---

## 历史：`v2.1`（非 root 修复版，已被 v3 取代）

## ⚠️ 2026-10-27 平台实测：`:v2` 被「协议检查」拒绝（以 root 运行）

平台「注册并检查」对 `ghcr.io/yzqcyl/ctfy-agent-pub:v2` 的判定：

| 检查项 | 结果 |
|---|---|
| 已注册为 `ctfy-agent-pub` | ✅ |
| 协议检查 | ❌ 以 root 运行——用户镜像必须以非 root 用户运行 |
| 试运行 | ❌ 同上 |
| 审核 | ⚠️ 等待人工审核（修复前不可运行） |

**根因**：`Dockerfile` 没有 `USER` 指令（`Config.User=""` → docker 判 root）。
**修复**（已入库）：Dockerfile 末尾
```dockerfile
RUN useradd --create-home --uid 10001 --shell /bin/bash app
ENV HOME=/home/app
USER app
```
运行期不需要 root：平台把 `/ctfy/work` 以 0o777 的 staging 目录 `docker cp` 进容器
（`providers/docker_provider/harness.py`），运行期状态全部写在 `/ctfy/work/runs`，`/ctfy` 只读即可。

**重建 + 重新检查**（不要覆盖 `:v2`）：
1. 构建新 tag：`python tools/publish_image.py --tag v2.1`（或本地 `docker build` 后 push）
2. 页面「配置本次测评」→ 镜像地址改成 `ghcr.io/yzqcyl/ctfy-agent-pub:v2.1` → 点「重新检查」
3. 五项全绿（注册 / 协议检查 / 试运行 / 审核）后再提交审核

## 历史：`v2.1` 已构建并校验（2026-10-27 23:37 +08:00）——已被 v3 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v2.1` |
| digest | `sha256:6406286f857e5cbe6efd36645e1347a3f8c8f9b94efda179c03904cf9fe151bb` |
| 构建 | GitHub Actions run `40532627504`（branch `master`、sha `429d280`）→ 全绿 |
| 非 root | ✅ `Config.User="app"`（uid 10001，`HOME=/home/app`、`PYTHONPATH=/ctfy/src`） |
| 入口 | `Entrypoint=["python","-m","ctfy_agent","agent"]`、`WorkingDir=/ctfy` |
| 体积 | 10 层，压缩后 47.4 MB |
| 契约 | CI 内五条路径全部 `exit 0`（dry_run / no_task / bad_run_mode / no_creds / no_mcp） |
| 代码基线 | **与已实测的 v2 完全一致**，仅增加非 root 运行（master 线最小改动） |

校验方式（不启 docker daemon，直接查 registry API）：
`python runs/_ghcr_check.py yzqcyl/ctfy-agent-pub v2.1`

**下一步（人工）**：平台「配置本次测评」→ 镜像地址改为 `ghcr.io/yzqcyl/ctfy-agent-pub:v2.1` → 点「重新检查」→ 五项全绿后提交审核。

## 历史：`v2` 已冻结（已被 v3 取代，勿再提交）

```
ghcr.io/yzqcyl/ctfy-agent-pub:v2
```

| 项 | 值 |
|---|---|
| **digest** | `sha256:73b39430873a1bf3d213e2881722a7aa69d78d1a24932790e932043306cf09e5` |
| 构建源码 | commit `21488518f1770cd77b7a79916245a91a3b859e90`（Actions run #6，`head_sha` 已核对一致） |
| 平台视角拉取 | 完全匿名可拉取（token/manifest/config 三层全 200，`tools/verify_image.py` 实测） |
| 体积 | 9 层，传输 47.2 MB（解压 126.5 MB；平台 guideline 8 GiB） |
| 架构 | amd64 / linux（与 worker 一致） |
| Entrypoint | `["python", "-m", "ctfy_agent", "agent"]`，Cmd 为空，不依赖 WORKDIR |
| 平台合规判定 | **passed**（平台自己的 `harness_conformance`：0 finding） |
| 镜像内容核验 | ✅ 层内确认含 `http_burst` / `jwt_forge` / `ctxcompress` / `follow_redirect` / `config/agent.json` |

### v2 相比 v1 新增能力

- `jwt_forge`：none / HS256-512 / RS256（内置测试密钥 + jwk 头注入），secret 永不回显、只进账本指纹
- `http_request` P0：`elapsed_ms`、`OPTIONS`、`follow_redirect=false`（看原始 302+Location）、`body_b64`
- `http_burst`：并发竞态（Barrier 同时打）+ `{v}` 批量枚举，≤24 请求
- 上下文压缩 `ctxcompress`：工具结果瘦身 + 事实常驻 + 两级滑窗
- 审计修复：burst 缓存失效、follow_redirect 绕过缓存、参数类型护栏、跨 host cookie 防泄漏

## 📦 v1 归档（旧代码，不要提交）

```
ghcr.io/yzqcyl/ctfy-agent-pub:v1
digest sha256:a68ebe6e619b95191cc5168c9e35f2e92788970cfca823093ba8d1100d66ec4b
```

v1 未被移动（已核验 digest 不变），但它**不含**上面的新工具。保留仅为留档。

> ⚠️ 历史段落，已失效：`:latest` 现在指向 **v3**（两个包都是）。提交时请显式填 `:v3`，别用 `latest`。

## 平台页面操作单（4 步，必须浏览器）

> 为什么不能我代做：`/eval/*` 这组路由要求 `user` 类型 token，而 API tokens 页只发
> `fine_grained`；铸造 `user`/`agent` token 本身又要 `user` 会话。平台刻意堵死了
> 细粒度 token 注册镜像这条路（避免泄露的 play token 提权），没有 API 绕法。

### 1. 指定镜像

选「你已经发布的镜像」，填：

```
ghcr.io/yzqcyl/ctfy-agent-pub:v2
```

### 2. 取名字

- **名字（id）**：`ctfy-v2` —— 只能是小写字母数字，可带 `.` `_` `-`；你的命名空间会自动加
- **显示名**：`ctfy-v2`（随意）
- **版本**：`v2`（随意，自由文本）
- 不要填 `source_url`（除非你想公开源码仓库地址）

### 3. ⚠️ 指明谁来付费

必须选 **OpenAI 兼容** 的 provider。**DeepSeek 的 key 是 OpenAI 兼容的，直接用**。

**绝对不要选 Anthropic** —— 我们的适配层只认 `LLM_FLAVOR=openai`，收到 anthropic
会立刻写 `stop_reason=refused` 并退出，等于 0 分。

### 4. 提交审核

等运营方批准。批准后跑的就是 `sha256:73b39430…` 的字节。

## 审核通过之后

用页面上的 **「测评我的 Agent」** 选一个测评集跑一遍。这一跑等价于正式评分路径，
跑完检查：

- 结果文件写了没有（没写 = `no_result_file`，直接失败）
- `stop_reason` 是不是 `done`
- 有没有 `answers`

平台侧的运行判定规则（`inspect_probe`）：缺 `result.json` 是 **blocking**，
非零退出只是 advisory，没有 `schema_version` 也是 advisory。

## 重新构建（发新版本时）

```powershell
$env:GH_TOKEN = "ghp_xxx"     # 需要 repo + workflow 权限
python tools/publish_image.py --tag v3
```

**必须显式给 `--tag`**：workflow 的默认 tag 是 `v1`，不带 `inputs.tag` 触发会把已提交的
v1 移走（平台判 `tag_moved`）。`publish_image.py` 现在会显式传 `inputs.tag`，并在
`--tag v1` 时打印警告。

推 master 本身是安全的（自动触发已移除，推了什么都不会重建）；危险只在手动 dispatch
时把 tag 填成已提交的那个。

产物还有一份 `ctfy-agent.tar.gz` 在运行页的 Artifacts 里。

## 相关工具

| 文件 | 用途 |
|---|---|
| `.github/workflows/build-agent-image.yml` | 云端构建流水线（仅手动触发） |
| `tools/container_check.py` | B1 契约验收（体积/目录/cwd/五条失败路径） |
| `tools/publish_image.py` | 建仓/推送/触发/轮询/可见性；**用 `--tag` 指定版本** |
| `tools/verify_image.py` | 匿名拉取 + 运行契约核验（随时可自检） |
