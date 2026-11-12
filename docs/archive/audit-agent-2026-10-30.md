# Agent 行为诊断与打磨记录（2026-10-30）

> 目标：把 agent 本身打磨好（与镜像提交无关）。数据：`bank-500k-v1` 35 题（10 解出）。
> 工具：`runs/_agent_forensics.py`（行为取证）、`runs/_rej_diag.py`（证据拒收诊断）、
> `runs/_timeline.py`（单题时间线）、`runs/_err_infra.py`（预算去向/错误分类）、
> `runs/_subset_score.py`（可达性口径）。
> 本轮**未消耗任何实例/API token**（全部离线转录分析 + 本地单测）。

## 0. 最重要的口径修正：28.6% 是「本地环境下的下界」

| 口径 | 得分 | 含义 |
|---|---|---|
| 全量 35 题 | **10/35 = 28.6%** | 与既有基线一致（解析正确性已验证） |
| 本地可达 28 题（排除仅内网实例） | **10/28 = 35.7%** | 去掉「本机不可能解出」的 7 题 |
| 可达且无 Windows 守卫干扰（约 26 题） | **10/26 ≈ 38.5%** | 再排除 2 题被本地 shell 守卫拖死的 |

**7 题本地不可达**（实例主机全部是 `*.internal`，本机无 VPN 隧道）：
CYBENCH-001/002/010/011、SAMPLE-003/004/012 —— **全部 0 分，与 agent 能力无关**。
**2 题被 Windows 专属守卫重创**：SAMPLE-011（rev）、SAMPLE-013（web，13 次错误多为 `python3` 拒收）。

⚠️ **关键未确认项**：平台容器（Linux）内是否自动具备题目 VPN 隧道/内网 DNS？
- 若"平台已打通隧道"（`netcheck` 的提示文案如此假设）→ 这 7 题在正式赛正常可打，预期基线 ≈35–40%；
- 若平台容器也没有隧道 → 需镜像内置 wireguard/tailscale 客户端并让 agent 建立隧道（v4 需求）。
- **这条必须在正式赛前用一次实跑确认**（不能靠猜）。

## 1. 解出 vs 未解的行为差异（实测）

| 指标（均值/题） | 解出 10 题 | 未解 25 题 | 解读 |
|---|---|---|---|
| 工具执行次数 | 30.6 | **47.6** | 未解多烧 55% 调用，信息密度更低 |
| 工具报错次数 | 1.0 | **2.4** | 错误消耗步数，且多数可避免 |
| 转录中出现 flag 但未交 | — | **0/25** | **无漏交**（好消息，不是"看到旗子忘了交"） |
| 角色派发 | web 10 | web 22/crypto 7/rev 6/pwn 3/misc 11 | 基本对口 |

失败原因：`budget_exhausted` 22、`coordinator_done` 2、`no_candidate` 1。

## 2. 已修复缺陷：M4 证据误杀（D1）

**根因**：模型提交的 `evidence` 常是**响应体嵌套 JSON 的内层内容**，而账本存的是带转义的外层原文
（`\"body\":\"{\\\"data\\\"…\"`），字面比对必然落空 → 10/14 被拒属误杀。每次拒收浪费一整轮（≈12k tok）。

**修复**（`src/ctfy_agent/ledger.py`，+62/−5）：
1. 新增 `_decoded_strings()`：解出嵌套 JSON 内层字符串（≤3 层）；
2. `verify_evidence` 三层比对：字面 → NFKC+空白折叠 → **内层解码串**（仍要求连续逐字，改写仍拒）；
3. `recent_sources` 片段 120→240 字符（被拒 evidence 中位长度 >120）。

**验证**：`tests/smoke_evidence.py` 14 条断言全过；真实回放 **14 例被拒 → 10 例可接受（71%）**，
剩 4 例确属改写；`tests/smoke.py` **144 passed / 0 failed**；`tests/smoke_context.py` exit 0。

## 3. C：SAMPLE 5 题全灭复看结论

| 题 | 分类 | 根因（实测） | 判定 |
|---|---|---|---|
| SAMPLE-003 | pwn | 实例仅 `*.internal:1337`，本机无 wg/隧道；agent 试了 3 条接入路线，烧掉 30 次调用（53%） | **环境阻塞** |
| SAMPLE-004 | crypto | 同上（`*.internal:31313`）；tailscale 无权限、vpn.config_url 为空、cert 卷未挂载 | **环境阻塞** |
| SAMPLE-012 | pwn | 同上；agent 正确认定"环境侧阻塞"并主动 `done`，行为正确 | **环境阻塞（行为正确）** |
| SAMPLE-011 | rev | 附件本地可读、已还原到 `.rodata` 候选 key/enc——但被 **Windows 专属 shell 守卫**反复拒收（无 `write_file` 工具，只能 `python -c "open(...)"` 拼脚本） | **环境假阴性（Linux 可解）** |
| SAMPLE-013 | web | 反序列化题；13 次错误中多为 `python3` 被 Windows 守卫拒收 + 无 write_file，容器格式探测未收敛 | **环境假阴性 + 难度** |

**结论**：SAMPLE 0/5 **不能作为 agent 能力证据**。其中 4 题是本机环境（无隧道/Windows 守卫）造成的，
1 题（SAMPLE-013）属真难度。**不要为 SAMPLE 过拟合**。
`tools_local.py:1856 _windows_shell_guard` 只在 `os.name == "nt"` 生效（1888 行确认），**容器 Linux 不受影响**。

## 4. A：预算去向与「环境失败断路器」规格

全量 2134 次工具调用中，环境类失败 **379 次（17.8%）**（严格分类，已排除汇总类工具回显的重复计数）：

| 类别 | 次数 | 说明 |
|---|---|---|
| `win_shell` | **265** | Windows shell 守卫拒收（`ls/cat/grep`、单引号、heredoc、`python3`）——**容器不存在** |
| `missing_bin` | 43 | 本地缺 ffuf/sqlmap（镜像内置）——**容器不存在** |
| `vpn_dns` | 51 | `*.internal` DNS/连接失败（本地无隧道） |
| `compliance` | 19 | 合规拦截：12 次 `http_burst values>24`、4 次 `{v}` 模板当主机、3 次未登记实例就 netcheck |
| `tool_schema` | 1 | `decode_blob` 缺 `text` 参数 |

**预算焚烧集中度**：SAMPLE-003 30 次（53%）、SAMPLE-004 28 次（54%）、SAMPLE-012 25 次；
最长连续环境失败 **7–9 次**；连续 ≥3 次的有 23/51 题。

**A1 已实现（2026-10-31，门 A 后；`progress.py` + `solver.py` 两条循环）**：
- `progress.failure_kind()`：把工具失败分到 `dns / bin / timeout / blocked` 四类（顺序保证「[工具被拒绝] + DNS 失败」先归 dns）；
- `progress.EnvFailureTracker`：连续 ≥3 次 → 「先确认接入方式、别烧预算」；≥6 次 → 「立即收口、换接入路线」；环境失败 ≥3 且预算已用 ≥30% → 预算侧提示。只在阈值点各出一次，不刷屏；
- 接线：`_solve_once` 与 `run_role` 每步工具结果后追加为 user 消息并落 transcript（`role=note` / `env_note` 字段）；
- **验收**（`runs/_env_replay.py`，真实转录回放）：SAMPLE-003 第 7 次调用、SAMPLE-004 第 7 次调用即触发首条提示（两题分别烧了 30/28 次接入层失败）；SAMPLE-012 触发 6 条；CYBENCH-001/010 均识别出 dns/blocked 类失败。脚本退出码 0。

**A2 已实现（2026-10-31，`tools_local.py` + `tools_misc_ext.py`）**：
- `write_file(path, content, append?)` 新工具（见 B1）——彻底绕开 Windows/Linux 引号差异；
- `http_burst` 的 `values` 上限文案补「超出请拆成多批调用，每批换一个 nonce」；
- `{v}` 等模板出现在 `host` 位时，直接报「host 里不能放模板」并给正确用法（实测 CTFARENA-005 曾被此拦 4 次）。

## 5. B：错误治理清单（2026-10-31 状态：B1/B2/B4/B5 已完成）

| # | 项 | 现状 | 处理结果 | 落点 |
|---|---|---|---|---|
| B1 | 无写文件工具 | 265 次 win_shell 拒收多由此引发 | ✅ **已新增 `write_file`**（沙盒锚定、512KB 上限、自动建父目录、append 模式；6 个角色 local 名单 + roles 双源同步） | `tools_misc_ext.py` / `tools_local.py` / `config/agent.json` / `roles/*.json` |
| B2 | 守卫"拒收"而非"改写" | `python3`/`ls`/`cat` 直接拒 | ✅ **已改为先安全改写**（python3→python、ls→dir、cat→type、pwd→cd、which→where、cp/mv/rm/clear），仅剩无语义等价物的命令（grep/head/tail/wc/sed/awk/xxd/sh/bash、heredoc、单引号）继续拒收 | `tools_local.py` |
| B3 | `ffuf`/`sqlmap` 未装 | 43 次 | 镜像已内置，本地假阴性；**待容器内 `which ffuf` 验证**（v4 镜像验收项） | 镜像 |
| B4 | `decode_blob` 参数契约 | 1 次 | ✅ **复核：schema 早已 `required:["text"]`**，当时是模型幻觉参数；`call()` 已有「合法参数」自愈文案，无需改 | — |
| B5 | `http_burst` 上限提示 | 12 次 | ✅ values 描述补「最多 24；超出拆多批、每批换 nonce」 | `tools_local.py` |
| B6 | 未登记实例就 netcheck | 3 次 | 工具错误文案已含指引；`failure_kind` 归为 `blocked` 计入环境失败，可触发 A1 提示 | 已覆盖 |

**验收**：新增 `tests/smoke_local_tools.py` 27 条断言全过（write_file 往返/越界/超限、命令改写 6 例、失败分类 6 例、断路器阈值与预算提示、模板主机拦截、规格面一致性）；全量 `tests/smoke*.py` **26/26 通过**（含 `smoke.py` 144 项）。

## 6. 确认「已修复 / 无需修」

| 项 | 结论 |
|---|---|
| `.internal` 内网提示 | 工作区**已修**（`tools_domain_ext.py:82/1740/1870`），历史数据为修复前 |
| 看到 flag 未提交 | **0 例** |
| 重复调用拦截 | 正常（P0 上下文去重） |
| 脚本解释器 `python` 指到当前解释器 | `tools_local.py:1896-1902` 已处理（PATH 前置），无问题 |

## 7. 门 A 已完成（2026-10-31）

| # | 项 | 状态 |
|---|---|---|
| 1 | `write_file` 工具（B1） | ✅ 6 角色可见（config/agent.json + roles/*.json 双源一致，漂移测试通过） |
| 2 | shell 守卫安全改写（B2） | ✅ python3/ls/cat 等自动改写；heredoc/单引号仍明确拒收并给替代路线 |
| 3 | 环境失败断路器（A1） | ✅ 两条循环接线，`runs/_env_replay.py` 验收通过 |
| 4 | `{v}` 主机位文案（A2） | ✅ |
| 5 | 回归 | ✅ `tests/smoke_local_tools.py` 27 断言 + 全量 26/26（`smoke.py` 144 项） |

**剩余（不阻塞）**：容器内 `which ffuf/sqlmap/python3` 验证（B3，镜像验收项）；A1 的实机效果需下一次真实跑批对比（离线只能证明"提示按时触发"）。

## 8. 本轮改动清单

- `src/ctfy_agent/ledger.py`：+62/−5（M4 证据归一，§2）✅
- `src/ctfy_agent/progress.py`：新增 `failure_kind` + `EnvFailureTracker`（A1）
- `src/ctfy_agent/solver.py`：两处循环接线（A1），`import EnvFailureTracker`
- `src/ctfy_agent/tools_misc_ext.py`：新增 `write_file` 实现 + `WRITE_SPEC`（B1）
- `src/ctfy_agent/tools_local.py`：`write_file` 适配层、`_windows_rewrite`（B2）、`{v}` 主机拦截（A2）、burst 文案（B5）
- `config/agent.json` + `roles/ai.json` + `roles/misc.json`：6 角色 local 名单加 `write_file`（双源同步）
- `tests/smoke_evidence.py`（14 断言）、`tests/smoke_local_tools.py`（27 断言）：新增
- `runs/_agent_forensics.py`、`_rej_diag.py`、`_timeline.py`、`_err_infra.py`、`_subset_score.py`、`_env_replay.py`：诊断/验收工具
- **未提交**：等用户确认后统一提交（工作区同时含并行会话已收尾的未提交改动）