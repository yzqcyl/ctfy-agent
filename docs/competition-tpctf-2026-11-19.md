# TPCTF 2026 · 智能体赛道 · 选手 VPS 测试赛 — 环境配置与自动化（2026-11-18 夜）

> 用户指示：**全程监视、自己决断、跑到正确为止、明早 9 点前结束所有工作**。
> 本文记录：比赛信息、环境怎么配的（平台/实例/VPN 的真实机制）、自动化怎么跑、怎么监控、风险兜底。

## 1. 比赛信息（平台 API 实查）

| 项 | 值 |
|---|---|
| 比赛 | `TPCTF 2026 · Agent Track · VPS Access` |
| competition_id | `1de3339befe549b2b740cdcf92994451` |
| 时间 | 2026-11-19 **02:00–07:00 UTC = 北京时间 10:00–15:00**（5 小时） |
| 队伍 | 一只七彩尧狼（captain yzqcyl，资格 fully_eligible） |
| 题目 | `challenges_withheld: true`，**开盘才公布**（现在 0 题） |
| 配额 | 每队 **3 个并发实例** |
| 接入 | VPN（WireGuard）——“用平台提供的 VPS 与 VPN 配置接入题目环境” |
| 参赛方式 | 邀请制、报名关闭；每队自己跑 agent（非平台托管镜像，所以“镜像上传 VPS”= 我们自己的 VPS 里跑） |

## 2. 环境真相（实测出来的，不是猜的）

1. **平台 MCP**：`https://ctfy.tsinghua.edu.cn/api/v1/mcp/`（99 个工具），`.env` 的 `CTFY_TOKEN` 直连可用，
   `get_challenge / start_instance / submit_answer / stop_instance` 全部验证通过。
2. **实例的 VPN 是「按实例签发」的**：`start_instance` 回执里
   ```json
   "surface": {"vpn": {"proto": "udp", "host": "node1.tpctf.ctfy.run", "port": 44372,
                       "config_url": "", "callback_host": "ctfy-callback"}}
   ```
   `config_url` 为空 → **agent 要自己用平台 REST 拉配置文件**：
   `GET https://ctfy.tsinghua.edu.cn/api/v1/instances/<id>/vpn-config?competition_id=<cid>`（Bearer=CTFY_TOKEN）。
   这正是仓库里 `src/ctfy_agent/netbridge.py` 的实现（用户态 wireproxy + portmap 改写 socket）。
3. **镜像 v8 里没有 wireproxy**（`.tools/` 被 .dockerignore 排除）：`netbridge` 找不到 wireproxy →
   所有依赖隧道（`*.internal` / `access=vpn`）的题**必然连不上**。
   **修复**：把 `wireproxy 1.1.3`（linux/amd64 静态、10.4MB）放到
   `/root/ctfy_practice/repo/.tools/wireproxy/wireproxy`（比赛容器 `-v repo:/work`，netbridge 自动找到）。
4. **不需要宿主机级 WireGuard**：per-instance 配置 → wireproxy → 本地端口 → portmap 透明改写，
   已用 SAMPLE-023 验证。宿主机 wg 接口只是兜底（`/root/vpn/wg0.conf` 槽位，watch.sh 会自动 up）。
5. **提交必须带 instance_id**：`submit_answer` 必填 `instance_id`（无 container 的题也一样，先
   `start_instance` 拿 id）。实测提交错误值 → `{"correct": false}`，链路正常。

## 3. 验证证据（2026-11-18 夜，VPS 实测）

| 验证 | 结果 |
|---|---|
| 平台链路（MCP + 实例 + 提交） | ✅ dummy 提交返回 `correct:false`，`stop_instance` 正常 |
| **SAMPLE-023（easy web，需 WireGuard 的 engagement 题）** | ✅ **解出**：2.8 分钟，flag `FLAG{0de8d4c5e88996886d3ecc1336664b51}`，平台记分 |
| host-network 容器 + 已解题跳过 | ✅ SAMPLE-016 秒过（0 token） |
| `comp_auto run` 全流程彩排 | ✅ 抓题→矩阵选题→run_batch→monitor.log→analysis.md，8/8 已解跳过，0 软失败 |
| v8 镜像 | ✅ GHCR 匿名可拉，digest `sha256:b1d975fd…`，VPS 已载入 |

SAMPLE-023 的意义：它同时验证了 **平台 API + 实例 + per-instance WG 配置拉取 + wireproxy 桥 + TCP 访问
+ flag 校验 + 提交记分** 整条链路——比赛环境已确实打通。

## 4. 自动化：到点开跑（VPS 上，已启动）

```
/root/comp/watch.sh          # 主看门狗（已在跑，pid 见 pgrep -f watch.sh）
  ├─ 装 VPN（若 /root/vpn/wg0.conf 存在）
  ├─ 拉比赛附件（自动识别 WireGuard conf → /root/vpn/wg0.conf）
  ├─ 等到 2026-11-19T01:58Z（北京 09:58）
  ├─ 开盘后二次拉附件/VPN
  └─ docker run ctfy-competition ... tools/comp_auto.py run
        --competition-id 1de3339befe549b2b740cdcf92994451
        --start-at 2026-11-19T02:00:00Z --concurrency 3 --run-id comp-2026-11-19
/root/comp/keeper.sh         # cron 每 5 分钟：比赛容器未起前，watch.sh 挂了就重新拉起
/root/comp/watch.log         # 全量日志
```

`tools/comp_auto.py`（已提交 dff494b）：
- **矩阵**（用户指定）：easy pwn×2/rev×2；medium pwn/rev/crypto/misc 各 2；hard web×4、pwn/rev/crypto/misc 各 2 = 24 题；
- 执行顺序 **easy → medium → hard**（同难度 pwn→rev→crypto→misc→web）；
- 桶不足自动按「同类相邻难度 → 同难度其它类别」补齐，并在报告标注缺口；
- 盘中每 30s 写 `monitor.log`（题数/解出/转录活动）；结束自动 `analyze_batch` 出 `analysis.md`
  （工具软失败、shell 非零、重复动作、环境坑统计）。

## 5. 监控与查看（随时）

```bash
bash /root/comp/status.sh                     # 一屏：容器/进度/解出/未解原因/token/工具分布
docker logs -f ctfy-competition               # 实时批次日志
tail -f /root/ctfy_practice/repo/runs/comp-2026-11-19/monitor.log
# 思考过程（逐题 jsonl）：
ls /root/ctfy_practice/repo/runs/comp-2026-11-19/transcripts/
# 复盘中：cat /root/ctfy_practice/repo/runs/comp-2026-11-19/analysis.md
```
Windows 侧：`python tools/remote_ssh.py --shell-script runs/_ssh/status.sh`

## 6. 风险与兜底

| 风险 | 兜底 |
|---|---|
| 平台另发「队伍级 WireGuard」且必须装宿主机 | 把 conf 放 `/root/vpn/wg0.conf`；watch.sh 起跑前/开盘后都会 `wg-quick up`，容器用 `--network host` |
| 开盘延迟/题目未放出 | comp_auto 每 30s 重试抓题，最多 5 分钟；watch.sh 已提前 2 分钟启动 |
| 实例配额 3 | `--concurrency 3` 与平台上限一致 |
| 预算 | 单题上限 800k；easy 15min/medium 25min/hard 40min 墙钟；总 worst-case 24 题会烧较多 token（正式赛必要开销） |
| 容器/脚本挂了 | keeper cron 每 5 分钟检查；watch.log 留痕 |
| VPS 重启 | cron `@reboot` 未加；keeper 会在重启后 5 分钟内拉起 watch.sh（watch.sh 幂等） |
| 镜像里没有 wireproxy（平台托管跑镜像时） | 本场是自跑（挂 repo），不受影响；**v9 镜像建议把 wireproxy 打进 `/ctfy/.tools/`** |

## 7. 时间线

- 2026-11-18 23:45 起：watch.sh + keeper 就绪，VPS 待命；
- 2026-11-19 09:58：watch.sh 启动比赛容器；
- 10:00：开盘，comp_auto 抓题→选题→easy 优先开跑；
- 盘中：`status.sh` / `transcripts/` 监视思考过程与问题；
- 15:00 收赛：`analysis.md` 复盘（解出数、token、问题统计）。

## 8. 相关提交

- `dff494b` feat(comp): comp_auto + fetch_comp_attachments（本文自动化）
- `c6372e6` docs: v8 构建成功（digest/体积/契约）
- `c0ff580` fix(image): 构建期能力自检
- SAMPLE-023 验证证据：`runs/wg-test-sample023/`（VPS 上）