# 测试报告 · ctf2 / BUUCTF 题库 · MISC/PWN/RE/CRYPTO 难度梯度（2026-11-12，2 小时窗口）

> 结论速览：**8 题 4 解出（50%）**；Easy 层 4 题解出 3（MISC/PWN/RE），Medium 层 4 题解出 1（CRYPTO）。
> 两类失败原因很不一样：**（A）基础设施 bug 让"需要实例"的题直接不可解**（2 题），**（B）能力/时间缺口**（另 2 题）。
> 4 道解出题平均 62 秒/1 次提交即 accepted；4 道未解题吃掉 **92% token、93% 墙钟**，且各自都能定位到确定性根因。

---

## 0. 测试元信息（可复核）

| 项 | 值 |
|---|---|
| 时间窗 | 2026-11-12 20:01:24 ~ 21:04:00（墙钟 63 分钟，全部 8 题跑完，远早于 2 小时上限） |
| 平台 / 题库 | ctf2 `mcp_url=https://ctf2.dasctf.com/api/ai/v1/mcp`，练习场 **BUUCTF**（`b9bbb32f-f186-458f-b90b-12440c0f6aea`，3244 题） |
| 被测代码 | **冻结快照**：`git archive HEAD`（HEAD=`fa8f877`，2026-11-12 19:21）解到 `runs/_lab2h/repo`；`.env` 与 `tools/pylibs` 复制/联接 |
| 隔离理由 | 并行会话正在同一工作区改 `src/`（当前 HEAD 已到 `2bcb94c`，另有未提交改动），直接把测试挂在活工作区上会互相污染；快照内 `config/platforms.json` 单独固定成 `active=ctf2`，**共享 config/src 一行未改** |
| 执行路径 | 生产路径 `gui/platform_worker.solve`（连平台 → seed 参数桥 → 题面 → 实例体检 → 附件落沙盒 → Crew），串行 8 题，每题动态墙钟（本轮落到 15 分钟上限） |
| 监视 | 独立监视进程每 20s 采样：当前题/耗时、转录增量、各 role 活动、工具计数、被拦、重复调用、提交、实例事件、卡死告警 → `runs/_lab2h/monitor.{md,jsonl}` |
| 原始产物 | `runs/_lab2h/buuctf2h-2026-11-12.{results.json,events.jsonl,finished.json}`、快照 store `runs/_lab2h/repo/runs/buuctf2h-2026-11-12/`、汇总 `runs/_lab2h/summary.json`、深挖 `runs/_lab2h/digest.txt` |
| 额度消耗 | LLM token 合计 **6.12M**（计费口径 1.10M）；平台提交 4 次全部 accepted（0 次判错/0 次被拒）；实例 start 尝试 8 次 = **1 次成功**（test_your_nc）+ 5 次 `NOT_FOUND`（非环境题）+ 2 次 `agent_environment_limit`（被前题泄漏拖死） |

### 选题（4 类型 × Easy→Medium 梯度；先易后难，保证时间不足时四类型都有 Easy 样本）

| # | 题型 | 难度 | 题目 | 出处 |
|---|---|---|---|---|
| 1 | MISC | Easy | easycap | BUUCTF |
| 2 | CRYPTO | Easy | rot | BUUCTF |
| 3 | PWN | Easy | test_your_nc | BUUCTF |
| 4 | REVERSE | Easy | easyre | BUUCTF |
| 5 | MISC | Medium | b64 | BUUCTF |
| 6 | CRYPTO | Medium | newbie_rsa | BUUCTF |
| 7 | PWN | Medium | easyROPtocol | BUUCTF |
| 8 | REVERSE | Medium | clockin | BUUCTF |

---

## 1. 逐题结果

| # | 题型/难度 | 题目 | 结果 | 用时 | 轮次 | token(计费) | 工具调用 | 平台提交 |
|---|---|---|---|---|---|---|---|---|
| 1 | MISC/Easy | easycap | ✅ **解出** | 72s | 2 | 166k(34k) | 21 | 1 次 accepted |
| 2 | CRYPTO/Easy | rot | ❌ wall_clock | 924s | 3 | 316k(50k) | 29 | 0 |
| 3 | PWN/Easy | test_your_nc | ✅ **解出** | 45s | 2 | 38k(13k) | 7 | 1 次 accepted |
| 4 | REVERSE/Easy | easyre | ✅ **解出** | 61s | 2 | 165k(32k) | 20 | 1 次 accepted |
| 5 | MISC/Medium | b64 | ❌ wall_clock | 936s | 9 | 1550k(263k) | 103 | 0 |
| 6 | CRYPTO/Medium | newbie_rsa | ✅ **解出** | 70s | 3 | 131k(24k) | 18 | 1 次 accepted |
| 7 | PWN/Medium | easyROPtocol | ❌ coordinator_done | 742s | 7 | 1132k(206k) | 111 | 0 |
| 8 | REVERSE/Medium | clockin | ❌ wall_clock | 905s | 11 | 2621k(480k) | 170 | 0 |

解出的 4 个 flag（均为平台 accepted 后回读，留档）：

```
easycap      flag{385b87afc8671dee07550290d16a8071}
easyre       flag{this_Is_a_EaSyRe}
newbie_rsa   31e7957b168706db1acb124c3589e367          # 该题平台不要求 flag{} 包裹
test_your_nc CTF2{900b4b20-4d62-418d-a7f1-b2a68ecad20f}  # 平台实例内 CTF2{...} 形态
```

> 说明：4 道题都是**第 1 次提交即 accepted**（`attempts=1`、`rejected_answers=[]`），说明提交闸门/答案提取链路正常。

## 2. 按题型 / 难度汇总

| 维度 | 解出率 | 平均用时 | 平均 token | 备注 |
|---|---|---|---|---|
| MISC Easy | 1/1 | 72s | 166k | 附件 pcap，直接 tshark/手工解析 |
| MISC Medium | 0/1 | 936s | 1550k | 卡在"图片口令 + 无 OCR 环境"，见 §3.2 |
| PWN Easy | 1/1 | 45s | 38k | 全链路最省：起实例→nc→拿 flag→提交 |
| PWN Medium | 0/1 | 742s | 1132k | **实例配额被上一题泄漏占住，全程无靶标**，见 §3.3 |
| REVERSE Easy | 1/1 | 61s | 165k | 单文件逆向，hexpeek+python 解 |
| REVERSE Medium | 0/1 | 905s | 2621k | APK+dex 深挖，15 分钟不够；同受限实例配额，见 §3.4 |
| CRYPTO Easy | 0/1 | 924s | 316k | **难度标签失真**：实为"ROT 变体 + 4 字符后缀 MD5 爆破"，见 §3.1 |
| CRYPTO Medium | 1/1 | 70s | 131k | 真·简单 RSA，标签反而是 Medium |

**总账**：总时长 3755s；解出 4 题共 248s / 500k token（8% token）；未解 4 题共 3508s / 5618k token（**92% token、93% 墙钟**）。
平台难度标签与真实难度**明显不单调**（Easy 的 rot 比 Medium 的 newbie_rsa 难得多），选型时不能只信 difficulty。

---

## 3. 逐题根因（证据可复核）

### 3.1 rot（CRYPTO/Easy，wall_clock 924s，3 轮）

- 事实链：解压 `rot/rot.txt`（GBK 提示）→ 86 个十进制数（23..138）→ 判定为"数值整体 -13"的 ROT 变体；
  crypto 专家拿到骨架 `flag{www_shiyanbar_com_is_very_good_????}` 与目标 MD5 `38e4c352809e150186920aac37190cbc`；
- 随后把剩余时间全押在"4 字符后缀 × 多种 MD5 拼接形式"的爆破上：`q4.py` 多核跑完 `inner95 exhausted 81450625`、`FINAL None`（≈86s），
  之后再无新方案，15 分钟墙钟到点收口（20:17:59）。
- 根因：**平台 difficulty=Easy 与实际不符**（该题真实解法需要正确猜测后缀字符集/拼接方式，属爆破+猜）；且 15 分钟墙钟只够 1~2 轮假设。
- 证据：`transcripts/586f0b12-….jsonl` 20:13:44 shell `python3 q4.py` 输出 `phase1 None / inner95 exhausted 81450625 / FINAL None`；20:17:59 `[crypto 结论（1 步，wall_clock）]`。

### 3.2 b64（MISC/Medium，wall_clock 936s，9 轮）

- 事实链：外层多层 base64 → 内层 `inner.zip`，`flag.txt` 被 ZipCrypto 加密；`password.txt` 是"口令图案"的 base64 包装；
- misc 专家尝试：字体模板匹配（1–2px 笔画粘连）、2x 降采样、`bkcrack` 已知明文攻击（明文仅 38 字节/密文 50 字节，信息量不足 12 字节）、弱口令、以及最后"原生分辨率 ASCII art 人读"。
- 根因：**环境没有 OCR/图像库能力**（`from PIL import Image` 不可用、无 tesseract），而口令只以图像形式给出 → 该题在本机环境下"看不了图"；
  叠加 **15 分钟墙钟**，第 9 轮才走到"人读 ASCII art"这条兜底路线时预算已尽。
- 证据：20:33:25 协调者 PLAN「环境无 OCR 库、字体模板匹配在 1–2px 笔画上不可行」；20:33:40 最后一轮派活「把 work/art.txt 直接人读成口令」；期间**同一条 shell 命令重复 9 次**（见 §4.4）。

### 3.3 easyROPtocol（PWN/Medium，coordinator_done 742s，7 轮）——**基础设施 bug 直接废题**

- 时间线：20:36:32 开跑（附件 pwn 14.9KB + libc-2.31.so 2MB 落盘成功）→ **t=54.9s `instance_start_failed: agent_environment_limit`**
  （"Practice and competition environments share one quota. Destroy the environment you no longer need…"）
  → 自愈尝试"按 challenge_id 回收残留环境"返回 **`ok=False`**（该题本身没有环境，删不到东西）
  → 之后全程 `state.json` 里本题 `instance={}`、`instance_id=""`，pwn 专家只能静态分析 + 尝试本地跑（WSL/Docker 均不可用）
  → 20:48:44 判"三条路线（静扫/本地跑/连远端）全部确定性排除"，coordinator_done。
- **根因（两层）**：
  1. **上一题 `test_your_nc` 的实例没被释放**：它的 `state.json` 里 `instance_id=""`，而 crew 的释放逻辑是
     `if challenge.instance_id and not keep: platform.stop_instance(instance_id)`（`crew.py:1630`）→ 空 id 直接跳过 → **唯一实例配额被前题占死**；
  2. 为什么 id 是空：`solver._register_instance` 只认 `info["id"] / info["instance_id"]`，而 **BUUCTF 练习场的实例回执字段是 `environment_id`**
     （实测 `{'environment_id': 'dfec6dc3-…', 'access_url': …, 'expires_at': …}`）→ `instance_id` 永远为空。
  3. 叠加第三个坑：即使 id 有值，`stop_instance` 走的是"通用平台"形状 `{"instance_id": …}`，而 ctf2 练习场的删除工具要
     `challenge_id + practice_ground_id`（`instance_guard.delete_environment` 已经写对了，但 crew 的释放路径没走它）。
- 证据：events.jsonl 该题 `instance_start_failed … agent_environment_limit`（t=54.9s）；`state.json` 中 `test_your_nc.instance_id=""` 且 `instance.access_ready=true`；`easyROPtocol.instance={}`。
  另外该题在 20:47:51 的 done 闸门提示里出现**别的题的候选 flag**（见 §4.2）。

### 3.4 clockin（REVERSE/Medium，wall_clock 905s，11 轮）

- 事实链：附件 `clockin.rar`(3.7MB) → 解出 APK（`滴，帅比卡.apk`）→ 目标 `Base64Coded` + `RSAEncrypt`（业务真身在 `classes2.dex`，string_ids=3423）；
  rev 专家 350 条记录，已定位 `Base64Coded <init>@135580 / decode@135500 / encode@135536`、`RSAEncrypt` 在 CD 260，
  但"smali→算法→明文"没走完；21:03 墙钟到点。
- 根因：**时间不够**（15 分钟对 APK/dex 逆向远远不够，正常要 30–60 分钟）+ **实例配额同样被泄漏占住**（t=52.1s `agent_environment_limit`，RE 题本不需要，但每道题都会自动 start 一次）；
  另有 `rarfile` 缺库 1 次（靠其它方式解压绕过）。
- 证据：transcripts 该题 21:01:28 / 21:03:10 协调者 PLAN 与 REFLECTION；`digest.txt` 中"缺库: rarfile"；**同一条 shell 命令重复 14 次**（§4.4）。

---

## 4. 问题清单（按影响排序，均带复现证据）

### 4.1 P1-A：实例配额泄漏 —— 单配额平台上一题失败会让后续"需要实例"的题直接不可解

- 机理：`_register_instance` 不识别 `environment_id`（BUUCTF 练习场字段）→ `challenge.instance_id` 恒空 → crew 的释放分支被 `if challenge.instance_id` 挡住 → 上一题实例活到 TTL（60 分钟）→ 下一题 start 报 `agent_environment_limit`。
- 影响：本轮 **2 道 Medium（PWN/RE）因此完全失去靶标**（本来至少能验证交互能力）。
- 定位：`solver.py:_register_instance`（只取 `id`/`instance_id`）、`crew.py:1630`（释放条件）、`crew.py:1632`（用 `stop_instance(instance_id)` 而非按题删除）。
- 建议：① `_register_instance` 兼容 `environment_id`；② 释放时若平台删除工具 schema 需要 `challenge_id/practice_ground_id`，走 `instance_guard.delete_environment(platform, challenge_id, competition_id)`；
  ③ 开跑前清扫在无 `list_instances` 平台至少有"上一题 challenge_id 记录"可删（本轮 store 里有 `test_your_nc`，但 preflight 只针对当前题）。

### 4.2 P1-B：同 run 跨题信息泄漏（沙盒能读 run 级 state.json，别的题候选串会污染本题账本）

- 机理：沙盒在 `runs/<run>/sandbox/<cid>`，而 `state.json` / `transcripts/` 在上一级 `runs/<run>/`；shell 可 `cd ..\..` 直接读。
- 实证：easyROPtocol 的 pwn 专家为找实例信息执行
  `cd /d "...\runs\buuctf2h-2026-11-12" & findstr /n /i "host port services surface" state.json | head -40`（20:44:28），
  把 `rot` 的笔记连同候选 `flag{www_shiyanbar_com_is_very_good_????}` 带进自己的上下文；
  随后 20:47:51 / 20:48:44 的 done 闸门用**别的题**的候选拦下本题（"你已经拿到候选 flag：…"），协调者要额外花轮次解释。
- 影响：解题质量（错误引导）+ 结果可信度（跨题候选会被当成自己的候选）+ 若换成真实比赛是信息泄漏。
- 建议：沙盒边界要覆盖"同 run 的元数据目录"（state/transcripts/其它题沙盒），参考已实现的 `tools_local._cross_run_guard`，扩展到同 run 的 `state.json`/`transcripts/`；
  或把沙盒移出 run 目录（`runs/<run>/sandboxes/<cid>` 与元数据同级即可，本次任务里这是唯一真正必要的改动）。

### 4.3 P2-C：非环境题每道白一次 `start_instance`（本轮 5/8 题）

- 事实：easycap / rot / easyre / b64 / newbie_rsa 全部在 t≈1.0s 报 `NOT_FOUND`（该题没有环境），当前 HEAD 靠负缓存（`instance_absent`）**不再重试**（比上一版好），但每道仍发 1 次请求。
- 影响：若平台按题计 start 次数（每题上限 5），5 题 = 5 次配额；对需要实例的题本身无影响，但对"单配额 + 计数"平台是纯浪费。
- 建议：题面分类为 misc/crypto 且无 `services` 字段时跳过自动 start；或首题 1 次 NOT_FOUND 后写 run 级"本平台这些题无环境"跳过（当前只按题记）。
- 佐证：本轮 8 次 start 尝试里只有 1 次真成功（PWN Easy），另外 2 次是 `agent_environment_limit`（§4.1 造成）。

### 4.4 P2-D：重复动作没被有效拦住（本轮 3 题出现 ≥3 次同参调用）

- clockin：同一条 `shell`（同一 python -c 脚本）**14 次**；b64：同一条 shell 命令 **9 次**（另有 5 次、4 次各一组）；easyROPtocol：`done` 3 次。
- 影响：直接烧 token/时间（未解题 92% token 里有一部分是纯重复）。
- 建议：把 `_cross_run_guard` 同款"动作指纹计数"提升为**硬断路器**（同一 `(tool, args)` 第 2/3 次直接返回"已执行过，换方案"，并计入 no-progress 刹车），当前只在台账里统计。

### 4.5 P2-E：实例 TTL 60 分钟且无续期

- 实测 `expires_at = created_at + 3600s`（`remaining_seconds: 3593`），代码里没有任何 renew 调用（`tool_map` 里 `renew_instance` 已映射但无人用）。
- 影响：本轮 15 分钟/题无感；上一轮 25 分钟/题的 PWN 若遇重试/自愈，或未来 40–60 分钟长题，实例会在解题中途静默过期，表现为"tcp 突然连不上"。
- 建议：solver 在 `remaining_seconds < 300` 且有进行中的交互前调用 `renew_instance`（或重新 start 并换 access_url）。

### 4.6 P2-F：本机能力缺口（按本轮实际卡点排序）

| 缺口 | 卡住哪题 | 证据 |
|---|---|---|
| 无 OCR / Pillow | b64（口令图） | 协调者 PLAN 明说"环境无 OCR 库"；`from PIL import Image` 不可用 |
| 无 `rarfile`（靠外部解压绕过） | clockin | `缺库: rarfile` 1 次 |
| 无 Linux/WSL/Docker | easyROPtocol（本地跑 pwn） | pwn 专家结论"本机无 WSL/Docker、无本地监听靶标" |

> 注：镜像/本地依赖当前只有 pycryptodome / gmpy2 / sympy（+ 并行会话刚补的 z3 等），OCR、unrar 仍缺。

### 4.7 P3-G：15 分钟/题墙钟与难度不匹配

- 本轮 3 题 wall_clock（rot 924s、b64 936s、clockin 905s），全部在"刚走到第 2~3 个假设"时被砍。
- 建议（按本轮实测）：Easy 8–10 分钟、Medium 20–25 分钟、Hard 40 分钟，按 difficulty 动态分配；或先跑 Easy 快速筛"链路问题"，再给 Medium 连续档期。

### 4.8 P3-H：平台 difficulty 标签不可信

- rot 标 Easy 实为"ROT+4 字符 MD5 爆破"（失败）；newbie_rsa 标 Medium 却 70 秒一次过。
- 建议：选题排序用"历史成功率/附件类型"而非 difficulty 字段；报告里也按实测难度记录。

---

## 5. 与上一轮（2026-11-10 三题 0/3）对比

| 指标 | 上一轮（3 题，25min/题） | 本轮（8 题，15min/题） |
|---|---|---|
| 解出 | 0/3 | **4/8** |
| 首次提交即 accepted | 0 | 4/4（无判错） |
| 附件落盘 | 3/3 | 8/8（含 2MB libc、3.7MB rar） |
| 实例起真端点 | 1/3（PWN unipickle） | 1/3（PWN Easy 成功；PWN/RE Medium 被前题泄漏的配额拖死，从未拿到靶标） |
| 主要失败类型 | 能力/时间（0 解） | 基础设施（2 题）+ 能力/时间（2 题） |

结论：**修完 11-10 那批问题后，基础链路（附件/实例/提交/预算）已稳定，Easy 层解题率 3/4；Medium 层的瓶颈主要不在模型，而在实例配额释放与跨题沙盒泄漏这两个 bug。**

---

## 6. 下一步建议（按投入产出排序）

1. **修 §4.1 实例配额泄漏 + §4.2 沙盒越界**（半天内可完成，预计直接解锁 PWN/RE Medium 类题目；本轮 2 道失败题可直接复测）。
2. **给 §4.4 重复动作加硬断路器**（1 天内；对 token 成本影响最直接，本轮 92% token 花在未解题上）。
3. **§4.3 非环境题跳过自动 start**（小时级；减少平台配额消耗）。
4. **§4.5 实例续期**（1 天内；为长题铺路）。
5. 选型改用"实测难度"：Easy→Medium 分批跑，Medium 给 20–25 分钟；模型能力侧补 OCR（b64）、pwntools 运行环境（PWN）后再评估。

---

## 7. 复现与产物位置

```powershell
# 快照（被测代码 = fa8f877；共享工作区未被修改）
runs\_lab2h\repo\                         # git archive HEAD + tools\pylibs 联接；.env 已在收尾时删除（敏感，复跑时从仓库根复制一份进去）
# 驱动 / 监视 / 汇总脚本
runs\_lab2h\run_2h.py                     # 串行驱动（动态墙钟、事件流、results.json）
runs\_lab2h\monitor.py                    # 每 20s 采样 → monitor.md / monitor.jsonl
runs\_lab2h\summarize.py / dig.py         # 汇总指标 / 逐题根因深挖
# 结果
runs\_lab2h\buuctf2h-2026-11-12.results.json
runs\_lab2h\buuctf2h-2026-11-12.events.jsonl
runs\_lab2h\repo\runs\buuctf2h-2026-11-12\{state.json,transcripts\*.jsonl,sandbox\}
runs\_lab2h\monitor.md                    # 全程监视屏（含卡死/重复/被拦告警）
runs\_lab2h\summary.json / digest.txt     # 本报告 §1/§2/§3 的数据源
```

> 备注：本轮测试期间并行会话仍在改共享仓库（HEAD 已前进到 `2bcb94c` 并有未提交文件）；本报告全部数字来自**冻结快照 `fa8f877`**，与当前 HEAD 的差异需另行复核。共享 `config/agent.json` 当前被并行会话切到 CTFY 档案（`mcp_url` 指向 ctfy、`tool_map` 为空），复跑本测试请务必用快照或重新切回 ctf2。

*报告生成时间：2026-11-12 21:30 左右；所有结论均来自当次运行的 results/events/transcripts 原始文件，未做推断性夸大。*