# CTFY「平台练习场」接入测试（2026-11-13）

> 目的：把 agent 接到 CTFY 平台，在已报名的练习场比赛里做一次**接入测试**，并把测试中暴露的缺口补上。
> 原则：探测阶段只做**只读探测 + 实例生命周期演练**；数据面打通后，**只交 1 个 flag**（SAMPLE-016，
> 用于验证判题链路，练习场不计名次）。所有数值都是 2026-11-13 实机回执，不是文档抄来的。

## 1. 结论

| 链路 | 结果 | 证据 |
|---|---|---|
| MCP 握手 / 工具目录 | ✅ | 平台自报 `ctfy 3.4.6`、协议 `2025-06-18`、无状态、**96 个工具** |
| 身份 / 报名 | ✅ | `yzqcyl's team`，role=captain，`is_registered=true`、`can_participate=true` |
| 题单读取 | ✅ | 17 道 SAMPLE-xxx（001/003/005/006/007/008/009/015/016/017/018/019/020/021/022/023/024） |
| 题目附件 | ✅ | `list_challenge_attachments` 4 题；`download_challenge_attachment` 回 base64 内容模式 |
| 实例生命周期 | ✅ | `start_instance→get_instance→get_instance_status→stop_instance` 全部走通（SAMPLE-016） |
| 实例**数据面** | ✅ | 平台 REST 取 WireGuard 配置 + 用户态隧道（wireproxy，免管理员）→ 读到靶场 banner → 拿到真 flag |
| 提交/判题 | ✅ | `submit_answer` → `correct=true`、`solve_rank=16`、`challenge_fully_solved=true`；`get_my_solves` 复核在册 |
| 局间暂停 | ⚠️ 已识别 | 4h 分场次，局间 start 直接 403 `competition_paused`；旧代码会当瞬时错误重试 → 已改成「等下一局」 |

一句话：**控制面 + 数据面 + 判题全通**；代码侧本次补了 7 处（§6），其中 4 处是只有 CTFY 才会踩的坑
（`result` 单数包裹、`competition_paused`、跨比赛 `my_solved_ids` 串场、`get_challenge` 逐题状态不可信）。

## 2. 平台事实（`get_competition` 实测）

比赛 `bc60dc1764f34413964a547102f17261` = **Platform Playground（平台练习场）**，`status=published`，
`starts_at=2026-11-13T00:00:00Z`、`ends_at=2026-11-15T00:00:00Z`。

| 字段 | 实测值 | 对 agent 的意义 |
|---|---|---|
| `tcp_access` | `vpn` | TCP 服务只能经平台 VPN 访问；直连 `*.internal` 必失败 |
| `agent_internet` | `deny` | 靶机无外网 → 带外交互只能回连平台回调地址 |
| `internet_policy` | `allow` | 被考核方（agent 容器）出网策略 |
| `practice_instance_cap` | `1` | 练习场同一时刻只有 1 个实例：开新的前必须先停旧的 |
| `practice_ttl_s` | `3600` | 练习实例 1h 回收（实例 `get_instance.ttl` 实测 10800 是题目级 TTL 覆盖） |
| `max_instances_per_team` | `0`（`max_instances_per_team_effective=3`） | 0 不表示禁用，实际并发按 effective |
| `max_rebuilds_per_challenge` | `0` | 不能靠 `reset_instance` 重建靶场，只能 stop→start |
| `solve_env_mode` | `grace` | 放题/关闭窗口有宽限语义，403/404 要先看阶段 |
| `at_capacity` / `capacity_counts` | `stop` / `submitted` | 提交即计入容量 |
| `sessions` | 12 场 × **3h45m**（00:00–03:45Z、04:00–07:45Z、… 每 4h 一场，共 2026-11-13~14） | 局间 `play_open=false`，起实例直接 403 `competition_paused` |
| `session_end_mode` / `session_end_grace_s` | `delete` / `900` | **局末实例会被删**：跨局不能复用实例，解法要能 stop→start 重来 |
| `current_session_ends_at` / `next_session_starts_at` | `…T07:45:00Z` / `…T08:00:00Z` | 每局 15 分钟空档；`play_open=true` 才是可打状态 |

题目形态（与本地 SAMPLE 训练库同源，但平台会给**练习专用 flag**，公开 flag 永远被拒）：
`questions[{id, prompt, mode, choices, requires, share_percent}]`，`mode ∈ {dynamic, static, checker, single_select, multi_select}`；
`is_qa_only`、`flag_solves`（题号→解出数）、`my_solved_question_ids`、`attachments[{name,size,per_team}]`、`launch_ttl_s`、`surface`。

## 3. 只读取证（做了什么）

```
doctor / whoami                              → ctfy 3.4.6、96 工具、team=yzqcyl's team
list_competitions                             → 找到练习场 id（只报已报名的那场）
get_competition                               → §2 全部约束
list_competition_challenges                   → 17 题
get_challenge × 17                            → questions/mode/is_qa_only/attachments/flag_solves
list_competition_attachments                  → []（本场没有比赛级附件）
list_challenge_attachments × 5 题             → 001 setup.txt(per_team) / 005 archive.zip / 018 welcome.txt(per_team) / 021 greeter / 015 README.txt
download_challenge_attachment(SAMPLE-021)     → {filename: greeter, size: 16376, encoding: base64, content: 43875 字符} → 解码为 ELF
list_instances                                → {"result": []}
start_instance(SAMPLE-016) → ... → stop       → §4
```

## 4. 实例回执形状（SAMPLE-016「Check-in (pwn)」实测）

`start_instance` 回执（节选）：

```json
{
  "id": "782e6892-e55e-4d75-8136-85766877d6fb",
  "surface": {
    "services": [{"service_type": "tcp", "host": "srdaqhkfl5agrntxi.internal", "port": 1337,
                  "label": "chal", "access": "vpn", "credentials": null, "path": "/"}],
    "provided_files": [{"name": "README.txt", "size": 595, "per_team": false}],
    "flag_path": null,
    "vpn": {"proto": "udp", "host": "node2.tpctf.ctfy.run", "port": 42616, "config_url": "",
            "callback_host": "ctfy-callback", "callback_peer": "172.16.13.254"}
  },
  "cert_volume": ""
}
```

`get_instance` 补充：`status=ready`、`ttl=10800`、`expires_at`、`node_id=10.90.0.4_8101`、`internet=allow`、
`questions[...]` 带 `unlocked` 字段。`stop_instance` 正常释放，之后 `list_instances` 回到 `{"result": []}`。

两个**只有 CTFY 有**、旧版没用的信息：
1. `surface.vpn.callback_host / callback_peer` —— OOB 题（SAMPLE-024 这类）里靶机看到的回调身份；
   旧版提示模型「用自己机器的 IP/localhost」，在这种平台必错。
2. `list_instances` 的 **`result`** 单数包裹 —— 旧归一化不认，等于「看不见实例」（§6 第 3 条）。

## 5. 数据面：VPN 怎么接（已打通，**不需要人工装 GUI 客户端**）

### 5.1 结论

平台**不要求你提供一个 VPN 应用**，也不是「打开某个 VPN」就行：每个实例都会签发一份**独立的
WireGuard 配置**（私钥每实例不同），用 REST 接口取，导入任意 WireGuard 客户端即可。

```
GET /api/v1/instances/{instance_id}/vpn-config?competition_id=…     ← 主接口（openapi.json 里的官方名）
GET /api/v1/instances/{instance_id}/openvpn-config                  ← 兼容别名（返回同一份 .conf）
GET /api/v1/competitions/{cid}/instances/{instance_id}/openvpn-config
→ 200, Content-Type: application/x-wireguard-profile, ~286B, filename="ctfy-<uuid前16>.conf"
```

实测内容（SAMPLE-016 实例，私钥已打码）：

```ini
[Interface] Address = 172.16.9.254/32   PrivateKey = ***   DNS = 172.16.5.3, 1.1.1.1, 8.8.8.8
[Peer]      PublicKey = TFpU8/…         Endpoint = node6.tpctf.ctfy.run:42177
            AllowedIPs = 172.16.5.0/24  PersistentKeepalive = 25
```

注意 `Address` 与实例回执里的 `surface.vpn.callback_peer` 是**同一个地址**：这就是靶机做 OOB 回调时
看到的「你」。`surface.vpn` 里没有私钥/public key，`config_url` 也是空串 —— 只靠 `surface` 造不出隧道，
必须走上面这个 REST 接口（MCP 96 个工具里**没有**任何 VPN 工具，已全量扫描确认）。

### 5.2 本机（Windows、非管理员）怎么连：用户态 wireproxy，免 TUN / 免管理员

WireGuard for Windows 需要管理员装驱动；本机没有客户端、进程也不是管理员。改走
[`wireproxy`](https://github.com/windtf/wireproxy)（10 MB 单文件，用户态实现 WireGuard，把流量转成
本地 SOCKS5 / TCP 隧道，**完全不动网卡**）：

```powershell
# 1) 起实例，取配置（脚本 runs/_mon/dp_step1_start.py，产物在 gitignored 的 runs/_mon/vpn_probe/）
# 2) 配置 = 平台给的 [Interface]+[Peer] 原样 + 追加：
#    [Socks5]        BindAddress = 127.0.0.1:25344
#    [TCPClientTunnel] BindAddress = 127.0.0.1:19100   Target = <rand>.internal:1337
#    [Resolve]       ResolveStrategy = auto
.\.tools\wireproxy\wireproxy.exe -c runs\_mon\vpn_probe\wireproxy.conf
# 3) 之后当成本地端口用（pwntools / nc 都行）
```

实测链路：`127.0.0.1:19100` → wireproxy → WireGuard(UDP 42177) → `sa57uywzpeyxra7kh.internal:1337`
→ banner `Welcome to the check-in box.` → `cat flag` → `FLAG{1a88000428febb068c2b04b557ca2fbf}`
→ `submit_answer` → `correct=true / solve_rank=16 / challenge_fully_solved=true`。

> 定位：这条桥只用于**本地开发/验收**，不进镜像（正式比赛容器里隧道由平台注入，见 §7）。
> 二进制度和私钥都不入库：`.tools/`、`runs/` 已在 .gitignore；落盘配置一律把 `PrivateKey` 打码。

### 5.3 本机现状（2026-11-13 实测）

```
wireguard / wg / openvpn / ncat / nc   → 均未安装；无 WireGuard/OpenVPN 安装目录；进程非管理员
WireGuard 客户端（GUI）               → 用户想用也可以：装 WireGuard for Windows，导入上面那份 .conf，启用即可
```

## 6. 本次补齐的功能（代码）

> 与已有能力的分工：`tools_local._dns_unreachable_advice`（O-G）和 crew 的「接入层不可达 = 本题到此为止」
> 纪律负责**事后**判据（DNS 不通时一步收口）；本次补的是**事前**事实注入（要不要 VPN、怎么连、回调是谁）
> 和协议感知的就绪探测，两者互补、不重复。

1. **`src/ctfy_agent/access_facts.py`（新模块，纯函数）**
   - `competition_access_lines(comp)`：把 `tcp_access=vpn`、`agent_internet=deny`、
     `practice_instance_cap`、`practice_ttl_s`、团队并发等翻成可照做的提示；
   - `instance_access_lines(instance)`：把 `surface` 翻成服务清单（tcp/udp/http + access）、
     VPN 告警（`*.internal` 必先连 VPN）、OOB 回调身份、`per_team` 附件要用实例附件工具、
     `flag_path`；
   - `readiness_probes(services)`：按 `service_type` 生成就绪探测计划（http/https→GET，
     tcp→socket connect，udp→**不探测**，去重）。
2. **`solver`**
   - `start_instance` 回执追加 `[接入提示]`（并落转录），模型第一眼就看到「怎么连/回调是谁」；
   - `_wait_instance_ready` 改为协议感知：tcp 实例不再拿 `http://` 白等 45s、udp 不探测、
     多服务逐个探；仍不就绪时，若 `access=vpn`/`*.internal`，明确写「大概率是隧道没连」。
3. **`crew`**：协调者简报前置「本场接入约束（平台事实）」= 比赛级约束 + 当前实例 surface，
   同一 `competition_id` 只拉一次（`get_competition` 一次读）。
4. **`list_instances` 的 `{"result": [...]}` 包裹**（`instance_guard._items` / `solver._find_live_instance`
   / `solver._evict_stale_instances` 三处统一）：不认就等于「看不见实例」——`reap()` 清不到残留
   （配额泄漏）、`_evict_stale_instances` 在配额 1 的场次永远清 0、`_find_live_instance` 看不到
   本题已有实例而重复 start。
5. 顺手修一个并行提交引入的回归：`auto_start_allowed` 的类别跳过（misc/crypto/reverse）盖掉了
   人工逃生口 `instance_absent_retry=true`，导致 `smoke_instance_absent` 失败 → 逃生口优先。
6. **[新·高优] `my_solved_ids()` 跨比赛串场（会直接漏题）**：练习场实测 `get_my_solves` 不带比赛 id 回
   **232 条**（CTFARENA/SAMPLE/SQLILABS/… 全局），带练习场 id 只回 **1 条**，带没打过的 TPCTF 回 **0 条**。
   题 id 又跨比赛重复（SAMPLE-016 同时在 SAMPLE 套题与练习场）→ 旧版永远查全局，
   等于「在别的比赛解过 = 本场已解」，预检直接短路跳过没做的题（最坏：整场一个 flag 都不交）。
   修复：`my_solved_ids(competition_id=…)` 按比赛查询 + 按比赛分键缓存 10s；`call_text` 记住最近一次
   带过的 `competition_id`（零参调用也收窄）；crew 4 处预检/兜底改走 `_platform_solved_ids()`，
   **比赛上下文未知时返回空集合**（宁可多花一次解题成本，也不误判已解）。
7. **[新] 局间暂停不能当瞬时错误重试**：练习场按 4h 分场次（00:00–03:45 / 04:00–07:45 / … UTC），
   局间 `start_instance` 回 `403 competition_paused: play resumes at 2026-11-13T04:00:00+00:00`。
   旧代码把它当网络/限流抖动反复重试 → 白烧实例操作次数。`solver._is_competition_paused()`
   识别 `competition_paused` / `paused between sessions`，回滚本次实例操作计数并给「等下一局」指引，
   不再走瞬时重试（区分于 `at_capacity`/`rate_limited`，测试覆盖 false 分支）。
8. **[新] 判题事实源按 CTFY 校准**：
   - `submit_answer` 的 schema 是 **`instance_id` + `answer`**（`question_id` 默认 `flag`），没有 `challenge_id`；
   - 回执字段：`correct / solve_rank / solve_time_s / challenge_fully_solved / attempts_remaining`；
   - `get_challenge` 的 **`my_solved_question_ids` / `my_fully_solved` 在 CTFY 上不可信**：SAMPLE-016 已
     `correct=true` 且 `get_my_solves` 在册，`get_challenge` 仍回 `[]` / `false`（`flag_solves` 只给全局计数）。
     → 多问题题（020 三问、022 两问）只能靠提交回执 + 本地 `answers_by_qid`，不能等平台回填逐题状态。

回归：`tests/smoke_access_facts.py`（27 项）、`tests/smoke_instance_list_wrapper.py`（11 项）、
`tests/smoke_solved_scope.py`（16 项，新增）。

## 7. 下一步

1. **正式赛容器里的隧道**：练习场是「选手自己连 VPN」形态；正式赛（TPCTF Agent Track）容器里
   隧道由平台注入，agent 不需要自己起 WG。但要**实测确认**一次（拿 task.json 的
   `vpn_endpoint`/instances 字段对一下），避免把本地 wireproxy 桥当成产线依赖。
2. **剩余数据面形态**（练习场每局可做，1 格实例、按 stop→start 串行）：SAMPLE-015（UDP，验证我们
   「UDP 不探测」的就绪逻辑）、SAMPLE-022（多服务）、SAMPLE-024（OOB 回调，验证
   `callback_host/callback_peer` 指引）、SAMPLE-018/001（per-team 附件）。
3. **真实解题验收**（会烧 LLM token + 平台提交次数）：练习场不计名次，适合跑 1 道端到端；
   建议等并行会话测试结束后再跑，避免抢 1 格实例配额。
4. 题库口径不变：本地 SAMPLE 17/17 已通过；外部 26 题 13/26（数据早于 v7 库）；本场 flag 由平台重签，
   公开 flag 必被拒 —— 不能用「公开 flag 被拒」当失败证据。