# O-D / O-E 策略补丁 + 撞墙判据：实现与行为验证（2026-10-31）

**结论先行：提示词单独不管用；把判据做进工具输出（`advice`）才真正收敛。**
两题仍未解出，所以这轮买到的是「少烧无用请求」，不是分数——但对正式赛的时间/预算效率直接有用。

## 1. 改了什么

| 编号 | 位置 | 内容 | 类型 |
|---|---|---|---|
| O-D① | `config/agent.json` → `roles.web.system` | 撞墙判据：同一端点 401/403 累计 3 次必须换机制（换凭据类型 / 换身份 / 换端点族 / 换传参位置）；**一次 burst 打 24 个值全 401 也算撞墙** | 提示词 |
| O-D② | 同上 | 空手判据：同一发现手段连续 2 批 0 命中必须换手段（fuzz 换档 → JS bundle 抽 API 面 → robots/sitemap/href/注释/备份） | 提示词 |
| O-D③ | `src/ctfy_agent/tools_local.py` `_wall_advice()` | `http_burst` 输出新增 `advice` 字段：整批 ≥80% 401/403 → 「认证层的墙，换值/加字典无效」；≥90% 404 → 「发现手段空手，换手段」 | 工具输出（确定性） |
| O-D④ | `src/ctfy_agent/tools_webscan.py` | `fuzz` 命中全为 401/403 时追加认证层 note（与 ③ 对齐） | 工具输出（确定性） |
| O-E | `src/ctfy_agent/crew.py` `COORDINATOR_PROMPT` | 专家结论里出现疑似成功值 → **下一动作必须是 submit**；submit 判错记 note 标「已提交-错误」，同值最多交 1 次 | 提示词 |

依据：`docs/archive/audit-budget-exhausted-2026-10-31.md` 的 M1/M2 模式（单端点 94 次 401、162 次连续 404、
35/37 失败题从未交过任何候选 flag）。

## 2. 验证设计

三个臂，同一模型（paratera `DeepSeek-V4.1-Flash-1017`）、同一预算（500k）、同一时限：

| 臂 | 代码状态 | 跑法 |
|---|---|---|
| 基线 | 补丁前（历史数据，500k 大测） | `runs/big-CTFARENA-*/traffic/*.sqlite` |
| v1 | 只有提示词（O-D①②、O-E） | `--run-id abwall` |
| v2 | 提示词 + 工具级 advice（③④） | `--run-id abwall2` |

指标从每次 run 的流量库（`runs/<run-id>/traffic/<题>.sqlite`，表 `exchanges`）直接统计，
不看模型自述。

**验证题**：`CTFARENA-021`（历史 94×401，其中一次 70×`POST /api/auth/otp` 在 76 秒内盲猜 OTP）、
`CTFARENA-034`（历史 162 次连续 404）。

## 3. 结果

### CTFARENA-021（401 墙）

| 臂 | 请求数 | 401 | 单端点最大 401 | 404 | 404 最长连击 | 200 | 结果 |
|---|---|---|---|---|---|---|---|
| 基线 | 239 | 94 | 59 | 126 | 48 | 18 | 未解 |
| v1 提示词 | 264 | 70 | **70** | 184 | 63 | 10 | 未解 |
| **v2 +advice** | **105** | **17** | **14** | 78 | 23 | 10 | 未解（522k 撞顶） |

→ 请求 −56%、401 −82%、单端点盲撞 −76%。**提示词单独反而更糟**（单端点最大 401 从 59 升到 70）。

### CTFARENA-034（404 墙）

| 臂 | 请求数 | 404 | 404 最长连击 | 200 | 唯一路径数 | 结果 |
|---|---|---|---|---|---|---|
| 基线 | 185 | 162 | 86 | 11 | 147 | 未解 |
| v1 提示词 | 384 | 313 | **98** | 57 | 226 | 未解 |
| **v2 +advice** | **32** | **24** | 24 | 7 | 27 | 未解（508k 撞顶） |

→ 请求 −83%、404 −85%、连击 98→24。代价是探索面变小（唯一路径 226→27），
说明模型从「广撒网」切到了「少而准」，但这题的真解法还没找到。

### 交付证据

`advice` 文案确实进了模型上下文：`runs/abwall2-*/CTFARENA-021.jsonl` 里 "撞墙判据" 出现 5 次。

## 4. 判读

1. **提示词对「一次调用打 24 个值」无能为力**：v1 明确写了「同端点 3 次即换机制」，
   模型仍然 70 次打同一个 OTP 端点——因为它认为「每个值是不同尝试」。判据必须落到工具输出里。
2. **v2 把浪费砍掉 8 成以上**，但两题仍 500k 撞顶未解：说明这两题缺的不是「少撞墙」，
   而是真正的凭据路径（021）与发现来源（034）。
3. 因此 O-D 的价值定位应改为：**释放被墙吃掉的请求量**（把预算让给有效路径），
   而不是「直接转化失败题」。要转化还需要 O-C（证据复用）/ O-E → 以及针对具体题的路线。

## 5. 复现

```powershell
# v1（只有提示词）
python runs/_paced_batch.py --competition 4394a10aa5d24f97a1ec3301ca718a45 --run-id abwall \
  --ids CTFARENA-021,CTFARENA-034 --models DeepSeek-V4.1-Flash-1017 --cap 500000 \
  --timeout 1200 --start-wait 600 --cooldown 5 --out runs/ab-wall/CTFARENA.jsonl

# v2（提示词 + 工具级 advice）
python runs/_paced_batch.py --competition 4394a10aa5d24f97a1ec3301ca718a45 --run-id abwall2 \
  --ids CTFARENA-021,CTFARENA-034 --models DeepSeek-V4.1-Flash-1017 --cap 500000 \
  --timeout 1200 --start-wait 600 --cooldown 5 --out runs/ab-wall2/CTFARENA.jsonl

# 指标对比（流量库直读）
python runs/_ab_wall.py CTFARENA-021 CTFARENA-034
```

## 6. 回归

- `tests/smoke_burst_group.py` 新增 `/authwall` 路由 + 2 条 advice 断言；
- 顺带修掉该测试的既有 flaky：`_BURST_VOLATILE` 漏了「浮点 epoch 秒」（小数位 <6 时残留 `.5`/`.25`
  尾巴，把同一类结果拆成多组）。修前原始代码 6 次跑挂 2 次，修后 10 连跑 0 失败；
- 全量冒烟 **28/28 通过**。
