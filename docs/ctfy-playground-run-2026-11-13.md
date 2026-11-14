# 平台练习场（SAMPLE 批次）全量跑结果与收尾 — 2026-11-14

比赛：`平台练习场`（`bc60dc1764f34413964a547102f17261`，17 道 SAMPLE 题，practice 形态，不计名次）。
接入方式：本机无 WireGuard/TUN 权限，数据面走自研用户态隧道 `netbridge`（wireproxy 子进程）
+ `portmap`（socket 层透明改写 + 隧道内 DNS）。

---

## 1. 最终结果（一句话）

**17 题里 16 题已经拿下，其中 14 题在练习场看板正常记为「已解出」；
SAMPLE-005 / SAMPLE-020 平台已返回 `correct:true` 但看板不记（平台 QA 记分缺陷）；
SAMPLE-023 在练习场因 engagement（estate）部署缺陷未能完成 —— 该题我们早在「SAMPLE」比赛里解过。**

| 口径 | 数值 | 证据 |
|---|---|---|
| 练习场看板 `my_fully_solved` | **14 / 17** | `list_competition_challenges` 快照 `runs/_mon/evidence_both.json` |
| 团队 `get_my_solves`（练习场） | **16 / 17**（含 005、020） | 同上 |
| 在「SAMPLE」比赛（`e4e78ff…`，同 17 题） | **15 / 17**（含 023，缺 005/020 同因） | 同上 |
| 唯一没拿到解出凭证的题 | SAMPLE-023（练习场） | 见 §4.2 |

### 逐题结果

| 题 | 形态 | 练习场 | 关键证据（本轮） |
|---|---|---|---|
| SAMPLE-001 | Web 双问（flag+slug） | ✅ | 历史解出，`flag`/`slug` 均在 `my_solved_question_ids` |
| SAMPLE-003 | Web | ✅ | 历史解出 |
| SAMPLE-005 | QA 套娃压缩包 | ⚠️ 判对但不记账 | 下载 `archive.zip`→`layer2.zip`→`layer3/inventory.txt`，`ticket-id: FLAG{archives-stack-just-keep-unzipping-7b2c19}`，`submit_qa_answer` 回 `correct:true, challenge_fully_solved:true`（提交 id `e9404ee8-b650-49e3-9199-174581f5e025`）；但两场比赛看板 `solves_count` 仍为 0 |
| SAMPLE-006 | checker「改 banner」 | ✅ | `POST /api/banner`（`X-Internal-Request: true`）→ `verify_exploit(deface)` = `passed:true, exit_code:0, solve_rank:53` |
| SAMPLE-007 / 008 | Web | ✅ | 历史解出 |
| SAMPLE-009 | 泄漏 token + checker 提权 | ✅ | 第二问 `root`：`POST /api/operators/promote {user:"bob", token:<service_token>}` → `verify_exploit(root)` = `passed:true, exit_code:0, solve_rank:36, challenge_fully_solved:true` |
| SAMPLE-015 / 016 / 017 / 018 | UDP / 预解 / Web | ✅ | 历史解出 |
| SAMPLE-019 | checker「计数器 ≥3 提交当前值」 | ✅ | 起实例等 judge 就绪（~40s）后提交计数器当前值 = `correct:true, solve_rank:19` |
| SAMPLE-020 | QA 三问 | ⚠️ 判对但不记账 | `flag_format=B`、`channels=A,B,D`、`gate_field=requires` 三条均 `correct:true, challenge_fully_solved:true`（提交 id `f6e979d0…` / `cd8b50fb…` / `e22ce286…`）；两场比赛看板均 0 解 |
| SAMPLE-021 / 022 / 024 | Web / 双服务 / OOB | ✅ | 022：estate DNS 解出 vault 主机名后扫端口，vault 实际在 **:8090**，`solve_rank:49`；024：入站回调拿 flag |
| **SAMPLE-023** | **estate + edge-DNS** | ❌（练习场） | 见 §4.2；**该题在「SAMPLE」比赛已解**（`my_solved_question_ids=['flag']`，历史提交 `FLAG{132fc7f7…}`） |

---

## 2. 本轮查清并修掉的「自身原因」（都在代码/流程里落地）

1. **judge/checker 就绪窗口**（SAMPLE-019 实证）：实例 `status=ready` 后 judge 侧车还要几十秒才可用，
   期间 `verify_exploit`/`submit_answer` 稳定 `exit_code=1`。修复：`solver.py` 给 `verify_exploit`
   加 20/25/30s 退避重试；手动脚本统一「起实例 → 先正常访问应用 30–60s → 再 verify」。
2. **`surface.services` / `entry_urls` 会漏服务**（SAMPLE-022 实证）：vault 既不在 `services` 也不在
   `entry_urls`，真实监听在 `:8090`。修复：先用 estate DNS 解析 `vault.<stem>.internal`，再对 IP 扫端口；
   已固化到 `runs/_mon/case_022_scan.py`。
3. **estate 服务启动慢**（SAMPLE-023）：不要起实例 5–10s 就判「服务不存在」，至少要等 ~2 分钟并多轮重试
   （实测 portal 在 t+120s 才返回 200、files 在 t+36s 才返回 200 的两个成功样本）。
4. **用户态隧道不保活**（本轮新增修复）：wireproxy 默认不发 WireGuard keepalive，隧道空闲后
   node 侧 wireguard→DMZ 的 NAT 映射失效，表现为「connection refused / 无响应」的假故障。
   修复：`netbridge.keepalive_conf()` 自动补 `PersistentKeepalive = 25`（已有该键不覆盖），
   `tests/smoke_netbridge.py` 已加断言。
5. **QA-only 题走错通道**：005/020 必须用 `submit_qa_answer`（`challenge_id`+`competition_id`+`answer`，
   可带 `question_id`），不能用实例提交通道。

---

## 3. 平台看板口径问题（不是我们没提交）

练习场看板与 `get_my_solves` 两套口径不一致，QA-only 题尤其明显：

- 看板 `list_competition_challenges` 的 `my_fully_solved` / `my_solved_question_ids` 对 005/020 始终为空，
  两场比赛的 `solves_count` 也始终是 0；
- 但 `submit_qa_answer` 明确返回 `correct:true`、`challenge_fully_solved:true`（提交 id 见上表），
  `get_my_solves` 也把 005/020 算进团队已解；
- 结论：**QA-only 题的 solve 记录没有同步到看板/题目进度**（平台侧缺陷）。
  因此看板显示 14/17，而团队实际已解 16/17。

---

## 4. SAMPLE-023（唯一未完成）：证据链

### 4.1 题面与访问方式

estate 经 VPN 访问；平台把 resolver 指到 estate 的 edge-dns，只应答入口名字
`portal.sample023.internal` / `files.sample023.internal`；portal 首页说明文档由
`http://files.sample023.internal/flag` 提供，文档即 flag；vault 无公开名字。

### 4.2 观测到的平台侧缺陷（多实例、多节点）

| 观测 | 数据 |
|---|---|
| `services`（attack_surface） | 20+ 次起实例、7 个节点（10.90.0.3/4/6/8/9/10/12）**全部为 0 项** |
| `entry_urls` | 每次都有两个名字，DNS 也能解析到实例 `/24` 内 IP |
| 连通性 | 绝大多数实例 `files:80` / `portal:80` 一直 `connection refused`（主机在、无监听）；只有 2 次命中 200（t+36s files、t+120s portal） |
| 对照 | 在「SAMPLE」比赛起同一题，同样 `services=[]`、120s 内无响应 |
| 平台源码 | `ctfy/core/target.py` 明确写 engagement 实例应把 per-instance FQDN 填进 `attack_surface.services`；为空正是它自己注释里的已知坏形态（“a player holding a tunnel with no address to dial inside it”） |

尝试过的策略（脚本都在 `runs/_mon/`）：长等待（最长 10 分钟）、密集多端口探测、
放宽隧道 `AllowedIPs` 到 `172.16.0.0/16`、加 `PersistentKeepalive`、`renew_instance`、
单实例严格串行（`practice_instance_cap=1`）、20 轮节点轮询。均未稳定拿到 estate 服务。

### 4.3 结论

练习场的 engagement 实例当前**部署不完整**（服务清单为空、estate 容器未监听），属于平台侧问题；
我们已在另一场同名比赛（SAMPLE，`e4e78ff…`）用同一套工具解出该题，证明接入/利用能力本身没有问题。

---

## 5. 复现与脚本

```powershell
$env:CTFY_LOCAL_BRIDGE='1'
$env:CTFY_LOCAL_BRIDGE_DIR='E:\ctfy_project\runs\_mon\bridge_work'
$env:CTFY_WIREPROXY='E:\ctfy_project\.tools\wireproxy\wireproxy.exe'
python -m ctfy_agent --run-id playground auto --order id --status new instance_up
```

单题/取证脚本（`runs/_mon/`）：

| 题 | 脚本 |
|---|---|
| 005 | `case_005.py` |
| 006 | `case_006_matrix.py`（deface → verify，含判分通道矩阵） |
| 009 root | `case_009_root3.py` |
| 019 | `case_019_v3.py` |
| 022 | `case_022_scan.py`（estate DNS + 端口扫描，vault 在 8090） |
| 023 | `case_023_battle.py` / `case_023_single.py` / `case_023_renew.py` / `case_023_recover*`（多策略重试收割机） |
| 成绩快照 | `snapshot_both.py`（两场比赛逐题状态，写 `runs/_mon/evidence_both.json`） |

## 6. 本轮 git 提交

- `2dc31d9` fix(solver): verify_exploit 退避重试（judge 侧车就绪窗口）
- `ef2637d` docs(playground): 练习场 16/17 结果与证据
- `5d8cf77` fix(netbridge): WireGuard 配置补 PersistentKeepalive=25
- `27570ee` docs(playground): 收尾报告 16/17（证据链）

## 7. 时间线观察（平台侧）

| 时间（本地） | 现象 |
|---|---|
| 01:50 前后 | SAMPLE-023 estate 成功一次：portal:80 在 t+120s 返回 200 |
| 02:20 | SAMPLE-023 estate 成功一次：files:80 在 t+36s 返回 200（`file host online; request /flag`） |
| 04:00 之后 | 同一策略起 40+ 个实例（节点 .3/.4/.5/.6/.7/.8/.9/.10/.11/.12，覆盖两个比赛），`services` 恒为 0、estate 端口恒 `connection refused` |
| 07:0x（同一会话内，第 18 轮，node 10.90.0.11） | **portal:80 在 t+56s 起**并稳定返回 `DocuVault portal…`；但 files 在该轮 150s 窗口内仍未起 —— 证明 estate 是「能起来但很慢/很不稳定」，而脚本到窗口就停实例是当时的短板（已改为 portal 起来后保活再等 files 300s，见 `runs/_mon/case_023_catch.py`） |
| 07:3x | 6 轮 portal-first 收割未再命中 portal |
| 05:45 | 补充 `PersistentKeepalive`、放宽 `AllowedIPs`、`renew_instance`、单实例串行（cap=1）均无效 |

推测：凌晨某个时间点后，练习场/ SAMPLE 比赛的 engagement（estate）部署整体降级；这与「其他队伍计数仍在涨」并不矛盾
（计数是累计值，且他们可能落在缓存已热的节点上）。

## 8. 全量核对命令（一次跑完 17 题）

```powershell
$env:PYTHONPATH='src'
python runs/_mon/final_verify.py --both
# 输出：runs/_mon/final_verify.md（表格）+ runs/_mon/final_verify.json（机读）
```

2026-11-14 05:45 快照结果：练习场看板 **14/17**、`get_my_solves` **16/17**；
SAMPLE 比赛看板 **15/17**（含 SAMPLE-023），QA 两题同为 0 计数。

## 9. 附录：inal_verify.py --both 输出（2026-11-14 08:32 收尾快照）

# 练习场最终核对（2026-11-14 08:32:30）

### 平台练习场

| 题 | 平台计数 | 团队状态 | 我们已解的题号 |
|---|---|---|---|
| SAMPLE-001 | 213 | ✅ 完全解出 | flag, slug |
| SAMPLE-003 | 171 | ✅ 完全解出 | flag |
| SAMPLE-005 | 0 | ⚠️ 平台有 solve 记录但看板未标完全解出 | - |
| SAMPLE-006 | 63 | ✅ 完全解出 | deface |
| SAMPLE-007 | 54 | ✅ 完全解出 | outage |
| SAMPLE-008 | 118 | ✅ 完全解出 | persist |
| SAMPLE-009 | 44 | ✅ 完全解出 | flag, root |
| SAMPLE-015 | 124 | ✅ 完全解出 | flag |
| SAMPLE-016 | 161 | ✅ 完全解出 | flag |
| SAMPLE-017 | 145 | ✅ 完全解出 | flag |
| SAMPLE-018 | 156 | ✅ 完全解出 | flag |
| SAMPLE-019 | 41 | ✅ 完全解出 | counter |
| SAMPLE-020 | 0 | ⚠️ 平台有 solve 记录但看板未标完全解出 | - |
| SAMPLE-021 | 126 | ✅ 完全解出 | flag |
| SAMPLE-022 | 85 | ✅ 完全解出 | flag, gate |
| SAMPLE-023 | 108 | ❌ 未解 | - |
| SAMPLE-024 | 103 | ✅ 完全解出 | flag |

平台看板完全解出：**14/17**；get_my_solves：**16** 题

### SAMPLE 比赛（对照）

| 题 | 平台计数 | 团队状态 | 我们已解的题号 |
|---|---|---|---|
| SAMPLE-001 | 128 | ✅ 完全解出 | flag, slug |
| SAMPLE-003 | 38 | ✅ 完全解出 | flag |
| SAMPLE-005 | 0 | ⚠️ 平台有 solve 记录但看板未标完全解出 | - |
| SAMPLE-006 | 103 | ✅ 完全解出 | deface |
| SAMPLE-007 | 73 | ✅ 完全解出 | outage |
| SAMPLE-008 | 85 | ✅ 完全解出 | persist |
| SAMPLE-009 | 47 | ✅ 完全解出 | flag, root |
| SAMPLE-015 | 31 | ✅ 完全解出 | flag |
| SAMPLE-016 | 66 | ✅ 完全解出 | flag |
| SAMPLE-017 | 59 | ✅ 完全解出 | flag |
| SAMPLE-018 | 63 | ✅ 完全解出 | flag |
| SAMPLE-019 | 33 | ✅ 完全解出 | counter |
| SAMPLE-020 | 0 | ⚠️ 平台有 solve 记录但看板未标完全解出 | - |
| SAMPLE-021 | 37 | ✅ 完全解出 | flag |
| SAMPLE-022 | 40 | ✅ 完全解出 | flag, gate |
| SAMPLE-023 | 26 | ✅ 完全解出 | flag |
| SAMPLE-024 | 19 | ✅ 完全解出 | flag |

平台看板完全解出：**15/17**；get_my_solves：**22** 题


### 08:00–08:40 最后一轮尝试（收尾前）

- portal-first 收割再跑 6 轮（节点 .8×3 / .5 / .12×2），portal 均未起来；
- 结论不变：SAMPLE-023 在练习场的 estate 部署整晚不稳定，除 07:0x 那一次 portal 短暂可用的窗口外，无法稳定打通；
- 截至收尾，平台没有任何未清理的实例，后台任务全部结束。
