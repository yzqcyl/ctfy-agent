# 平台练习场（SAMPLE 批次）全量跑结果 — 2026-11-13

比赛：`平台练习场`（`bc60dc1764f34413964a547102f17261`，17 道 SAMPLE 题，practice 形态）。
本机无 WireGuard/TUN 权限，数据面走自研用户态隧道：`netbridge`（wireproxy 子进程）
+ `portmap`（socket 层透明改写 + 隧道内 DNS）。

## 1. 结果：13/17 解出

| 题 | 形态 | 结果 | 备注 |
|---|---|---|---|
| SAMPLE-001 | 单服务 Web | ✅ | agent 自动 |
| SAMPLE-003 | Web | ✅ | agent 自动 |
| SAMPLE-005 | 平台问答 | ✅ | agent 自动 |
| SAMPLE-006 | checker「改 banner」 | ❌ | 利用已成功（banner 已变），`verify_exploit` 恒 `passed=false/exit_code=1` — **平台 checker 侧车问题** |
| SAMPLE-007 | Web | ✅ | agent 自动 |
| SAMPLE-008 | Web | ✅ | agent 自动 |
| SAMPLE-009 | 泄漏 token → 动态 flag | ✅ | 手动收口：`GET /api/debug/config` 拿 `service_token` 提交即解出 |
| SAMPLE-015 | UDP 服务 | ✅ | 需要 UDP 隧道 + 同源端口复用（`udp` 工具） |
| SAMPLE-016 | 预解 | ✅ | 平台侧已完成 |
| SAMPLE-017 | Web | ✅ | agent 自动 |
| SAMPLE-018 | Web | ✅ | agent 自动 |
| SAMPLE-019 | checker「计数器 ≥3」 | ❌ | 计数器已推到 3，`verify_exploit` 恒 `passed=false/exit_code=1` — 同上 checker 侧车问题 |
| SAMPLE-020 | 纯问答（三道） | ✅ | 直接作答：Q1 `B`、Q2 `A,B,D`、Q3 `requires`（依据 CTFY-0002 §2.5/§4.1，用 `submit_qa_answer`） |
| SAMPLE-021 | Web | ✅ | agent 自动（本轮 86.5 万 token） |
| SAMPLE-022 | 双服务 | ❌ | `start_instance` 一律 `Challenge failed to start on the worker node` — **平台 worker 起不来**，与本机接入无关 |
| SAMPLE-023 | estate + edge-DNS | ❌ | 隧道通、`portal/files.sample023.internal` 经平台解析器解析成功，但 80/443/8080 全部 `connection refused` — estate 内无监听（或服务未下发） |
| SAMPLE-024 | OOB 回调 | ✅ | 入站 `[TCPServerTunnel]` 已实测打通，agent 靠回调拿 flag（182 万 token） |

`get_my_solves`（按 competition 过滤）= 13；未解出的 4 题失败原因都在平台侧或题面本身，
不是「不会做」。

## 2. 本轮补的接入能力（已入库）

- **内网 DNS over 隧道**：`portmap` 劫持 `getaddrinfo`，`.internal` / `.ctfy.run` 走
  WireGuard 配置里的 `DNS =` 服务器（SAMPLE-023 类题的前提）。
- **estate 域名隧道**：`surface.services` 为空时，从题面提取 `*.internal` 域名并按
  80/443/8080/8000/3000 预建 `[TCPClientTunnel]`。
- **OOB 入站**：题面出现 `ctfy-callback:PORT` 时建 `[TCPServerTunnel]`，本地端口独立分配。
- **UDP 工具**：`udp` 角色工具（复用同一 socket，保源端口），SAMPLE-015 实证。
- **「已在运行」回执**：平台回带前缀文本 + 内嵌 JSON 时抠出实例信息，照常登记端点并建桥。
- **白名单放行**：本地桥就绪时允许 `*.internal`/`*.ctfy.run` 目标（原先被合规拦截，
  导致 SAMPLE-023 被误判「接入层不可达」）。
- **提示词**：「内网不可达」判据由「一次 DNS 失败即收口」改为分级判定。

## 3. 未解 4 题的下一步（都需要平台侧先恢复）

1. **checker 类（006/019）**：等平台修 checker 侧车后，`start_instance → 完成效果 →
   `verify_exploit` 即可（利用脚本已备：`runs/_mon/cases2.py`）。
2. **022**：worker 恢复后重试 `start_instance`；双服务题的 gate→flag 流程已按题面写好
   （`runs/_mon/manual_quick.py: case_022`）。
3. **023**：需要平台把 estate 服务真正起来；若起来后仍不通，先看 `runs/_mon/probe_023.py`
   的多端口探测结果再定位。

## 4. 复现

```powershell
$env:CTFY_LOCAL_BRIDGE='1'
$env:CTFY_LOCAL_BRIDGE_DIR='E:\ctfy_project\runs\_mon\bridge_work'
$env:CTFY_WIREPROXY='E:\ctfy_project\.tools\wireproxy\wireproxy.exe'
python -m ctfy_agent --run-id playground auto --order id --status new instance_up
```

单题手动收口脚本（`runs/_mon/`）：`case_009.py`（009）、`cases2.py 019`、`cases2.py 006`、
`manual_quick.py 022`、`probe_023.py`（023 探针）。