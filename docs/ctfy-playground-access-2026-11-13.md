# CTFY「平台练习场」接入测试（2026-11-13）

> 目的：把 agent 接到 CTFY 平台，在已报名的练习场比赛里做一次**接入测试**，并把测试中暴露的缺口补上。
> 原则：本次只做**只读探测 + 一次实例生命周期演练**，不提交任何答案（另一并行会话在同一平台跑测试，
> 团队练习场实例配额只有 1 格）。所有数值都是 2026-11-13 实机回执，不是文档抄来的。

## 1. 结论

| 链路 | 结果 | 证据 |
|---|---|---|
| MCP 握手 / 工具目录 | ✅ | 平台自报 `ctfy 3.4.6`、协议 `2025-06-18`、无状态、**96 个工具** |
| 身份 / 报名 | ✅ | `yzqcyl's team`，role=captain，`is_registered=true`、`can_participate=true` |
| 题单读取 | ✅ | 17 道 SAMPLE-xxx（001/003/005/006/007/008/009/015/016/017/018/019/020/021/022/023/024） |
| 题目附件 | ✅ | `list_challenge_attachments` 4 题；`download_challenge_attachment` 回 base64 内容模式 |
| 实例生命周期 | ✅ | `start_instance→get_instance→get_instance_status→stop_instance` 全部走通（SAMPLE-016） |
| 实例**数据面** | ⛔ 被 VPN 卡住 | `tcp_access=vpn`，本机没有 VPN 适配器/内网路由 → `*.internal` 不可达（见 §5） |
| 提交/判题 | ⏸ 未做 | 避免与并行会话抢平台提交次数；且依赖实例数据面 |

一句话：**控制面全通，数据面待 VPN**。代码侧本次补了 4 处（§6），其中两处是只有 CTFY 才会踩的坑。

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

## 5. 阻塞点：本机没连 VPN（需要人工动作）

`tcp_access=vpn` 且实例 host 是 `*.internal`，实测本机：

```
Get-NetAdapter（Up）      → 无 WireGuard/TUN 适配器
Get-NetRoute 10./172.16-31/192.168 → 无内网路由
```

→ 数据面（nc/curl 打 `srdaqhkfl5agrntxi.internal:1337`）从这台机器**不可能通**。
要在本机做实例级解题验证，需先在平台实例页/比赛页取 VPN 配置（`surface.vpn` 给了端点
`udp://node2.tpctf.ctfy.run:42616`）并连上。**在比赛容器里跑时**这一层由平台注入，通常无需自己连。

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

回归：`tests/smoke_access_facts.py`（25 项）、`tests/smoke_instance_list_wrapper.py`（11 项）。

## 7. 下一步（需用户拍板）

1. **连 VPN**：本机连上平台 VPN 后，可做真正的数据面验证（建议顺序：SAMPLE-016 pwn 签到 → 015 UDP →
   022 多服务 → 024 OOB 回调 → 001 per-team 附件）。要我跑就说一声，注意练习场实例只有 1 格。
2. **是否要真实解题**（会烧 LLM token + 平台提交次数）：练习场不计名次，适合做端到端验收；
   建议等并行会话的测试结束后再跑，避免抢 1 格实例配额。
3. 题库口径不变：本地 SAMPLE 17/17 已通过；外部 26 题 13/26（数据早于 v7 库）；本场 flag 由平台重签，
   公开 flag 必被拒 —— 不能用「公开 flag 被拒」当失败证据。