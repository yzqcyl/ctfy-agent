# 门② S1/S2 上下文优化 A/B：解出率持平、原始 token 未降（+4%）、折后成本 −44%（2026-11-01）

**一句话：HEAD 基线 vs 当前树（S1a 稳定前缀 + S1b 轮间新会话 + S1c 烧毁护栏 + S2 分角色
keep_tail/事实板家族配额）在 4 道 CTFARENA 未解题上：解出率持平（1/4 = 1/4）、原始 token
+4.1%（未达 −20% 目标）、缓存命中 61.3%→87.4%、**按缓存折价的计费等价 −44.3%**。
S1b 单变量消融证否「轮间新会话导致回退」：关掉它反而更贵（014：260k → 346k）。**

## 1. 实验设计

| 项 | A 臂（基线） | B 臂（候选） |
|---|---|---|
| 代码 | `git show HEAD` 的 solver/crew/ctxcompress，隔离树 `runs/_gate2_a/`（独立 PROJECT_ROOT/config/.env/runs） | 当前工作树（S1+S2 全开；`history_soft_mode=truncate`、`budget_cache_discount=off` 保持默认关） |
| 配置 | `runs/_gate2_a/config/agent.json`（快照） | `runs/_gate2_b_config/agent.json`（与 A 逐字节相同） |
| 模型/思考 | paratera `deepseek-v4.1-flash-1017`，`LLM_ENABLE_THINKING=1` | ← 同 |
| 预算 | 500,000 tok/题 | ← 同 |
| 题集 | CTFARENA-008 / 010 / 012 / 014（均为此前 paced 批未解） | ← 同 |
| 执行 | `runs/_gate2_chain.py` 串行（团队实例配额=1；等待他人实例自然释放，绝不误杀） | ← 同 |

起跑前修掉的平台坑（影响复现）：`start_instance` 传比赛**名**会 403 `not_registered`，
必须传比赛 **UUID**（脚本内置 name→UUID 映射）；期间并行会话占用配额，链按对方实例 TTL 动态等待。

## 2. 结果

| 题 | A calls / raw / cache% / 折后 | B calls / raw / cache% / 折后 | 解出 |
|---|---|---|---|
| 008 | 28 / 500,676 / 56.0% / 259,332 | 32 / 486,243 / 86.2% / 130,160 | A✗ B✗（A=budget_exhausted，B=coordinator_done） |
| 010 | 29 / 503,923 / 60.0% / 244,723 | 28 / 491,343 / 87.4% / 127,196 | A✗ B✗（同上） |
| 012 | 30 / 506,930 / 62.6% / 238,630 | 29 / 487,213 / 86.3% / 131,361 | A✗ B✗（同上） |
| 014 | 11 / 145,259 / 69.8% / 57,823 | 16 / 260,305 / 89.8% / 56,977 | **A✓ B✓**（均 flag_accepted） |
| **合计** | **98 / 1,656,788 / 61.3% / 800,508** | **105 / 1,725,104 / 87.4% / 445,694** | **1/4 = 1/4** |

- 原始 token：**+68,316（+4.1%）**；扣除 014 单样本方差后三题 **−3.1%**。
- 缓存命中率：**+26.1pp**（61.3%→87.4%）。
- 调用数：+7 次（98→105，+7.1%）；单次 prompt 均值基本持平（~15.5k）。
- 折后计费等价（公式 `(prompt−cached) + ceil(cached/10) + completion`，DeepSeek 缓存命中价=1/10）：
  **−354,814（−44.3%）**。

## 3. 机制诊断

1. **S1a 稳定前缀是唯一被证实的大收益**：缓存命中率 56~70% → 86~90%，
   这就是折后成本 −44% 的全部来源；与 `runs/_cache_probe.py` 的探针结论（共享前缀 96.9% 命中）一致。
2. **S2 分角色压缩中性**：每调 prompt 两臂均值持平；008 上 B 的每调 prompt 17.1k→14.3k，
   但被调用数增加抵消（32 vs 28）。
3. **014 的 +115k 不是上下文机制，是探索深度方差**。三组同题对照（全部解出）：

   | 014 配置 | calls | raw | cache% | 每调 prompt | 折后等价 |
   |---|---|---|---|---|---|
   | A：HEAD（无 S1/S2） | 11 | 145,259 | 69.8% | 12,662 | 57,823 |
   | B：当前树（fresh_sessions=on） | 16 | 260,305 | 89.8% | 15,723 | **56,977** |
   | 消融：当前树 + fresh_sessions=off | 21 | 345,769 | 87.9% | 15,947 | 80,925 |

   三样本 token ≈ 调用数 × 单次 prompt，差异由调用数（11/16/21）驱动——即模型该次探索走多深
   （B 多跑了 fuzz/manifest/路径枚举/verify_answer）。**S1b 消融证否**：关掉轮间新会话反而更贵
   （+85k）。折后成本 A≈B（57.8k vs 57.0k）——S1a 的缓存红利恰好抵消多出来的调用。
4. **三题未解与上下文无关**：两臂都在 ~490–505k 预算上限处死（A=budget_exhausted、
   B=coordinator_done），上下文工程没有改变战斗力；这三题要换解法/工具面。

## 4. 判定

| 门②标准 | 结果 |
|---|---|
| 解出率不降 | ✅ 持平（1/4 = 1/4） |
| tok/题 −20%（raw 口径） | ❌ +4.1% |
| 实际付费 token（折后）改善 | ✅ −44.3% |
| 机制符合预期（缓存/压缩生效） | ✅ 缓存 +26pp、单调 prompt 持平/局部 −16% |

## 5. 花费披露

- 门②两臂：raw **3,381,892**（A 1,656,788 + B 1,725,104）；折后等价 **1,246,202**。
- S1b 消融（014 单题）：raw **345,769**；折后等价 **80,925**。
- 合计 raw **3,727,661**；折后等价 **1,327,127**。

## 6. 决策与后续

1. **保留当前默认**：S1a 稳定前缀、S1b 轮间新会话（实测更省调用）、S1c 烧毁护栏、S2 分角色
   keep_tail/事实板家族配额；`history_soft_mode` 默认 `truncate`（elide 未启）、
   `budget_cache_discount` 默认 off（`cached_tokens`/`raw_prompt_tokens` 始终记录，便于事后核算）。
2. **不再追加 raw-token 方向的上下文实验**：证据表明原始 token 由「调用次数 × 单次 prompt」决定，
   而调用次数是探索行为；如果目标口径是 raw token，杠杆在**减少调用**（coordinator 轮数上限/
   专家提前交付/收敛策略），需要另做 A/B（约 3.4M raw 预算级）。
3. 未解题（008/010/012）转入「解法/工具面」工作流，不再用上下文旋钮试。

## 7. 复现

```powershell
# A 臂（HEAD 基线隔离树）→ 再 B 臂（当前树）；两者串行
python runs/_gate2_chain.py
# S1b 单变量消融（014，fresh_sessions=off）
python runs/_paced_batch_b.py --competition CTFARENA --run-id gate2abl --ids CTFARENA-014 \
  --cap 500000 --out runs/gate2-ablate/rows-noS1b.jsonl --set-extra fresh_sessions=false
```

数据：`runs/gate2-a/rows.jsonl`、`runs/gate2-b/rows.jsonl`、`runs/gate2/chain.log`、
`runs/gate2-ablate/rows-noS1b.jsonl`；逐题口径在各自 run 目录 `state.json` 的
`challenges.<id>.tokens`（含 `calls/cached_tokens/raw_prompt_tokens`）。

**caveat**：每题每臂 n=1；单次运行方差大（014 调用数 11/16/21），结论只对「机制方向」可靠，
不适合当作精确百分比；A 臂 HEAD 已包含 P0–P2 工作，故 Δ 精确对应 S1a/S1b/S1c/S2。
