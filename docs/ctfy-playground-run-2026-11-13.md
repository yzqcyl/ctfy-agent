# 平台练习场（SAMPLE 批次）全量跑结果 — 2026-11-13（更新：15/17）

比赛：`平台练习场`（`bc60dc1764f34413964a547102f17261`，17 道 SAMPLE 题，practice 形态）。
本机无 WireGuard/TUN 权限，数据面走自研用户态隧道：`netbridge`（wireproxy 子进程）
+ `portmap`（socket 层透明改写 + 隧道内 DNS）。

## 1. 结果：15/17 解出

| 题 | 形态 | 结果 | 备注 |
|---|---|---|---|
| SAMPLE-001 | 单服务 Web | ✅ | agent 自动 |
| SAMPLE-003 | Web | ✅ | agent 自动 |
| SAMPLE-005 | 平台问答 | ✅ | agent 自动 |
| SAMPLE-006 | checker「改 banner」 | ❌ | 利用确定生效（banner 已改，页面/接口都变），`verify_exploit` 恒 `passed=false/exit_code=1`；同形态的 019 在平台侧能通过 → 该题 checker 侧车/答案注入问题 |
| SAMPLE-007 | Web | ✅ | agent 自动 |
| SAMPLE-008 | Web | ✅ | agent 自动 |
| SAMPLE-009 | 泄漏 token → 动态 flag | ✅ | 手动收口：`GET /api/debug/config` 拿 `service_token` 提交即解出 |
| SAMPLE-015 | UDP 服务 | ✅ | 需要 UDP 隧道 + 同源端口复用（`udp` 工具） |
| SAMPLE-016 | 预解 | ✅ | 平台侧已完成 |
| SAMPLE-017 | Web | ✅ | agent 自动 |
| SAMPLE-018 | Web | ✅ | agent 自动 |
| SAMPLE-019 | checker「计数器 ≥3」 | ✅ | **时序问题**：实例 `ready` 后 judge 侧车还要几十秒才可用，早打恒 `exit_code=1`；等 ~40s 再提交计数器当前值 → `correct=true`、`solve_rank=19`。已在 `solver.py` 加 `verify_exploit` 退避重试 |
| SAMPLE-020 | 纯问答（三道） | ✅ | 直接作答：Q1 `B`、Q2 `A,B,D`、Q3 `requires`（依据 CTFY-0002 §2.5/§4.1，用 `submit_qa_answer`） |
| SAMPLE-021 | Web | ✅ | agent 自动（本轮 86.5 万 token） |
| SAMPLE-022 | 双服务 gate→vault | ✅ | gate 在 `auth`（surface 已给），**vault 不在 `surface.services` 也不在 `entry_urls`**；由 estate DNS 解析 `vault.<stem>.internal` → 对 IP 扫端口，实际监听 **`8090`**；拿 gate 打 `/flag?gate=` → `FLAG{...}`，`challenge_fully_solved=true`（solve_rank 49） |
| SAMPLE-023 | estate + edge-DNS | ❌ | 隧道通、`portal/files.sample023.internal` 经 estate DNS 解析成功（IP 在实例 `/24` 内），但 1–3000 端口扫描无监听（多数 `refused`）——estate 服务未真正下发（平台侧） |
| SAMPLE-024 | OOB 回调 | ✅ | 入站 `[TCPServerTunnel]` 已实测打通，agent 靠回调拿 flag（182 万 token） |

`get_my_solves`（按 competition 过滤）已覆盖 15 题；剩余 2 题（006/023）的失败点都在平台侧部署，不在本机接入。

## 2. 关键教训（都是「自身原因」先排查完之后定位到的）

1. **实例 `ready` ≠ judge 就绪**（019 实证）：起实例后立刻 `verify_exploit`/`submit_answer`
   会稳定失败；应用层先访问 30–60s 再判定。`solver.py` 已加 `verify_exploit` 退避重试
   （20/25/30s），不再把「时机未到」当「利用无效」。
2. **`surface.services` / `entry_urls` 会漏服务**（022 实证）：vault 既没进 services，
   `entry_urls` 只有 portal/files；真实端口要扫。补充手段：estate DNS 解析
   `vault.<stem>.internal` → 对 IP 做端口扫描（`runs/_mon/case_022_scan.py`）。
3. **本地大白话排障顺序**：先确认 VPN 隧道与 DNS 通 → 再确认 TCP 端口有监听 →
   最后才怀疑 judge/checker；不要一上来就把题目判成「平台坏了」。

## 3. 仍未解 2 题

1. **SAMPLE-006（checker）**：改 banner 已确认生效（`POST /api/banner` + `X-Internal-Request: true`，
   页面/接口均变），`verify_exploit` 任何 payload 下都是 `exit_code=1`；同形态 019 能通过，
   差异在题目判分脚本/答案注入，需平台侧修。
2. **SAMPLE-023（estate + edge-DNS）**：estate DNS 能解析出 `portal/files` 的实例内 IP，
   但 1–3000 全无监听；`entry_urls` 的 80 端口也 `refused`。等平台把 estate 服务真正起来；
   `runs/_mon/case_023_wide.py` 可直接重扫。

## 4. 复现

```powershell
$env:CTFY_LOCAL_BRIDGE='1'
$env:CTFY_LOCAL_BRIDGE_DIR='E:\ctfy_project\runs\_mon\bridge_work'
$env:CTFY_WIREPROXY='E:\ctfy_project\.tools\wireproxy\wireproxy.exe'
python -m ctfy_agent --run-id playground auto --order id --status new instance_up
```

单题手动收口脚本（`runs/_mon/`）：`case_009.py`（009）、`case_019_v3.py`（019）、
`case_022_scan.py`（022，含非标准端口扫描）、`case_023_wide.py`（023 端口扫描）、
`case_006_variants.py`（006 多种 payload）。