# 问题统计与测试巡检（2026-11-13 · 体系修复轮）

> 目的：把「体系问题」从感觉变成可复现数字。分三部分：①测试面巡检；②批次问题统计（修复前后对比）；
> ③待修/观察清单。数据产物在 `runs/_big/`（gitignored）：`test_sweep.log`、`stats-next24.json`、
> `loop_audit.json`、`results-*.json`。

## 1. 测试面：32 个独立 smoke 套件 + 主套

命令：`python runs/_big/run_test_sweep.py`（继承父进程环境；**不要**只设 PYTHONPATH，否则 socket 类测试
全部 WinError 10106 误判失败——本轮踩过）。

| 结果 | 内容 |
|---|---|
| 主套 `tests/smoke.py` | **180 passed / 0 failed**（P6/P7/P8/P9/P12/P13 改动后各跑一遍） |
| 独立套件第一轮（32 个） | **31 个通过**；唯一失败 `smoke_http_cache.py` 是并发跑批时本地端口 WinError 10053 抖动，单独复跑 **通过** |
| 独立套件第二轮（剩余 34 个） | **34/34 全部通过**（`smoke_gui_theme`、`smoke_libs`、`smoke_ai_*`、`smoke_webmap` 等） |
| 合计 | **66 个独立套件全绿 + 主套 180 例**，无产品缺陷级失败 |
| 覆盖命中 | `smoke_attachments`（平台参数归一 P12）、`smoke_platform_attempt`、`smoke_solved_scope`、`smoke_gate`、`smoke_action_loop`（P6/P13 语义）、`smoke_instance_*` 6 个、`smoke_netbridge`、`smoke_crypto/rev/pwn`（70 例）全部通过 |

结论：功能回归面全绿；唯一的「失败」是测试环境并发抖动，复测即过，不是产品缺陷。

## 2. 批次问题统计

### 2.1 修复前（历史 257 份转录 / 241 道有结果，`loop_audit.py` 口径）

| 问题 | 数值 | 占比 |
|---|---|---|
| 近失（拿到 flag 形态候选却没收下） | 72 | 30% |
| 提前收口（`coordinator_done` 且 <70% 预算） | 58 | 24% |
| `done` 被候选/预算闸门拦 | 398 次 | — |
| 重复动作断路器摘工具 | 50 次 | — |
| 同值重复提交 | 103 次 | — |
| 提交被去重/闸门拦 | 99 次 | — |
| 平台判错提交 | 54 次 | — |
| 裸值提交（无 `flag{}` 包裹） | 23 次 | — |

> 口径说明：`loop_audit.py` 现已按「批次→结果文件」映射判定，同题跨批次不再被旧结果覆盖
> （修正后 `big2-static` 真实解出数从 0 修正为 9、总计解出 39）。

### 2.2 修复后（next24：24 道平台真实未解近失/早停题，10 分钟/题、3 并发，已全部跑完）

| 指标 | 数值 | 对比 |
|---|---|---|
| 完成 | **24/24** | 无中断 |
| 解出 | **1**（`简单的逆向` 133s，平台 `flag_accepted`） | 该题基线 0 |
| 提前收口 | **3/24（12.5%）** | 历史基线 36% |
| `wall_clock`（用满预算） | **16/24（67%）** | 说明预算被真正用起来了 |
| `coordinator_done` / `action_loop` | 6 / 1 | — |
| 附件获取成功 | **20 道有附件全成功，0 失败** | fixval2 时 0/10（平台参数被拒） |
| 工具报错（404/INVALID_REQUEST 等） | **0** | 修复前本批全部 404 |
| 提交 | 12 次（0 接受 / 4 平台判错 / 8 去重拦） | — |
| 断路器 | 1 次（login 同值 9 连交 → 370s action_loop） | 旧版同题 34~39 连交 |
| refuted 闸门 / 裸值补包裹 | 0 / 0 | 本轮没触发到对应场景 |

### 2.4 真正的瓶颈已经换位：不是「提交机制」，是「根本没有候选」

对 24 道完成题做漏斗统计（`stats_zerosubmit.py`）：

| 漏斗层 | 数量 |
|---|---|
| 完成 | 24 |
| **一次都没提交** | **20（83%）** |
| 提交过（≥1 次） | 4 |
| 候选被平台接受 | 1 |

- 20 道 0 提交的题里，19 道用满/接近用满 10 分钟预算（`wall_clock`），说明时间花在**读题、逆向、
  还原算法/协议**上，不是卡在工具或闸门；
- 近失候选里大量是模板串（`DASCTF{...}`、`flag{...}`、`DASCTF{32位md5}`）——即「知道格式但没算出来」，
  属于解题能力缺口，不是提交链路问题；
- 这与历史近失 71 道的构成一致：**候选生成不足**是主因，提交/闸门问题（P6-P9）只是次要放大器。

### 2.3 修复前后同题对照（fixval3，10 道近失题）

| 题 | 旧代码 | 修复后 | 变化 |
|---|---|---|---|
| bang | coordinator_done 257s | action_loop 758s | 预算利用率 +195% |
| vigenere | coordinator_done 215s | action_loop 78s（当时无 P13） | P13 已修 |
| powerPacked | coordinator_done 318s | coordinator_done 864s | +171% |
| LittleJunk | coordinator_done 227s | wall_clock 1019s | 用满 |
| 书鱼的秘密 | coordinator_done 129s | coordinator_done 273s | 仍偏早 |

## 3. 本轮修掉的问题（按优先级）

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| P11 | 全局平台档案被切到 CTFY，批次跟着跑错平台 | 练习场题全 404，整批白跑 | ✅ `run_big` 支持 `BIG_PROFILE` 强制档案 |
| P12 | DASCTF 平台 strict 参数校验（`additionalProperties:false`）拒绝桥接参数 | 题面/附件全丢，专家空转 | ✅ `platform._clean_args` 按 schema 归一 |
| P6 | 判错候选仍逼提交 + submit 整工具被摘 → 死锁 | vigenere 100s 收口 | ✅ refuted 闸门 + 按值处理 |
| P13 | submit 同值重复 4 次就收口 | 77s 早停 | ✅ 分级提示 + 8 次上限 |
| P7 | 裸 token 不补 `flag{}` | rsa15 白交 3 次 | ✅ 一次性规范化重试 |
| P8 | 预算闸门上限太低 | 65/207 道 <70% 预算收口 | ✅ 2×上限 + 零候选延长 |
| P9 | 占位诱饵当候选 | 白烧每题 5 次提交额度 | ✅ 占位过滤 |
| — | 陈旧快照跑批 / 跑已解题 | 结论作废、`ALREADY_SOLVED` | ✅ 每轮重建快照 + `solved_ids` 跳过 |
| — | 审计脚本合并 results 时 `setdefault` 旧批次覆盖新批次 | 批次对比数字错 | ✅ 已改为「按批次映射」；`big2-static` 的真实解出数从 0 修正为 9 |
| — | `run_big` 队列 `done=ok`（只有解出才算 done） | 心跳长期显示 `done=1/pending=23`，与实际 24 条结果不符，续跑判断失真 | ✅ 改为「处理完即 done」，解出另由 `flag` 字段表达 |

## 4. 待修 / 观察清单

1. **同值提交提示不足**：`login` 在 2 次提示（第 2 次软提示、第 4 次强警告）后仍连交到第 8 次才收口，
   白烧 ~200s。可考虑第 4 次后把 `submit` 从工具面摘掉，由 P6 refuted 闸门兜底（需再验证不死锁）。
2. **`coordinator_done` 仍偏早**（如 `书鱼的秘密` 273s/900s、`[HarekazeCTF2019]A_Z` 273s/600s）：
   候选判错后是否应更快「回到硬目标 delegate」。
3. **候选生成是主瓶颈**：24 道里 20 道 0 提交。短期最划算的是把「近失题的真实候选」复盘成题型卡
   （REVERSE 反编译主逻辑、MISC 流量/隐写、CRYPTO 交互式 3 类），而不是继续加洞闸门。
4. **能力缺口未动**：本轮 16 道 wall_clock 多是 REVERSE/MISC 深挖题；镜像能力包（v8）仍未构建，
   CI 构建验证仍是长尾项。
5. 测试面基建：`run_test_sweep.py` 必须继承父进程环境（否则 socket 用例 WinError 10106 假失败）；
   `smoke_http_cache.py` 在并发跑批时偶发本地端口 10053，属环境抖动，复测通过即视为通过。