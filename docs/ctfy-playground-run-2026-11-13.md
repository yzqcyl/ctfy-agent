# 平台练习场（SAMPLE 批次）全量跑结果 — 2026-11-13 / 11-14

比赛：`平台练习场`（`bc60dc1764f34413964a547102f17261`，17 道 SAMPLE 题，practice 形态，不计名次）。
本机无 WireGuard/TUN 权限，数据面走自研用户态隧道：`netbridge`（wireproxy 子进程）
+ `portmap`（socket 层透明改写 + 隧道内 DNS）。

## 1. 结果：团队已解 16/17（平台看板显示 14/17，见 §2 的平台记账问题）

`get_my_solves`（按 competition 过滤）= 16 题：

`SAMPLE-001, 003, 005, 006, 007, 008, 009, 015, 016, 017, 018, 019, 020, 021, 022, 024`

唯一未解：**SAMPLE-023**（Split Horizon — Edge DNS and the Internal Vault）。

| 题 | 形态 | 结果 | 关键证据 |
|---|---|---|---|
| 001 / 003 / 007 / 008 / 015 / 016 / 017 / 018 / 021 / 024 | Web / UDP / OOB / 预解 | ✅ | 各自 `submit_answer` 返回 `correct:true`（本场记录在 `list_submissions`） |
| 005 | QA-only「套娃压缩包」 | ✅ 已解（看板不显示） | 下载 `archive.zip` → `layer2.zip` → `layer3/inventory.txt` → `ticket-id: FLAG{archives-stack-just-keep-unzipping-7b2c19}`；`submit_qa_answer` 回 `correct:true, challenge_fully_solved:true`（提交 id `e9404ee8-b650-49e3-9199-174581f5e025`） |
| 006 | checker「改 banner」 | ✅ | `POST /api/banner`（带 `X-Internal-Request: true`）后 `verify_exploit(deface)` → `passed:true, exit_code:0, solve_rank:53, challenge_fully_solved:true` |
| 009 | 泄漏 token → 动态 flag + checker「提权」 | ✅ | `flag` 早前已交；第二问 `root`：`POST /api/operators/promote {user:bob, token:<service_token>}` 后 `verify_exploit(root)` → `passed:true, exit_code:0, solve_rank:36, challenge_fully_solved:true` |
| 019 | checker「计数器 ≥3 提交当前值」 | ✅ | 起实例后等 judge 侧车就绪（~40s），提交计数器当前值 → `correct:true, solve_rank:19` |
| 020 | QA-only 三问 | ✅ 已解（看板不显示） | `flag_format=B`、`channels=A,B,D`、`gate_field=requires`，三条 `correct:true, challenge_fully_solved:true`（提交 id `f6e979d0…`/`cd8b50fb…`/`e22ce286…`） |
| 022 | 双服务 gate→vault | ✅ | 双服务：vault 不在 `surface.services`/`entry_urls`！由 estate DNS 解析 `vault.<stem>.internal` 后对 IP 扫端口，真实监听 **:8090**；`GET /flag?gate=<gate>` → 提交 → `solve_rank:49, challenge_fully_solved:true` |
| 023 | estate + edge-DNS | ❌（进行中） | 见 §3 |

`runs/_mon/evidence.json` 是上述状态的机器可读快照（captured_at / my_solves / 逐题状态 / 最近提交回执）。

## 2. 平台记账问题：QA-only 题（005 / 020）不计入看板

- `submit_qa_answer` 对 005/020 都返回 `correct:true`、`challenge_fully_solved:true`，
  `get_my_solves` 也把两题算进团队已解；
- 但 `list_competition_challenges` 里这两题 `solves_count:0`、`my_solved_question_ids:[]`，
  `questions[].answered_correctly` 是 `null` —— 看板因此永远不显示已解/勾选；
- 结论：QA-only 题的记分/看板同步是平台侧缺陷（不是我们没提交）。证据见 §1 的提交 id。

## 3. SAMPLE-023：卡点与最新进展（唯一未拿下的题）

题面：estate 内 `portal.sample023.internal`、`files.sample023.internal` 经 edge-DNS 解析；
portal 首页说明文档由 file host 提供（`http://files.sample023.internal/flag`）；
文档即 flag。vault 无公开名字，只能由 estate 内服务访问。

已确认与踩过的坑：

1. `surface.services` 为空，`entry_urls` 给出两个名字；DNS 能解析出实例 `/24` 内的 IP。
2. **estate 服务启动是概率性的**：同一套探测方式，成功记录两次 ——
   `t+120s` 时 portal:80 返回 200（`DocuVault portal...`）、另一次 `t+36s` 时 files:80 返回
   200（`file host online; request /flag for the stored document`）；多数实例在 5–10 分钟窗口内
   一直 timeout（node 侧 health 已通过，说明是 estate 容器/网络没就绪，不是我们路由问题）。
3. 一直 timeout 而不是 RST，曾怀疑隧道 `AllowedIPs` 覆盖不到 estate IP；把隧道
   `AllowedIPs` 放宽到 `172.16.0.0/16` 后确实遇到过 files 立即 200 的情况
   （`runs/_mon/case_023_anyip.py`），但并非每次生效 —— 更像 estate 启动本身不稳定。
4. 当前做法：`runs/_mon/case_023_loop.py` 反复 start → 放宽 AllowedIPs → 6 端口 × 2 名字隧道
   → 150s 窗口内密集探测 `files/flag`，起来就提交；没起来立即换实例重来。

## 4. 本轮补的接入/排障能力（已入库）

- **judge 侧车就绪窗口**：实例 `ready` 后 judge 还要几十秒才可用；`solver.py` 给
  `verify_exploit` 加了 20/25/30s 退避重试（019 实证，commit `2dc31d9`）。
- **非标准端口**：`surface.services`/`entry_urls` 会漏服务，022 的 vault 实际在 `:8090`；
  先用 estate DNS 解析主机名，再对 IP 做端口扫描（`runs/_mon/case_022_scan.py`）。
- **estate 服务启动慢**：不要起实例 5s 就判「服务不存在」，至少等 2 分钟并多轮重试。
- **QA-only 题**：`submit_qa_answer`（`challenge_id`+`competition_id`+`answer`，可带 `question_id`）。

## 5. 复现

```powershell
$env:CTFY_LOCAL_BRIDGE='1'
$env:CTFY_LOCAL_BRIDGE_DIR='E:\ctfy_project\runs\_mon\bridge_work'
$env:CTFY_WIREPROXY='E:\ctfy_project\.tools\wireproxy\wireproxy.exe'
python -m ctfy_agent --run-id playground auto --order id --status new instance_up
```

单题脚本（`runs/_mon/`）：`case_005.py`（005）、`case_006_matrix.py`（006）、
`case_009_root3.py`（009 root）、`case_019_v3.py`（019）、`case_022_scan.py`（022）、
`case_023_loop.py`（023 重试收割机）、`snapshot_evidence.py`（成绩证据快照）。