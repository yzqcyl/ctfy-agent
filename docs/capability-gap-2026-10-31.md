# Agent 各方向能力盘点：现在有什么、差什么（2026-10-31）

数据来源全部可复现：`runs/_cap_inventory.py`（角色×工具矩阵）、`runs/_cap_stats.py`（按方向战绩）、
`runs/_cap_web.py`（web 工具采用率）、`runs/_cap_coverage.py`（题库覆盖度）、
`runs/_cap_envdiff.py`（开发机 vs 镜像差异）、`runs/_cap_ai.py`（AI 卡与 chat 工具）。

---

## 0. 一句话结论

**只有 web 方向被真正测过（128 题 / 61%），AI 方向 0/2，rev/pwn/crypto/misc 有效样本 ≈ 0；
平台题库 233 题里 101 题（43%）从没跑过。所以「各方向能力」目前不是强弱问题，是「大部分方向没有数据」。**

另外发现一个测量假阴性：`GAUNTLET-LITE-001` 实际已拿到 `FLAG{cc551e…}`，但该题库禁止提交
（`silent_competition` 403），于是被记成未解。

---

## 1. 现状总表

| 方向 | 代码能力（工具/卡） | 有效实战样本 | 战绩 | 判定 |
|---|---|---|---|---|
| web | 11 个本地工具 + 平台组 + **25 张方法卡** | **128 题** | **61%**（CTFARENA 66% / CYBENCH 38% / UPLOADLABS 95%） | 唯一有数据；中上，波峰在「常规 Web」 |
| ai（prompt injection / agentic） | `chat`（SSE/json/session/history）+ **20 张 ASI 卡** + `http_map` | **2 题** | **0/2** | 知识够、执行卡住 |
| rev | `elfpeek`/`hexpeek`/`tcp`/`shell`，**0 张方法卡** | 3 题（SAMPLE，全解） | 100%（n=3，无意义） | 未验证 |
| pwn | `elfpeek`/`pwngen`/`pwnfind`/`tcp`/`tcp_batch`，**0 张卡** | **0 题**（SAMPLE-003 是 VPN 不可达） | — | 未验证 |
| crypto | `rsatool`/`aesblock`/`prng`/`tcp`，**0 张卡** | **0 题**（SAMPLE-004 是 VPN 不可达） | — | 未验证 |
| misc | `unpack_archive`/`decode_blob`/`hexpeek`/`tcp`，**0 张卡** | 2 题（解码类） | 2/2 | 只验证了编码/解包 |

平台题库真实构成（`runs/_bigbank_ids.json`）：web 224 / SAMPLE 混合 15（web 6、reverse 3、misc 3、pwn 2、crypto 1）。
**练习库里几乎没有非 web 题**，这不是我们的选择，是平台题库事实。

---

## 2. 覆盖度：最大的短板是「没测」

| 题库 | 总题 | 测过 | 没测 | 备注 |
|---|---|---|---|---|
| CTFARENA | 50 | 50 | 0 | web，66% |
| CYBENCH | 40 | 40 | 0 | web，38%（最弱子库） |
| UPLOADLABS | 21 | 21 | 0 | web，95% |
| SAMPLE | 15 | 14 | 1 | 混合，非 web 多在此 |
| CVE | 2 | 2 | 0 | web |
| AGENTBENCH | 2 | 2 | 0 | AI/注入，0/2 |
| AGENT-DEFENSE | 2 | 2 | 0 | 防御题（无有效战绩） |
| GAUNTLET | 1 | 1 | 0 | 实际已拿 flag，禁提交→假阴性 |
| **XBOW** | **50** | **0** | **50** | 真实世界硬靶场，完全没跑 |
| **SQLILABS** | **50** | **0** | **50** | 教科书 SQLi，完全没跑 |
| 合计 | 233 | 132 | **101** | 43% 盲区 |

**为什么这是最贵的问题**：正式赛按解题数计分，而我们连「在 XBOW/SQLILABS 上能拿几分」都不知道。
尤其 SQLILABS 是「本该拿满」的题库——我们内置了 sqlmap 封装（`sqli` 工具），但它在 128 题里只被用过 **2 次**。

---

## 3. Web（主战场）：工具不缺，缺「用对」和「用起来」

### 3.1 工具采用率（128 题，按「这题用过该工具」统计，含调用次数）

| 工具 | 用过的题 | 解出题中使用率 | 未解题中使用率 | 总调用 | 判读 |
|---|---|---|---|---|---|
| `submit` | 82 | **97%** | **12%** | 163 | 提交是分水岭 → O-E「先提交再补链路」正对此 |
| `http_request` | 115 | 96% | 82% | 4595 | 主力 |
| `shell` | 108 | 79% | **94%** | 2687 | 未解题更依赖 shell = 手搓多、工具没接住 |
| `note` | 65 | **37%** | **73%** | 173 | 反向指标：笔记越多越可能卡住 |
| `write_file` | 95 | 64% | 92% | 1049 | 同上（写脚本手搓） |
| `http_burst` | 60 | 45% | 51% | 1049 | 已加撞墙 advice |
| `fuzz` | 53 | 37% | 49% | 192 | 采用率仅 41% |
| `traffic` | 34 | 22% | 35% | 163 | 线索复用少 |
| `jsrecon` | 32 | 24% | 27% | 96 | SPA 题该更常用 |
| **`sqli`** | **2** | 1% | 2% | **4** | ⚠️ sqlmap 封装形同虚设 |
| **`authdiff`** | **3** | 1% | 4% | 4 | ⚠️ 越权对照没被用 |
| **`jwt_forge`** | **2** | 1% | 2% | 12 | ⚠️ JWT 题没吃透 |
| **`decode_blob`** | **3** | 4% | 0% | 4 | ⚠️ 编码剥离几乎没用 |
| `verify_exploit` | 4 | 5% | 0% | 18 | 未被当成交付前校验 |

### 3.2 各子库短板

| 子库 | 解出率 | 未解主因 | 差什么 |
|---|---|---|---|
| CYBENCH | 38%（15/40） | `budget_exhausted×15` | 不是「不会」，是「耗到没预算」：需要更省的动作、更早收敛 |
| CTFARENA | 66%（33/50） | `coordinator_done×10` | 自己收口（线索没吃透/没提交）→ O-E 后应改善 |
| UPLOADLABS | 95%（20/21） | — | 上传链基本打通（O-F 补了 multipart） |
| GAUNTLET | 0/1（假阴性） | 禁提交 403 | 静默库需改成「report + 平台评分」，别再判失败 |

### 3.3 Web 能力缺口（按重要性）

1. **101 题未测**（XBOW/SQLILABS）→ 没有难度/题型基线与短板定位。
2. **专用工具不被触发**：`sqli`/`authdiff`/`jwt_forge`/`decode_blob` 采用率 1–4%。
   工具实现了但提示词没把「什么时候必须用」写死 → 与 O-D 同类问题，需要「工具选择判据」。
3. **无 JS 运行时/浏览器**：只有静态 `jsrecon`。客户端加密、需执行的逻辑、WebSocket 题都没有手段。
4. **无 OOB / 回调通道**：blind SSRF、blind XXE、blind XSS 依赖外带通道；平台若不给回调域名，这类题只能靠时序盲猜。
5. **无 WebSocket 工具**：SSE/WS 类实时题只能靠 `chat`（仅 AI 场景）。
6. **上传→包含→执行链**仍是手搓（虽已补 multipart 原子能力）。
7. **口令/OTP 爆破**依赖 `http_burst`，缺「先判锁定策略再爆破」的判据（O-D 已覆盖一部分）。

---

## 4. AI / Prompt-Injection：知识最全、执行最差

- **有**：`chat(url, message, session, reset, history, mode)`，支持 SSE/流式、多会话；20 张 ASI 卡（ASI01–ASI10 + 陷阱类：hidden_dom、unicode_zero_width、context_bomb、tarpit、honeypot、output_filtering 等，100+ 标签）。
- **实测**：AGENTBENCH-002「TravelMate」注入链**打通了**（lang 字段伪造平台通知让模型自认已验证），
  但卡在业务标识 `booking_ref` 无法获得；其余（001/003/004）以 `no_more_actions`/`budget_exhausted` 收口。
- **差什么**：
  1. **未知标识符 oracle 策略缺失**：注入成功后需要「让目标自己吐出/枚举出 ID」的成套手法（报错差异、默认参数、通配、列表端点）。这次就是死在这一步。
  2. **`chat` 不解析结构化 `tool_calls`**：目标 agent 若把工具调用返回成结构化字段，无法判定「是否被我劫持成功」。
  3. **AGENT-DEFENSE（防御题）没有专门策略**：只有 1 张 `agent_defense` 卡，2 道题无有效战绩。
  4. **载荷批量验证未自动化**：卡里有「同一目标换 3–5 种说法」，但仍靠手写 `chat` 逐条发，没有 burst 化。

---

## 5. rev / pwn / crypto / misc：工具是「stdlib 再造」，但缺关键件

设计思路是「镜像 stdlib-only，所以用纯 Python 重写」：`elfpeek`（ELF 解析/反汇编/gadgets/pyc）、
`pwngen`/`pwnfind`、`rsatool`/`aesblock`/`prng`、`unpack_archive`/`decode_blob`。方向没错，但覆盖有限：

| 方向 | 现有 | 缺 |
|---|---|---|
| rev | elfpeek（info/symbols/strings/gadgets/disasm/bytecode）、hexpeek、tcp | **反汇编引擎**（capstone 直到 v4 才有；此前 disasm 退化成字节视图）、无 gdb/动态调试、无 PE/Mach-O 深度解析、无 Java/.NET/WASM、无 **方法卡** |
| pwn | elfpeek（gadgets/lookup）、pwngen（cyclic/build）、pwnfind、tcp/tcp_batch | 无 gdb、无 ROPgadget/one_gadget、无 libc 数据库（只有附件 libc 的 lookup）、**无堆利用辅助**（tcache/malloc 布局）、无 seccomp 分析、无方法卡 |
| crypto | rsatool（small_e/common_modulus/wiener/fermat）、aesblock（ECB/CBC/CTR）、prng（MT 恢复/预测）、tcp_batch | 无 ECC、无离散对数、**无格攻击（Coppersmith/LWE）**、无哈希长度扩展、无 z3/sympy（SAT/符号求解）、padding oracle 无专用流程、无方法卡 |
| misc | unpack_archive（zip/tar/gz/bz2/xz）、decode_blob（base64…）、hexpeek、tcp | 无 PIL（图像题）、无音频、无二维码、无 binwalk/zsteg、无磁盘/内存/浏览器取证、无 node/jq、无方法卡 |

**重要前提**：这些方向**没有一次有效实测**。SAMPLE 里唯几道非 web 题（pwn 003、crypto 004、misc 012）
是 VPN 不可达，本地只能看到「连不上」，看不到能力。

---

## 6. 横切缺口（优先级高于单方向补工具）

### 6.1 镜像 stdlib-only + 运行期无网络 + 删了 pip → 任何第三方 import 必崩

历史转录里 agent 自己写脚本尝试过的库：

| 库 | 出现次数 | 镜像里 |
|---|---|---|
| `capstone` | **90** | ❌ 没有（v4 才计划加） |
| `requests` | 29 | ❌ 没有（应改用 `urllib.request`，或补进镜像） |
| `angr` | 9 | ❌ 没有 |

而本地开发机有 numpy/PIL（镜像没有）→ **本地跑得过的脚本，镜像里未必跑得过**，本地数据要打折看。

### 6.2 方法卡严重偏科

| 方向 | 方法卡 |
|---|---|
| web | 25 张（覆盖 ~80 个 tag） |
| ai | 20 张（ASI01–ASI10 + 陷阱/防御） |
| rev / pwn / crypto / misc | **0 张** |

### 6.3 角色工具面 & 步数

- `coordinator` 无本地工具（只能 delegate/report/note 等），13 个专家角色上限 10–14 步；
- 上下文 32k、单次工具输出 6000 字符；
- 132 题里有 **24 题 `budget_exhausted`**（主要在 CYBENCH）→ 预算/动作效率仍是主因之一。

### 6.4 平台提交通道差异

`GAUNTLET` 库 `silent_competition`：`submit` 返回 403，agent 已拿到 flag 却被判未解。
正式赛若遇静默库/防御题，需要明确「用 `verify_exploit`/report 交付」而不是当失败。

---

## 7. 建议做的事（按 ROI 排序）

### P0（本周内，能直接换成分数或信息）
1. **补测 101 题基线**：先 5 题 SQLILABS + 3 题 XBOW 校准（约 4M token），确认
   「sqlmap 封装能用吗」「XBOW 难度到底多高」，再决定是否全量。验收：得到每库解出率与失败主因。
2. **修镜像第三方库缺口**：v4 镜像除 capstone 外，加 `requests`（~0.5MB）与 `file`/`unzip` 两个 apt 包；
   并在提示词里明确「镜像无 numpy/PIL/angr，别 import」。验收：镜像内 `python -c "import requests"` 通过，
   冒烟里加一条「脚本只 import stdlib+capstone+requests」。
3. **AI 方向补「标识符 oracle」卡 + `chat` 解析 tool_calls**：直接把 AGENTBENCH-002 这类
   「注入成功但拿不到 ID」的失败变成可解。验收：重跑 AGENTBENCH-002 能拿到 flag 或明确的新失败点。

### P1（下一轮优化）
4. **工具选择判据**：像 O-D 一样把「什么情况必须用 `sqli`/`authdiff`/`jwt_forge`/`jsrecon`」写进 web 提示词
   （现在采用率 1–4%）。验收：这 4 个工具采用率 ≥15%，且 SQLILABS 5 题样本解出率 ≥60%。
5. **CYBENCH 撞预算攻坚**：O-D/O-E 之后复测 8 题 CYBENCH，看 `budget_exhausted` 是否下降。
6. **静默库/防御题交付方式**：遇 `submit` 403 时改走 `verify_exploit` + report，并记 note 区分
   「答案对但通道封」与「真没解出来」。验收：GAUNTLET 记为已解。

### P2（长期）
7. **非 web 方向造自测题**：SAMPLE 里非 web 只有 6 题且多 VPN，建议用 3 道公开题（1 rev/1 pwn/1 crypto）
   端到端验证工具链——否则这些方向的代码是「写了没验」的负债。
8. **补方向方法卡**：rev/pwn/crypto/misc 各 3–5 张（先从 SAMPLE 题的解法反推）。
9. **能力扩展**（无数据支撑，最后做）：浏览器/JS 执行、WebSocket、OOB 回调、图像/音频取证。

---

## 8. 复现

```powershell
python runs/_cap_inventory.py   # 角色 × 工具矩阵 + 方法卡覆盖 + 镜像工具链
python runs/_cap_stats.py       # 按方向/题库战绩 + 工具使用与报错
python runs/_cap_web.py         # web 工具采用率（成功/失败画像）
python runs/_cap_coverage.py    # 题库覆盖度（哪些没跑过）
python runs/_cap_envdiff.py     # 开发机 vs 镜像的库差异
python runs/_cap_ai.py          # chat 工具能力 + AI 方法卡
```
