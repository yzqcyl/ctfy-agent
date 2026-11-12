# CTF Agent 项目完整度审计（2026-11-02）

> 方法：不采信文档结论，全部项目重新实跑/实测。每条结论后面标了验证方式。
> 环境：`E:\ctfy_project`，HEAD `89739a5`，Python 3.12（DSH 运行时）。

---

## 0. 一句话结论

**交付链是完整的、能打的；短板不在"做没做"，在"有没有被回归网和运行时覆盖"。**
代码 1.65 万行 / 25 个本地工具 / 8 个角色 / 25 张方法卡 / 152 条单测全绿；容器五条契约路径全通；
平台侧 19 场题库、211 道可达 web 题可练。**5 个缺口**（run_mode 覆盖、CI 不跑测试、测试未入总入口、
README 失真、本地运行污染工作区）都不影响"能提交"，但会影响"改坏了没人知道"。

---

## 1. 代码与测试

| 项 | 实测值 | 验证方式 |
|---|---|---|
| `src/ctfy_agent/*.py` | **32 个文件 / 16,493 行** | `find src -name "*.py" \| xargs wc -l` |
| 测试文件 | **34 个**（1 总入口 + 33 套件） | `ls tests/*.py` |
| 总入口单测 | **152 passed / 0 failed** | `python tests/smoke.py` |
| 容器契约路径 | **5/5 exit 0**（dry_run / no_task / bad_run_mode / no_creds / no_mcp） | `python tools/container_check.py --local` |
| 平台握手 | ctfy **3.4.6**、**96** 个工具、doctor 通过 | `python -m ctfy_agent doctor` |
| 本地工具 | **25 个**（http_request/fuzz/sqli/authdiff/jsrecon/shell/elfpeek/rsatool/…） | `LocalTools.names()` |
| 方法卡 | 25 张（AI 21 + 领域 8，含交叉） | `method_cards` 计数 |

> 首轮跑出 1 个 `FAIL traffic_store`，定位为**本机 HTTP 代理**把 `127.0.0.1:1` 请求变成 502
> （`http_proxy=http://127.0.0.1:52330`）。加 `NO_PROXY=127.0.0.1,localhost` 后 152/0，
> **不是代码缺陷**。这条要写进 README：本地跑测必须绕过代理。

### 1.1 未被总入口覆盖的独立测试套件

`smoke.py` 只 import 了 17 个模块。仓库里另有 **33 个 `tests/smoke_*.py`** 完全不经过总入口，
我全跑了一遍：

- **25 个不依赖凭据的**：全部 rc=0（burst_group / domain_cards / evidence / http_cache /
  http_multipart / http_save / local_tools / vpn_advice / crypto 27项 / pwn 24项 / rev 15项 /
  webai_gap 12项 / ai_* / context / gate / webmap / progress / llm_retry / http_session / case_misc）。
- **8 个要读真实 `.env` 的**（ai_role / brake / clustera / clusterb_crew / ctxopt / p1 / p2 / p21）：
  本机有凭据能跑，CI 没有 → 在 workflow 里显式 SKIP，不制造假红。

结论：**逻辑都是对的，但"绿不绿没人自动检查"**——总入口和 CI 原先都碰不到它们，改坏了不会报警。
已把 25 个接进 CI（见第 6 节）。

---

## 2. 交付形态（容器镜像）

| 项 | 状态 | 证据 |
|---|---|---|
| Dockerfile | 完整：sqlmap/ffuf 安装 + 自检、非 root、`ENV LLM_ENABLE_THINKING=0`、`ENTRYPOINT python -m ctfy_agent agent` | `Dockerfile:79-203` |
| 五条契约路径 | 全通（本地模式） | `container_check.py --local` |
| 镜像 v4 | 文档记录已推送（digest `4e153aeb…`），**本机无 Docker，无法复验** | `docs/archive/HANDOFF-2026-11-08-对外交接版.md §0` |

### 2.1 CI 不跑任何单测（缺口 #2）

`.github/workflows/build-agent-image.yml` 只有 3 步：build → `container_check --no-build` → 存档。
**没有 `tests/smoke.py`**。也就是说，单测全绿这条防线在云端构建时是缺席的：
只要五条契约路径还能走通，逻辑回归可以一路进镜像。

---

## 3. 运行时覆盖的缺口

### 3.1 `run_mode` 只支持 instance（缺口 #1，风险最高）

`src/ctfy_agent/agent_main.py:410-415`：`if run_mode != "instance": → 写空 result.json + exit 0`。
平台契约里 `run_mode` 还有 `qa / defence / engagement / awd` 四种；平台也确实有
`Agent AWD 表演赛`、`MMLU-CS`（qa 类）。**如果正赛用到任一非 instance 模式，交付物会静默拿 0 分**
（exit 0 不算失败，只是没答案）。

### 3.2 实验开关默认全关

`cluster_a_concurrency` / `clusterb_k` / `budget_cache_discount` 等在 `config/agent.json` 的 `extra` 里
**都不存在**（`extra={}`），即默认走串行基线。这与 HANDOFF 记录一致（k=3 实测有害），不算缺陷，
但意味着**代码里那部分并发/多尝试逻辑在生产路径上是冷代码**。

---

## 4. 文档与仓库卫生（缺口 #4、#5）

### 4.1 README 有 6 处与现状不符

| README 说 | 实际 | 位置 |
|---|---|---|
| "纯 Python 标准库（不装任何依赖）" | 镜像内置 sqlmap、ffuf、capstone | `README.md:3` |
| "离线自测，18 项" | 152 项 | `README.md:23,137` |
| "单个 DeepSeek 跑 12 步" | web/ai/rev/crypto/pwn 均 14 步 | `README.md:48` |
| "每道题 30 万 token、20 次调用" | 1,200,000 token、100 次 | `README.md:82,88` |
| "还没做 Web/Pwn/AI 解题器、调度层" | 三个方向都已落地（工具面+方法卡+夹具） | `README.md:145-151` |

README 是新会话/外部评审的第一入口，这份失真会直接误导排期判断。

### 4.2 本地运行把产物写进仓库根目录

`git status` 里的未跟踪项大多不是"忘了提交"，而是**跑题时泄漏到根目录的工件**：

| 路径 | 内容 | 推断来源 |
|---|---|---|
| `js/{app,entry,start}.js`、`bundle.js`、`assets_index.js` | 某道 web 题的 JS bundle | 11-01 22:42–22:55 的本地 run |
| `work/capture.pcap` | 某道 misc 题的流量包 | 11-01 17:24 |
| `x/traffic/` | 流量库 sqlite | 10-31 23:36 |
| `%TEMP%/bisect_*` | 未展开的 Windows 环境变量被当成目录名 | 10-30 |

比赛容器里 cwd=`/ctfy/work` 且沙箱受限，不会外溢；**但本地跑题会污染工作区，
多题之间还可能串数据**（流量库/x 目录）。建议本地运行时显式指定 run 目录并把沙箱锚到 `runs/<run>/sandbox`。

### 4.3 三份关键实验报告未入库

`docs/archive/gate2-context-ab-2026-11-01.md`、`docs/archive/p0-calibration-2026-10-31.md`、`docs/archive/eval-mix-live-2026-10-30.md`
都是 untracked —— 里面恰好是"缓存折价 −44%""SQLILABS 5/5""XBOW 0/1"这些要被反复引用的结论。

---

## 5. 平台与题库

### 5.1 题库盘点（`list_competitions`，19 场）

| 库 | 题量 | 难度分布 | 可达性 |
|---|---|---|---|
| CTFARENA | 50 | easy 2 / medium 14 / hard 34 | ✅ `*.ctfy.run` |
| CYBENCH（公开基准 40 题） | 40 | 11 / 27 / 2 | ✅ |
| XBOW（公开基准 104 题子集） | 50 | 22 / 26 / 2 | ❌ 实例是 `*.internal`，本机 DNS 不解析 |
| SQLILABS | 50 | 8 / 36 / 6 | ✅ |
| UPLOADLABS | 21 | 2 / 11 / 8 | ✅ |
| MMLU-CS / CTI-MCQ / SECQA | 50 / — / — | 纯问答 | 需 qa 模式（当前不支持） |
| CVE / GAUNTLET / AGENTBENCH / AGENT-DEFENSE | 2 / 1 / 2 / 2 | 少 | 视题而定 |

**关键发现：XBOW 那 50 道在本机根本打不通。** 实测启动 `XBOW-020` 得到
`smeof6jer23pocyaf.internal`，`socket.gethostbyname` 直接 `getaddrinfo failed`（无 VPN）。
所以 10-31 报告里"XBOW 0/1"**不能当作能力结论**，那是环境不可达。

### 5.2 实例配额：并发不可能（实测）

- 第 1 个实例：`start_instance` 成功，**56.7 s** 就绪。
- 第 2 个实例（另一个 challenge）：**0.5 s 被拒**（`isError=true`）。
- 源码侧同样约束：`ctfy/server/instance_start.py:270` 有 `per_team_cap`，规则文档写的是 **1**。

→ 只要题需要实例，**多题并行在平台侧就被堵死**；吞吐上限 = 串行。
这也是"比赛时同样只有 1 个实例"，所以串行反而是忠实模拟。

### 5.3 两个网关事实（影响成本判断）

1. **`enable_thinking:false` 被忽略**。带该参数请求仍返回 `reasoning_content`
   （实测 `curl ... "enable_thinking":false` → 响应里 reasoning 照常存在）。
   镜像里那条 `ENV LLM_ENABLE_THINKING=0` 在本网关**不产生效果**，思考 token 照常计输出。
2. **缓存确实生效**。`usage.prompt_tokens_details.cached_tokens` 单题实测 **88.8% 命中**
   （260,087 prompt 里 231,040 命中），`budget.py:177-182` 解析的是嵌套字段，口径没错。

---

## 6. 缺口清单（按风险排序）

| # | 缺口 | 影响 | 状态 |
|---|---|---|---|
| 1 | 只支持 `run_mode=instance` | 非 instance 模式直接 0 分 | **部分修**：CLI/crew 路径的 QA 提交已通（见 6.2），容器 `run_mode=qa` 仍缺 |
| 2 | CI 不跑单测 | 逻辑回归可进镜像 | **已修**：workflow 加两步（总入口 + 独立套件） |
| 3 | 33 个测试套件不在总入口 | 回归网漏面 | **部分修**：25 个无凭据套件已接进 CI，任一失败即红 |
| 4 | README 6 处失真 | 误导排期/评审 | 未修（见 4.1 表） |
| 5 | 本地运行污染仓库根 | 多题串数据、git 脏 | 未修 |
| 6 | XBOW 本机不可达 | 该库结论不可用 | 环境限制，报告中已标注 |
| 7 | `enable_thinking=0` 无效 | 思考 token 计费 | 网关限制，无法在代码侧修 |
| 8 | `submit` 默认题号回落 `flag` | 多问题题正确答案被 422 丢弃 | **已修**（见 6.1） |
| 9 | 部分解被兜底改写成整题解出 | 完成度虚高 | **已修**（见 6.1） |
| 10 | QA 题提交被「需要实例」挡死 | MCQ 答对也 0 分 | **已修**（见 6.2） |

### 6.1 实测暴露的两个真缺陷（已修 + 有回归用例）

这两个都是**跑真实题才暴露**的，纯跑单测碰不到 —— 也是这次实测最大的产出。

**缺陷 A：多问题题提交时题号回落 `flag`，正确答案被丢弃**

- 现象：`CTFARENA-048`（3 题号：user/admin/techniques）第一轮
  `verify_answer(question_id=user)` 返回 `true`，紧接着 `submit(answer=...)`（不带题号）
  → 平台按默认 `flag` 提交 → **422 `unknown_question_id`**，知道的答案就此丢掉，
  bench 记成未解。
- 根因：`platform.py:_repair_runtime_args` 只在「模型给了**错的**题号」时纠正，
  「**没给**题号」时原样放行，落回平台默认值 `flag`。
- 修复：`verify_*` 判 `true` 时记住题号（`_verified_qid`），后续 `submit` 缺省就沿用它；
  单问题题直接用唯一题号；多问题且无验证记录时仍不猜（避免把答案错投到别的题上）。
  `solver._submit` 同步用 `platform.preferred_question_id()`。
- 回归用例：`tests/smoke.py::case_submit_question_id`（总入口第 154 项）。
- **A/B 复测**：同题同预算只换代码，`CTFARENA-048` 从「未解」变为
  `correct:true, question_id:"user"`（1/3 题号）。

**缺陷 B：部分解被 `get_my_solves` 兜底翻成整题解出**

- 现象：缺陷 A 修好后复测，`challenge_fully_solved:false` 已被正确识别为 `partial_solve`，
  但收尾时的 proof-of-exploitation 兜底（`crew.py:1151`）看到平台 `my_solves` 里有这题，
  又把 `submitted` 翻回 `True`、`reason` 改成 `exploit_verified` → **1/3 的完成度被报成整题解出**。
- 修复：兜底排除 `partial_solve`（`superseded` 同理）。
- 口径影响：bench 的 `submitted` 只代表「平台至少记了一个题号」，多问题题的完成度要看回执里的
  `challenge_fully_solved`。

单测：**153 passed / 0 failed**（较审计初期的 152 项新增 1 项回归用例）。

### 6.2 第三轮（11-03 六题型）暴露的缺陷：QA 题提交通道整条不通

- 现象：`SECQA-0001`（MCQ）模型第一轮就选对 D，但每次 `submit` 都返回
  **「本题还没有实例（instance_id 为空），先 start_instance 再提交」**，最终记 `no_candidate`。
- 两层根因：
  1. `normalize_challenge` 从来没映射 `is_qa_only`，`Challenge` 也没有这个字段
     → 提交一律走 `submit_answer` 的 instance 分支；
  2. 改走 `submit_qa_answer` 后仍失败：该工具按 `(competition_id, challenge_id)` 定位，
     payload 里没有 `competition_id` → 400。
- 修复：`cli.normalize_challenge` + `state.Challenge` 补 `is_qa_only`；
  `solver._submit` 对 QA 题改用 `submit_qa_answer` 并带上 `store.meta.competition_id`，
  且不再塞默认题号 `flag`。
- 回归用例：`tests/smoke.py::case_qa_submit`。
- 效果：`SECQA-0001` 从「0 分 + 57 s + 33k token」变成 **8.9 s / 2.1k token 通过**；
  整组 12 道 MCQ **12/12** 全对，平均 12 s、2.8k token。

单测：**154 passed / 0 failed**。

### 6.3 平台侧两条硬约束（实测，影响所有后续计划）

1. **每题 `start_instance` 硬上限 = 5 次**。用尽后返回
   `start_instance 已达本题上限 5 次`，换 ttl / 换 competition_id 都绕不过 —— 这道题当天就废了。
   六题型这批里 **11 道**因此完全没被测到（占比 21%）。
2. **部分 CYBENCH 题的实例只给 VPN 地址**（`access:"vpn"`、host 形如 `*.internal`），
   本机 DNS 直接解析失败 → 这类题在本机是**不可测**，不是「不会」。

→ 建议：agent 侧对 `start_instance` 做退避（等 provision 而不是连打 5 次），
批量跑题前也不要为了探配额反复启停 —— 我第一轮就是探了两次，触发上游限流带崩一批。

---

## 7. 跨库实测（正确率 / 花费 / 速度）

见 `docs/archive/bench-1102-report.md`（由 `runs/_bench1102_report.py` 从原始 jsonl 生成）。
抽样口径：未解池随机抽样 12 题（seed=20261102），单题预算 400k token，串行执行。

**一句话结论**：平台确认解出 **5/11 = 45%**；剔除 3 道「实例起不来」的环境阻塞后 **5/8 = 63%**；
把「已拿到 flag 但被 400k 预算/工具缺陷截断」算进来是 **6/8 = 75%**。
平均 **396 s/题**、**308k token/题**、缓存命中 **90.6%**，按公开价近似 **¥0.15–0.22/题**。
