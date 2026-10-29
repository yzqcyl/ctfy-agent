# 领域扩展工具（pwn / crypto / reverse 地基）

> 背景：矩阵实测暴露三个方向是空的——**pwn 没有角色也没有工具**，crypto 只有 `shell` + 平台 `oracle`（上限 50），
> reverse 只有 `shell`（容器里没有 objdump/file/binwalk）。SAMPLE-003/004/012 是 vpn-only 实例，
> 本地测不到；010/011/014 从未跑过。本模块先补「三个方向都需要的同一块地基」。

## 新增文件

| 文件 | 内容 |
|---|---|
| `src/ctfy_agent/tools_domain_ext.py` | 4 个工具：`tcp` / `tcp_batch` / `elfpeek` / `netcheck`（纯 stdlib） |

## 工具说明

| 工具 | 用途 | 典型题 |
|---|---|---|
| `tcp(host, port, payload, hex_mode, expect, read_timeout, max_recv)` | 单次 TCP 往返：连实例 → 发 payload（文本或 hex）→ 收回显（hex + ascii 双视图） | pwn 打栈溢出、crypto 连 oracle 服务、misc 连通性 |
| `tcp_batch(host, port, payloads, hex_mode, read_timeout, same_conn)` | 同连接顺序发 N 个 payload 并汇总每条响应（≤200/次） | padding oracle、盲爆破 |
| `elfpeek(path, view, min_len, limit)` | 纯 stdlib ELF 解析：`info`（架构/入口/PIE/NX/canary/RELRO）/`sections`/`symbols`/`strings`/`gadgets` | pwn 偏移与缓解、reverse 静态 |
| `netcheck(host, port)` | DNS 是否解析 + TCP 是否可连；`*.internal` 解析失败时明确提示「VPN 隧道未接入」 | pwn/crypto 连不上时先诊断，避免盲目重试烧预算 |

## 接线方式（零改动 crew.py）

- 规格聚合：`tools_misc_ext.py` 尾部把 `DOMAIN_SPECS` 并进 `MISC_EXT_SPECS`；
  `crew.py:411-413` 已有「按角色 `local` 名单过滤 `MISC_EXT_SPECS`」的门禁循环，因此无需改 crew。
- 分发放行：`LocalTools.call()` 对 `DOMAIN_NAMES` 走 `_call_domain_tool()`（`tools_local.py`），
  先做**沙盒路径锚定**与**实例端点白名单校验**（复用 `allowed_hosts/allowed_endpoints`，与 `http_request` 同一套合规），
  再交给 `tools_domain_ext.call_domain()`。
- 角色授权（`config/agent.json`）：`rev.local += [elfpeek, tcp, tcp_batch, netcheck]`；`crypto.local += [tcp, tcp_batch, netcheck]`。
  web/misc/ai/coordinator 不可见（门禁断言已覆盖）。

## 验收

`python runs/_verify_domain_tools.py` → **8/8 PASS**：
- 模块注册、配置接线、真实 `Crew._role_specs` 门禁（rev 全量 / crypto 三件 / 其他角色零泄漏）
- 本地 echo 服务上的 `tcp` / `tcp_batch` 往返、`expect` 命中、hex 模式
- 合成 ELF64 的 info/sections/strings/gadgets、非 ELF 拒绝
- `netcheck` 可连通与 `*.internal` DNS 失败提示
- 合规：未登记实例拒绝、第三方主机拦截、沙盒路径越界拒绝

回归：`python runs/_run_cases.py` → 14/14 PASS（含 `case_stdlib_only` 纯 stdlib 契约、misc/ai 防漂移、web 工具白名单、crew/ledger）。

## 仍未做（截至本次收尾）

1. ~~pwn 角色~~ → ✅ 已新增（config-only，`delegate` enum 自动含 `pwn`，已验证）。
2. ~~crypto `oracle_limit: 50`~~ → ✅ 提到 **300**；`crypto.max_steps` 10→12。
3. ~~实测验证~~ → ✅ SAMPLE-010 **已解出**（111,552 tok）；SAMPLE-011 为环境性假阴性（本地不能执行 Linux ELF）；003/004/012 需 VPN；014 待平台限流解除。
4. **misc 授权**：014「TCP 连通性测试」属 misc，需要给 misc 角色 `tcp/netcheck`，会触碰 `roles/misc.json` 防漂移断言 → 需与 P 系列同步（唯一未闭环项）。

## 阶段 0：可达性探针（`runs/probe-010-014.json`，零 LLM 成本）

| 题 | 类别 | access | 服务 / 文件 | 本地可测？ |
|---|---|---|---|---|
| SAMPLE-010 | reverse | 无服务 | `provided_files`: `licencecheck.py`（1015B，python） | ✅ 文件型 |
| SAMPLE-011 | reverse | 无服务 | `provided_files`: `vaultsvc`（14392B，**stripped ELF**） | ✅ 文件型 |
| SAMPLE-012 | pwn | `vpn` | tcp `stx3c6ddrprjtk2se.internal:1337`；平台下发 `vpn: udp node1.ctfy.run:32979` | ❌ 需隧道 |
| SAMPLE-014 | misc | `tls_sni` | tcp `snmadzp2epg5iahdf.node2.ctfy.run:443`；`vpn: null` | ✅ 公网直连 |

修正与情报：
- **014 不是 VPN 题**（原判断"必须平台容器"错误）——它是 443 端口 TLS+SNI 的公网接入，本地可测。
- 012 的 `surface.vpn` 给了 udp 接入点，说明**平台侧提供隧道参数**，评测容器内很可能由平台打通；本地需自建 wireguard 才能测。
- 010/011 是纯文件题（`services: []`），不依赖实例网络——`elfpeek` 正好对口 011 的 stripped ELF。

## 阶段 A：pwn 角色 + crypto 参数（config-only，零代码冲突）

| 改动 | 内容 |
|---|---|
| `roles.pwn` | model Flash / `max_steps: 12` / `local: [shell, elfpeek, tcp, tcp_batch, netcheck]` / 自定义 pwn 打法提示词（取料→静态→偏移→打通→取旗） |
| delegate enum | **自动包含 pwn**：`crew.py:417-426` 从 `config.roles` 派生，无需改 crew.py |
| `oracle_limit` | 50 → 300（padding oracle 类需要数百次） |
| crypto `max_steps` | 10 → 12 |
| TLS+SNI | `tcp` / `tcp_batch` / `netcheck` 增加 `tls` / `sni` 参数（stdlib ssl，对应 `access=tls_sni` 的 443 接入） |

验收：`runs/_verify_domain_tools.py` **8/8 PASS**（含 pwn 门禁与 delegate enum 断言）；`runs/_run_cases.py` **14/14 PASS**（含纯 stdlib 契约与 misc/ai 防漂移）。

## 阶段 B：reverse 实测 → 揪出附件截断 bug（P0）

| 轮次 | run-id | 结果 | token | 观察 |
|---|---|---|---|---|
| v1 | `mat-domain-v1` | 010 ✗ / 011 ✗ | 318,564 | 均 3 步 159k，identical 失败形态 → 系统性 |
| v2 | `mat-domain-v2` | 010 ✗ | 154,910 | 落盘未生效 |
| v3 | `mat-domain-v3` | 见下 | — | 解析器修复后重跑 |

根因链（三方证据）：
1. **平台返回完整**：MCP 直连复现——010 返回 2361 字符（含信封）、011 返回 38,653 字符（14392B base64 完整），无截断。
2. **我们这侧压缩掉中段**：`ctxcompress.shrink_tool_text`（`DEFAULT_TOOL_CHARS=1600`，保留头 45% + 关键行 + 尾 12%）被 `solver.py:871/1120` **无条件**套在每个工具结果上。010 的 2521 字符源码 → 中段丢失；011 的 38k base64 → 只剩 ELF 头。
3. **转录证词**：rev 明确记录"附件正文中间被省略号截断"，随后反复重下（被账本拦截）直到预算耗尽。

修复（新增 `artifacts.py` + `solver.py` 3 行接线）：
- 下载类工具（`download_instance_attachment` / `download_challenge_attachment`）返回**解码后落盘到本题沙盒**，只回摘要（相对路径 / 字节数 / sha256 / 头 32 字节 hex）。
- 解析器要点：`platform.call_text()` 返回带 `----- 工具返回 -----` 前缀且尾部再拼美化 JSON，必须剥前缀 + `raw_decode` 只取第一个 JSON（v2 就是漏了这一步而静默透传）。
- 真实 MCP 端到端验证：010 → `licencecheck.py` 落盘 1015 字节，sha256 `e52dd1ed…`。

旁证：v2 转录里 rev 仅凭被截断后可见的两条 strip 就推出了候选 flag `FLAG{6553412ef6c3a23b80c5f4e702fef198}`（v3 已验证通过）。

### v3 结果（修复后）

| 题 | 结果 | token | 耗时 | 步数 | 说明 |
|---|---|---|---|---|---|
| SAMPLE-010 | ✅ **flag_accepted** | 111,552 | 330.7s | 2 | 下载落盘 → shell 5 步（python 分析 + 编译 .pyc）→ report → submit |
| SAMPLE-011 | ✗ budget_exhausted | 150,178 | 325.0s | 4 | 文件已正确落盘（14,392B），elfpeek 2 次 + shell 9 次，卡在「无反汇编器/无法执行 Linux ELF」 |

**011 定性：环境性假阴性，不是能力缺口**
- 二进制里 `strcmp` 紧邻字面串 `v4ultsvc-p4d`（服务码已拿到），但全库搜不到 `FLAG{` 字面量 → flag 只能由程序运行时打印。
- 该 ELF 是 Linux x86-64（ET_EXEC、无 PIE、NX/RELRO 开）；本地 Windows 沙箱无法执行 → 只能反汇编，而容器没有 objdump/gdb/capstone。
- **平台容器（Linux）里 `chmod +x ./vaultsvc && ./vaultsvc v4ultsvc-p4d` 即可**，属与 pwn/crypto 同类的「本地不可测」题。

阶段 B 总花费：**735,204 token**（v1 318,564 + v2 154,910 + v3 261,730），超出预估 400k，原因是 v2 解析器缺陷导致多跑一轮。

## 变更清单（本分支可归责到本次域扩展工作的部分）

### 新增文件

| 文件 | 作用 | 验证 |
|---|---|---|
| `src/ctfy_agent/tools_domain_ext.py` | `tcp` / `tcp_batch`（含 TLS+SNI）/ `elfpeek`（ELF 静态分析）/ `netcheck`，纯 stdlib | `_verify_domain_tools.py` 8/8 |
| `src/ctfy_agent/artifacts.py` | 附件下载落盘（替代"塞上下文"），路径消毒 + sha256 摘要 | `_verify_artifacts.py` 5/5 + 真实 MCP 端到端 |
| `docs/domain-tools.md` | 本文件 | — |
| `runs/_verify_domain_tools.py` | 域工具 + 配置 + 门禁 + 合规验收 | 8/8 PASS |
| `runs/_verify_artifacts.py` | 落盘模块离线验收（真实信封形状） | 5/5 PASS |
| `runs/_run_cases.py` | 按路径加载 `tests/smoke.py`，跑 14 个既有用例 | 14/14 PASS |
| `runs/_probe_010_014.py` / `runs/probe-010-014.json` | 零 token 可达性探针 | — |
| `runs/_download_probe.py` | 零 token 验证平台附件返回完整性 | — |
| `runs/_debug_persist.py` | 落盘端到端调试（真实 MCP） | — |
| `runs/_tcp_014_probe.py` | 014 TLS+SNI 端到端验证（被平台限流挡住） | 待跑 |

### 既有文件中的本次改动（精确位置）

| 文件 | 位置 | 内容 |
|---|---|---|
| `config/agent.json` | 全文 | 新增 `roles.pwn`；`oracle_limit` 50→300；`crypto.max_steps` 10→12；`rev.local` 加 `tcp/tcp_batch/netcheck`；`crypto.local` 加 `tcp/tcp_batch/netcheck` |
| `src/ctfy_agent/tools_misc_ext.py` | 尾部 11 行 | 聚合 `DOMAIN_SPECS` → `MISC_EXT_SPECS`（try/except 包裹，失败不影响既有工具） |
| `src/ctfy_agent/tools_local.py` | 38 / 544 / 616 / 676 | 4 处接入：import 域工具、`_check_endpoint` 合规检查、`call()` 分发、`_call_domain_tool()` 沙盒路径锚定 |
| `src/ctfy_agent/solver.py` | 18 / 594-597 | 5 行：import + 下载类工具落盘钩子 |

⚠️ `solver.py` 与 `tools_local.py` 的 diff 里同时含 **P 系列**改动（`_traffic_enabled`、traffic 库等），提交前需按上述行号分开确认归属。

## 待协调 / 未完成（等 P 系列）

| 项 | 依赖 | 说明 |
|---|---|---|
| 011 类题（Linux ELF 需执行） | 平台容器 | 本地 Windows 不可测；平台上 `./vaultsvc <code>` 即出，无需代码改动 |
| 014 TLS+SNI 端到端 | 平台限流解除 | 跑 `runs/_tcp_014_probe.py`（零 token）即可收尾 |
| misc 角色拿 `tcp`/`netcheck` | `roles/misc.json`（P 系列所有） | 需与 `case_misc_contract` 防漂移断言同步 |
| 「见到 FLAG 立即 report」门禁 | `crew.py` / `tools_local.py`（P 系列范围） | misc 005 实测 6/10 模型看到 flag 却 0 提交 |
| 附件落盘回归 | 全领域 | 该修复对 web/ai/misc 的附件题同样生效，P 系列合并后应重跑一次全集 |

## SAMPLE 全量基准：第二个 P0（proof-of-exploitation 无法提交）

**证据（SAMPLE-006，run-id `sample-bank-v1`）**

| 项 | 事实 |
|---|---|
| 工具调用 | `http_request`×12、`http_burst`×2、`shell`×3、`jsrecon`/`fuzz`/`http_map` 各 1；**从未出现 `verify_exploit` / `verify_answer`** |
| 转录 | web 专家已成功 deface：POST `/api/banner` 返回 `{"banner":"DEFACED_BY_CTF_20250101"}`，之后仍在找「首页逐字证据」，最终 `budget_exhausted`（152,794 tok） |
| 题面 | `There is no flag string to submit — press "Verify exploit" and the platform's trusted judge sidecar will confirm the defacement.` |
| 平台能力 | 存在 `verify_exploit(instance_id, question_id)`；SAMPLE-006 的 question id = `deface`（mode=`checker`） |

**根因**：web/ai 角色的 `platform` 白名单是显式工具名列表，不含 `verify_exploit`；角色 system 提示词也只教「找 flag」，没有 proof-of-exploitation 流程。006/007/008/009 四题同族（tags 均含 `proof-of-exploitation`）。

**修复方案（前两步 config-only，第三步需 P 系列配合）**

1. `config/agent.json`：web / ai / coordinator 的 `platform` 追加 `verify_exploit`（`crew._role_platform_names` 支持直接写工具名，已验证 `PLATFORM_SCOPE.get(entry) or {entry}`，零 crew.py 改动）。
2. `roles.web.system`：新增流程——「若题面无 flag 或 `questions[].mode == "checker"`：达成效果后调用 `verify_exploit(instance_id, question_id=<题目 id>)` 让平台侧 checker 判定；不要找 FLAG 字符串，不要反复重放请求直到预算耗尽」。
3. **判定回传（P 系列）**：`summary.submitted` 目前只由 submit 回执置真（`solver.py:938`、`crew.py:904`）；`verify_exploit` 的成功回执需同样被识别，否则平台已记 solve 而本地统计仍为 ✗。

> 应用时机：等本轮回合基准跑完后改 config 再单独复跑 006 验证，避免污染本轮 150k 口径的统计。

## SAMPLE 题库最终战绩（两个修复落地后）

合并口径：每题取最新一轮结果（`runs/sample-bank-final/matrix.jsonl`，由 `runs/_merge_sample_stats.py` 从
`sample-bank-v1` + `sample-006-exploitfix2` + `sample-014-tcpfix` + `sample-bank-v2` 合并）。

**7 / 14 解出（50%）**，当前口径总 token 1,965,090、总时长 3,791 s、平均 140,364 token/题。

| 类别 | 题数 | 解出 | 解出率 | token 均/题 |
|---|---|---|---|---|
| web | 6 | 3 | 50% | 150,267 |
| reverse | 3 | 2 | 67% | 132,582 |
| misc | 2 | 2 | **100%** | 97,731 |
| pwn | 2 | 0 | 0% | 154,264 |
| crypto | 1 | 0 | 0% | 161,752 |

平台侧 `get_my_solves` 已确认：`SAMPLE-001/002/005/006/009/010/014` 共 7 题。

### 修复带来的净变化

| 题 | 修复前 | 修复后 | 说明 |
|---|---|---|---|
| 006 | ✗ 152,794 | ✅ 161,393（`exploit_verified`） | `verify_exploit` 通道 |
| 009 | ✗ 156,347 | ✅ 151,668（`exploit_verified`） | 同上 |
| 014 | ✗ 151,070 | ✅ **46,805**（↓3.2×） | misc 授权 `tcp` |

### 剩余 7 题归因（全部有据可查）

| 题 | 归因 | 依据 |
|---|---|---|
| 003 / 004 / 012 | 环境：VPN 隧道缺失 | 探针 `access=vpn`；agent 已确认本机无 wg/openvpn/tailscale |
| 011 | 环境：本地无法执行 Linux ELF | 二进制无 `FLAG{` 字面量，服务码 `v4ultsvc-p4d` 已知，需运行才出 flag |
| 007 | **真能力缺口：DoS 打点未达成** | 4 步内只做了 http_map/fuzz/http_burst，从未接近「打掉后台 worker」 |
| 008 | 差一步（预算耗尽） | `/api/notes` 已有 8 条路径返回 200 `{"saved":...}`，正准备 ENOTDIR 复核后调 `verify_exploit` 时 cap 用尽 |
| 013 | 本地 shell 非同一性 | 协调者诊断：Windows cmd 的引号/换行把 pickle payload 生成卡死；平台 Linux 应正常 |

### 花费

| 轮次 | token |
|---|---|
| `sample-bank-v1`（全量基线） | 2,065,359 |
| 006 复跑（exploitfix2） | 161,393 |
| 014 复跑（tcpfix） | 46,805 |
| `sample-bank-v2`（007/008/009/013） | 619,354 |
| **合计** | **≈ 2,892,911** |

## 修复①：proof-of-exploitation 提交路径（已合入 + 已实测验证）

**证据**：006 修复前 web 专家已 deface 成功（`{"banner":"DEFACED_BY_CTF_20250101"}`）却找不到 `verify_exploit`，
152,794 tok 预算耗尽。该类题（006/007/008/009）**没有 flag**，必须由平台侧 judge 判定。

**改动（6 处）**

| 文件 | 改动 |
|---|---|
| `crew.py:65` | `"oracle"` 组 += `verify_exploit`（coordinator/crypto 自动获得） |
| `crew.py:72` | `DEFAULT_ROLES["web"]["platform"]` += `verify_exploit`（契约要求与 config 一致） |
| `tests/smoke.py:2285` | `keep` 白名单 += `verify_exploit`，注释/断言 9→10 |
| `config/agent.json` | `roles.web.platform` += `verify_exploit`；`roles.web.system` 增加 proof-of-exploitation 流程 |
| `platform.py` | 新增 `my_solved_ids()`：读 `get_my_solves` 作为确定性事实源（10 s 缓存，失败返回空集） |
| `solver.py` / `crew.py` | `verify_exploit` 成功后用 `my_solved_ids()` 判定并把本题记为已解（`reason=exploit_verified`） |

**验证：SAMPLE-006 复跑（run-id `sample-006-exploitfix2`）**

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 结果 | ✗ budget_exhausted | ✅ **flag_accepted（`exploit_verified`）** |
| token | 152,794 | 161,393 |
| 工具链 | http_request×12 … 无 verify 工具 | http_request×8 + **`verify_exploit`×1** |
| 平台记录 | 未解出 | `get_my_solves` 含 `SAMPLE-006` |

转录证据：`banner 已是 DEFACED_TEST_X，对照 POST 返回 403；调用 verify_exploit 交由平台侧 judge 判定`，
回读确认 `<h1 id="banner">DEFACED_TEST_X</h1>`。

## 修复②：misc 授权 TCP 工具（已应用 + 已实测验证）

改动（两处，保持防漂移断言绿）：

| 文件 | 改动 |
|---|---|
| `config/agent.json` | `roles.misc.local` += `tcp` / `tcp_batch` / `netcheck` |
| `roles/misc.json` | 同步同一份 local 列表（`tests/smoke.py:2096` 要求逐键一致） |

验证：SAMPLE-014 复跑（run-id `sample-014-tcpfix`）

| 指标 | 修复前（`sample-bank-v1`） | 修复后 |
|---|---|---|
| 结果 | ✗ budget_exhausted | ✅ **flag_accepted** |
| token | 151,070 | **46,805**（3.2× 降低） |
| 耗时 | 251 s | 251 s |
| 工具链 | 只有 shell 写 python socket（7 次，被 cmd 引号坑） | `tcp` **1 次** TLS+SNI 直连取旗 |

转录证据：`PLAN: 直接用 tcp 工具以 TLS+SNI 连 ...:443，读取横幅后发送 flag` → `一次 TLS+SNI 连接即返回横幅与 FLAG 原文`。
回归：`_run_cases.py` 14/14、`_verify_domain_tools.py` 8/8、`_verify_artifacts.py` 5/5。

## 平台限流真因（已解决）

- 现象：`start_instance` 返回 `Rate limited by upstream API, please retry later`，20 s 递增退避重试 7 次（≈8.5 min）仍拒。
- 真因：早前崩溃的 014 探针留下**孤儿实例**，占满团队实例配额（配额 = 1）。`list_instances` 的真实返回结构是
  `structuredContent.result`，此前按 `instances` / `items` 解析，所以误判为「无孤儿实例」。
- 处理：停掉孤儿实例后，看门狗第 7 次探测即通过，SAMPLE 全量基准顺利开跑。
- 教训：`start_instance` 报「Rate limited」时，**先列实例清孤儿**，再怀疑平台限流。
