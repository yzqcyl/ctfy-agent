# 智能体赛道测评集题型比例（live 实拉，2026-10-30）

> 口径修正：**比赛没有选择题**（用户确认，2026-10-30）。此前按可见 catalog 算出的
> 「QA/MCQ 占 51.8%」是**题池噪声**，不是赛题比例，本文一律剔除。
> 数据源：`https://ctfy.tsinghua.edu.cn/api/v1/challenges`（`CTFY_TOKEN`）实拉，
> 快照 `runs/site/catalog-challenges-live-2026-10-30.json`（794 题）。

## 0. 赛道抽题方式

| 项 | 实测 |
|---|---|
| 智能体赛道 | `challenge_scope=catalog`、`challenges_withheld=true`、`dedicated_pool=false`、题量开赛前不公开 |
| 账号可见 catalog | 794 题（10-28 快照 793 → 今日 794，仅 +SAMPLE-015） |
| 其中选择题（SECQA/CTI-MCQ/MMLU-CS 等） | 411 题 —— **不在比赛范围，全部剔除** |
| **实操题池** | **383 题** |
| 平台全量 | 2862 题 / 21 suites；我们可见 13 suites（8 个不可见，疑为赛题集） |

## 1. 实操题按 category（383 题）

| 类型 | 数量 | 占比 |
|---|---|---|
| **web** | **375** | **97.9%** |
| reverse | 3 | 0.8% |
| pwn | 2 | 0.5% |
| misc | 2 | 0.5% |
| crypto | 1 | 0.3% |

## 2. 实操题按难度

| 难度 | 数量 | 占比 |
|---|---|---|
| easy | 110 | 28.7% |
| medium | 188 | 49.1% |
| **hard** | **85** | **22.2%** |

hard 全部是 web：CTFARENA 60、UPLOADLABS 8、XBOW 8、SQLILABS 7、CYBENCH 2。

## 3. 实操题按题库来源

| 来源 | easy | medium | hard | 小计 | 占比 |
|---|---|---|---|---|---|
| CTFARENA | 7 | 48 | 60 | 115 | 30.0% |
| XBOW | 45 | 51 | 8 | 104 | 27.2% |
| SQLILABS | 19 | 43 | 7 | 69 | 18.0% |
| CYBENCH | 11 | 27 | 2 | 40 | 10.4% |
| UPLOADLABS | 2 | 11 | 8 | 21 | 5.5% |
| SAMPLE | 11 | 3 | 0 | 14 | 3.7% |
| AGENT-DEFENSE / AGENTBENCH / C2O-ALIST / CAPTCHABENCH / AUTOPENBENCH-AC / AWD / MISCONFIGLABS / FORGELABS | – | – | – | 各 2 | 各 0.5% |
| CVE-2017 / CVE-2018 / GAUNTLET-LITE / GENERATED-GAUNTLET-WEEKLY | – | – | – | 各 1 | 各 0.3% |

## 4. AI 安全实操题（tags 信号，25 道 = 实操池 6.5%）

全部 `category=web`，难度 easy 8 / medium 9 / hard 8。

AGENT-DEFENSE 001-002、AGENTBENCH 001-002、C2O-ALIST 001-002、CAPTCHABENCH 001-002、
CTFARENA-037/038/039/040/041/042/043/044/045/046/047/099/101/105/108、SQLILABS-018、XBOW-084。

## 5. 结论

1. **实操池 97.9% 是 web**：备赛投入应几乎全押 web（含 AI 安全，占 web 的 6.7%）。
2. **hard 占实操池 22.2%**（85 道，全 web）——比含选择题口径的 10.7% 高得多，
   高难题是分差主要来源，CTFARENA(60 hard) 与 UPLOADLABS(8/21=38% hard) 最典型。
3. pwn 2 / reverse 3 / crypto 1 / misc 2 合计 8 道（2.1%）：这些方向对初选几乎无收益，
   工具箱保留即可，不再追加投入。
4. 此前「补 QA/选择题通道」的判断**作废**（已回退相关改动，工作树无残留）。

## 6. 风险

- 正式赛题 withheld（题号与题量均不公开），8 个 suite 不可见 → 以上是**抽题池**比例，
  正式试卷比例可能有偏移；但「在线交互类 only + 以 web 为主」的结构不会变。
