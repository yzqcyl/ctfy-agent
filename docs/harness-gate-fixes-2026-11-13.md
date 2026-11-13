# 收口体系修复（2026-11-13）

> 起因：120 题批次 36/122 解出后，复盘发现**失败不只是能力不够，而是收口体系把「还能救的题」提前判死**。
> 本文记录审计口径、证据、四处修复、以及验证与回滚方式。
> 提交：`3a3fdd4 fix(harness): 收口体系四处修复（判错候选/裸token/占位诱饵/预算闸门）`

## 1. 审计口径与证据

工具：`runs/_big/audit_loop.py`（读全部批次 transcript，量化体系行为，不看题目难度）。
样本：212 份 transcript / 207 道有结果 / 30 解出（含 cap、solve2 批次）。

| 指标 | 数值 | 说明 |
|---|---|---|
| 近失（拿到 flag 形态候选但没有 accepted） | **62** | 有候选却没收下 |
| 提前收口（`coordinator_done` 且用时 < 70% 预算） | **65** | 最夸张 129s/900s |
| `done` 被候选闸门拦 | 304 次 | 拦了但没给出新方向 |
| 重复动作断路器摘工具 | 34 次 | 主要是 submit 被整个摘掉 |
| 同值重复提交 | 79 次 | 白烧平台额度 |
| 裸值提交（无 `flag{}` 包裹） | 20 次 | rsa15 把 md5 直交 3 次 |

三个代表性死锁/浪费现场：

1. **vigenere**：候选 `flag{mrctf_vigenere_crypto_crack_man}` 被平台判错 → 同值再交被去重拦 2 次 →
   断路器把 **submit 整个工具摘掉** → `done` 又被候选闸门拦（账本里还留着那个候选）→
   100s 收口，13 分钟预算没用。
2. **rsa15**：裸 md5 `2f1d580c…` 直交 3 次全判错，从未尝试 `flag{2f1d580c…}`。
3. **占位诱饵**：`flag{aaaa…}`、`DASCTF{fake…}`、`FLAG{you_may_need_this}` 被账本当真候选，
   反复推去提交，而每题平台提交上限只有 **5 次**。

## 2. 四处修复（`src/ctfy_agent/crew.py`）

| # | 修复 | 行为变化 |
|---|---|---|
| P6 | **判错候选进入 `refuted_values`**；`done` 闸门（`_done_gate` 新增 `refuted` 参数）不再拿判错值逼提交，改走 `_REFUTED_DONE_GATE` 逼「换证据」；submit 从「整个工具摘除」改为「按工具+参数摘除」（`suppressed_sigs`），换候选值仍可提交 | 消除「判错→submit 被摘→done 被候选闸门拦」死锁 |
| P7 | **裸 token 一次性规范化**：md5/uuid/base64 形态的值首次判错后自动补 `flag{…}` 重试一次（`_needs_flag_wrapper`，只认机器 token，不认普通单词） | rsa15 这类不再白丢额度 |
| P8 | **预算闸门上限 2×`MAX_DONE_BLOCKS`**；零候选但预算充足时继续拦；`done` 被拦不交给断路器计数 | 压低早停，且不再把收口语义掩盖成 `action_loop` |
| P9 | **占位/诱饵候选过滤**（`_is_placeholder_candidate`）：全同字符、含 fake/placeholder/dummy/you_may_need/xxx 等特征的值不进入候选闸门 | 不再把诱饵当真候选逼提交 |

回归：`tests/smoke.py` **180 passed / 0 failed**；`smoke_gate.py` 新增 8 条断言（判错闸门、
预算上限、裸 token 判定、占位过滤）；`smoke_action_loop.py` 改为锁「按值摘除、工具仍可见」语义。

## 3. 验证（进行中）

验证集：10 道**平台当前确实未解**、且审计标记为近失/早停的题（`runs/_big/manifest_fixval.json`），
15 分钟/题、并发 3、新快照 `runs/_fix3/repo`（含本修复）。

对照基线（同一批题在旧代码下的表现）：**0/10 解出**，其中 ≥8 道提前收口或断路器摘工具。

判定标准（任一即算有效，否则按规则回滚 `3a3fdd4`）：

- 近失题里出现 accepted（解出）≥ 1；
- 或 `coordinator_done < 70% 预算` 的比例显著下降（基线 8/10）；
- 或修复生效计数（`wrap_retry_total` / `refuted_gate_total` / `value_suppress_total`）> 0，
  说明死锁路径已被打掉（即使题仍没解出）。

复跑审计：`python runs/_big/audit_loop.py`（已扩展 fix3 快照与 `results-fixval2.json`）。

## 4. 顺带修掉的批次基础设施问题

- **陈旧快照**：验证批次一开始跑在 `runs/_fix2/repo`（crew.py 还是 11-12 版），结果无效。以后
  每轮验证必须重建快照（本轮 `runs/_fix3/repo`，已校验含修复标记）。
- **跑已解题**：并行 run 会解掉清单里的题，旧清单继续跑 → 平台回
  `ALREADY_SOLVED`（rsa15 实证），白烧时间还污染「早停」统计。
  新增 `runs/_big/fetch_unsolved.py`（平台权威未解清单）+ `run_big.py` 启动时跳过
  `solved_ids.json`（`BIG_SKIP_SOLVED=0` 可关闭）。
- 平台当前口径（17:12）：3244 题中**已解 45 题**，其中 120 题清单内 37 题已解（与本地记录 36 基本一致，
  rsa15 是并行 run 期间新解的）。

## 5. 回滚

```powershell
git revert --no-edit 3a3fdd4     # 或 git checkout 3a3fdd4^ -- src/ctfy_agent/crew.py tests/smoke_gate.py tests/smoke_action_loop.py
```

回滚后跑 `python tests/smoke.py`（应仍 180/0）并重建快照再跑批次即可。