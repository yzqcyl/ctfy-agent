# Agent 能力评估（2026-11-19）

> 口径：只写**可核对的事实**（代码/配置/CI/跑批记录/文档），推断处标「推断」。
> 数据截点：2026-11-19 09:40（HEAD `c62e999`），`tests/smoke.py` 现场跑过 **180 passed / 0 failed**。
> 每类结论后给出证据来源，方便复算（§7）。

## 0. 一页总览

| 方向 | 判定 | 最新硬证据 | 主要缺失 |
|---|---|---|---|
| 平台接入 / 提交 / 实例 / 网络 | ✅ 强 | ctf2 + CTFY 双平台实测通；SAMPLE-023 端到端 2.8min 解出并记分；实例自愈、attempt 账本、per-instance WireGuard 桥 | 单实例槽（ctf2 并发 2 互踩）；大附件无 URL；wireproxy 不在镜像里 |
| 编排 / 运行时（角色·预算·账本） | ✅ 强 | 7 角色、40 步/角色、`max_llm_calls=240`、单题 800k、32k 上下文、ledger 去重、14 张方法卡 | 预算按题不按类别，PWN/CRYPTO 易烧穿；背景重复注入挤压上下文 |
| WEB | ✅ 强 | 24 题矩阵 hard web **4/4**；SAMPLE 9/9；13 个专用工具 | 无浏览器/JS 引擎、XSS 只能推理不能验证、无 WAF 策略库 |
| REVERSE | ✅ 强 | 矩阵 **6/6**（含 hard）；外部题累计 8/8；Ghidra headless + capstone + .pyc | Ghidra/gdb_pwn **不在镜像里**（`tools/` 被排除）；无 .NET/Java/APK 反编译 |
| PWN | 🟡 中（在追） | 矩阵 **5/6**；但大样本 120 题里仅 **3/31**；pwnlocal/pwnkit/pwnsession 已就位 | 堆利用/FSOP 只有文档没工具；协议/菜单题弱（easyROPtocol 6 轮未解）；pwndbg-lite 不在镜像 |
| CRYPTO | 🟡 中偏强 | 矩阵 **4/4**（含 hard）；120 题 **16/30**；LLL/Coppersmith 纯 stdlib 已实现 | 无 ECC/离散对数专用工具；无 Sage；格攻击只支持根模 N 场景 |
| MISC / FORENSICS | 🔴 短板 | 矩阵 **1/4**；120 题 **9/30**；未解 4 题全撞 800k 上限 | 缺 tshark/pyshark 级流量分析、磁盘镜像/内存取证、OCR；隐写深度不够 |
| AI（LLM 应用/Agent 安全） | ⚪ 未验证 | 角色 + `chat` + 注入打法已写进 prompt（OWASP ASI 族） | 题库无样本，能力未测；无多模态 |
| 比赛形态 / 自动化 | ✅ 基本就绪 | `comp_auto`（矩阵选题/监控/分析）+ watch/keeper + VPS 彩排 8/8 跳过 + 离线自查 | 镜像缺 wireproxy（平台托管形态会断 VPN 题）；镜像内工具面与 prompt 承诺不一致 |
| 工程 / 质量 | ✅ 强 | 78 个 smoke 套件 / 21.9k 行；CI 跑全量单测 + 密钥扫描 + 构建 + 契约校验 | 解题成功率没有 CI 回归集（只能靠真实跑批）；成本/耗时无看板 |

**一句话**：工程与平台链路已经是「可参赛」水平，难点已经从「能不能跑」收敛到 **MISC 套路深度、PWN 难题、以及镜像内工具面与 prompt 的一致性**。

## 1. 规模与验证基线（事实）

| 项 | 值 | 来源 |
|---|---|---|
| 运行时代码 | `src/ctfy_agent/` 46 文件 / 29,105 行（GUI 另 11 文件 / 6,100 行，仅本地开发） | 现场统计 |
| 测试 | `tests/smoke*.py` 78 个 / 21,939 行 | 现场统计 |
| 主回归 | `python tests/smoke.py` → **180 passed / 0 failed** | 2026-11-19 09:33 实跑 |
| CI | `.github/workflows/build-agent-image.yml`：smoke.py + 全部 smoke_* + `secret_scan.py --all` + 镜像构建 + `container_check.py`（手动触发，按 digest 提交） | workflow 原文 |
| 镜像 | v8：327.3MB / 23 层，digest `sha256:b1d975fd…`，匿名可拉取 | `docs/capability-pack-v8-2026-11-13.md` |
| 本地工具面 | 17 个域工具 + 5 个扩展工具 + 核心工具（shell/pwnlocal/http_*/chat 等）≈ 35+，按角色派发 | `src/ctfy_agent/tools_*.py` + `config/agent.json` |

## 2. 平台接入 / 提交 / 实例 / 网络（强）

**能做什么**
- 双平台：ctf2（DASCTF 练习场，9 个动作 `tool_map`）与 CTFY（MCP 99 工具，`docs/competition-tpctf-2026-11-19.md` §2）。
- 实例：`start/stop/renew/get`、自愈（回执无 id 时用按题原语探活→回收→重启），`instance_ops_limit=5`，`instance_absent` 负缓存。
- 提交：平台 attempt 计数并入 `Challenge.platform_attempts`，用满硬停（不再白交），回执回显「第 N/M 次」；`submit_limit=10`、`platform_submit_limit=10`（当前工作树口径）。
- 网络：per-instance WireGuard → `netbridge` 拉平台 REST 配置 → 用户态 wireproxy + portmap 透明改写；SAMPLE-023（需隧道）**2.8 分钟解出并记分**。

**缺失 / 风险**
1. ctf2 全队只有 **1 个实例槽**，并发 2 会互踩（已改并发 1）；CTFY 比赛是 3。
2. 平台大附件题不给 URL（只有 `_attachments.json`）→ 53MB 级附件拿不到（文档记为 B1，平台侧缺陷）。
3. **wireproxy 不在 v8 镜像里**（`.dockerignore` 排除 `.tools/`）：VPS 挂 repo 的形态没问题，**平台托管跑镜像时所有 `access=vpn` 题必然连不上**（`competition-tpctf` §2.3 已记录，建议 v9 打进 `/ctfy/.tools/`）。
4. `config/agent.json` 的 `mcp_url`/`tool_map` 就是「当前 active 平台」开关：并行会话会互相切（`ebd1be1` 已修 comp_auto 的一处）。**当前工作树里该文件是 ctf2 口径、未提交**——赛前必须确认镜像/容器用的是 CTFY 口径。

## 3. 编排与运行时（强）

**能做什么**
- 7 角色：coordinator + web / ai / rev / crypto / pwn / misc；每角色 40 步，`max_calls_per_step=8`，`max_steps=40`。
- 预算：`max_llm_calls=240`、`max_tokens_per_challenge=800000`、`max_context_tokens=32000`，并有全局 token 账本（`global-token-ledger.md`）。
- 反浪费：同参数重复调用 ledger 拦截、方法卡（14 张：pcap/webshell/forensics/stego/protocol + 各类兜底）、`analyze_batch.py` 复盘（工具软失败/shell 非零/重复动作/环境坑）。
- 转录：每题 jsonl 全量留痕，可回放思考过程（`runs/*/transcripts/`）。

**缺失 / 风险**
1. **预算按题一刀切**：PWN/CRYPTO 3–4 分钟就能烧掉 250k（`batch-2026-11-18-linux.md` §3）；misc 未解 4 题全撞 800k（矩阵复盘 §1）——「不是缺 token，是缺套路」。
2. **上下文被重复注入挤压**：同一题「题目背景」重复 9–14 次（`solving-record-review` §1.3），压缩频繁、真实推理窗口变小。
3. 撞预算时不强制收口：有题 stop 时 `attempts=0`，一个候选都没交（`batch-2026-11-18-easy.md` §3-2）。建议「80% 预算强制提交最优候选」。
4. coordinator 也只有 40 步，多题切换/收口压力大。

## 4. 分题型

### 4.1 WEB（强）
- 工具：`http_request`（窗口翻页/greps）、`http_map`（crawl/probe）、`http_burst`、`fuzz`（软 404 校准 + 内置字典）、`authdiff`（四变体鉴权对照）、`jsrecon`（bundle 静态侦察）、`sqli`（六类）、`jwt_forge`（含 RS256→HS256 / alg=none）、`listen`/`udp`、`traffic`。
- 证据：SAMPLE 9/9（v6 期）；24 题矩阵 **hard web 4/4**；120 题跑批里 web 样本少但无系统性失败。
- 缺失：① 无浏览器/JS 引擎，纯前端题只能读 bundle；② XSS 无实际利用验证；③ 无 WAF 绕过策略库（靠模型手试）；④ 运行期无外网，OOB 外带受限。

### 4.2 REVERSE（强）
- 工具：`elfpeek`（info/sections/symbols/strings/disasm/gadgets/xrefs/bytecode，capstone 反汇编，.pyc/PyInstaller 提取）、`hexpeek`、`fileview`、`z3solve`、`pwnlocal`（Linux 容器内本地跑 + `gdb -batch`）、`uncompyle6`；Ghidra headless 反编译（`tools/linux_bootstrap/ghidra_decompile.py`，**按 PATH 探测**）。
- 证据：外部题 8/8（v6 期）；24 题矩阵 **6/6（含 2 道 hard）**。
- 缺失：① **Ghidra / gdb_pwn（pwndbg-lite）不在镜像里**——`tools/` 被 `.dockerignore` 排除，只有「挂 repo 跑」的形态能用；② 无 .NET/Java/APK 专用反编译器（androguard/angr 明确不进镜像）；③ 无调用图（`view=calls` 未做）；④ 大二进制 + 混淆题受步数/预算限制。

### 4.3 PWN（中，在追）
- 工具：`pwnlocal`（本地原生运行 ELF + stdin/argv + `gdb=true` 抓寄存器/崩溃点）、`pwnsession`（交互式 session）、`pwnkit`（libc 识别 / ret2libc 推导 / ASan shadow）、`pwnrop`/`pwngen`/`pwnfind`、`elfpeek`、`tcp`/`tcp_batch`、`listen`；镜像内 pwntools/ROPgadget/patchelf/gdb 齐全。
- 证据：24 题矩阵 **5/6**（2 hard 全解）；但 120 题大样本 **3/31**——差别在题型：栈题/ret2text 稳，堆题、协议题不行。
- 缺失：① 堆利用/FSOP 只有 `docs/pwn-heap-fsop-recipes.md`（方案定、文档/偏移表未建），无工具化；② one_gadget 离线库未建（2.27/2.31/2.35 字典待核对）；③ **协议/菜单式题弱**：easyROPtocol 连跑 6 轮未解（79 次 shell、11 次 pwnlocal，没构造出报文序列）；④ pwndbg-lite 同样不在镜像；⑤ 32 位静态/ASan 题对本地验证要求高，Windows 开发机做不了（已用 Linux 容器解决）。

### 4.4 CRYPTO（中偏强）
- 工具：`rsatool`（small_e/common_modulus/wiener/fermat/pollard/hastad… + `op=lll`/`op=coppersmith`，`lattice_math.py` 488 行纯 stdlib）、`aesblock`、`pycrypt`（AES-GCM/CFB/OFB/EAX、3DES）、`prng`（MT/LCG）、`z3solve`、`tcp_batch`（padding oracle 盲打）。
- 证据：120 题 **16/30 (53%)**，其中容器题 6 解 2；24 题矩阵 **4/4（含 2 hard）**。
- 缺失：① 无 ECC/离散对数专用工具（靠 shell 手写）；② 无 SageMath（明确不做，500MB+）；③ 格攻击边界：只支持根模 N，beta<1 需 25+ 维直接报错；④ HNP/多元小根等进阶场景没有配方卡；⑤ 预算消耗大。

### 4.5 MISC / FORENSICS（短板）
- 工具：`decode_blob`（多层编码链）、`unpack_archive`（魔数识别 + Zip Slip/炸弹防护）、`hexpeek`、`fileview`、`write_file`、`tcp_batch`；v8 镜像再补 binwalk / foremost / steghide / exiftool / p7zip / poppler / john + Pillow / zxing-cpp（二维码）/ scapy / dpkt / pyzipper / qrcode。
- 证据：**24 题矩阵 1/4**（hard misc 0/2）、120 题 9/30；4 道未解全部撞 800k。典型卡点：菜刀流量（81 次 shell、33 次超时）、Webshell Plus（TLS/连接层失败 22 次）、BlueTrace（编码坑 23 次超时 20 次）。
- 缺失：① 无 tshark/pyshark 级流量分析（scapy/dpkt 在手但套路不成型）；② 无磁盘镜像分区/文件系统只读解析；③ 无内存取证（volatility 类）；④ 无 OCR（tesseract 刻意不装）；⑤ 隐写只有 steghide/binwalk，图片/音频深度分析靠手写；⑥ PDF/Office 结构化解析只有 poppler 基础命令。
- 备注：2026-11-19 已补 5 张 domain 卡（pcap/webshell/forensics/stego/protocol）+ misc category 兜底卡（commit `3649a66`），**效果待验证**。

### 4.6 AI / LLM 应用（未验证）
- 角色 `ai` 已就位：`chat`（多轮/SSE 聚合/cookie 会话）、`http_request`/`http_map`、注入打法（角色扮演、间接指令、编码绕道、工具滥用、会话记忆）、OWASP ASI01–ASI10 + 框架漏洞（Ollama/ComfyUI/Dify/n8n/Langflow/MCP server…）。
- 缺失：题库无 AI 样本，**能力未测**；无多模态；无 RAG/向量库题专用流程（可直接 HTTP 打）。

## 5. 横向瓶颈（按影响排序）

1. **单题预算 / 上下文效率**：真实烧速约 64k token/min；PWN/CRYPTO 3–4 分钟烧完 250k；misc 未解全撞 800k。**且撞预算时没有强制收口**（有题 0 次提交）。
2. **MISC 套路与工具深度**：矩阵 1/4 是当前最大短板，缺流量/取证/编码的「确定性流程」，而不是缺 token。
3. **镜像内工具面与 prompt 承诺不一致**：`tools/` 被排除 → gdb_pwn（pwndbg-lite）/Ghidra/one_gadget 库在镜像里不存在，但方法卡会引导模型去用；平台托管形态下这是硬伤（VPS 挂 repo 形态掩盖了它）。
4. **PWN 难题（堆/协议）**：栈题已稳，堆利用与协议状态机没有工具化配方。
5. **实例/网络/配额**：单槽实例、实例不可达、wireproxy 缺镜像、DNS 白名单（比赛机）——环境问题会直接毁题。
6. **平台数据缺陷**：大附件无 URL；练习场重签发 flag 会把「做对」记成「判错」（归因污染）。
7. **验证口径**：解题成功率无 CI 回归集，只能靠真实跑批；不同批次预算/环境不同，数字不可直接横比（11-18 linux 批次 0/4 用的是 250k 预算，11-19 矩阵 20/24 用的是 800k + v8 镜像）。

## 6. 建议路线

### P0（赛前，30 分钟内）
1. 确认模型/网关：容器内 `curl $LLM_BASE_URL/models`（断网由平台注入 `LLM_*`，见 `offline-posture` §5）。
2. 确认镜像 tag/digest 与平台批准一致；若平台托管跑镜像，**先补齐 wireproxy**（或接受 VPN 题放弃）。
3. `config/agent.json` 指向 CTFY（当前工作树是 ctf2 口径，未提交）——别让镜像/容器带错平台。
4. `python tools/offline_audit.py` ✅、`python -m ctfy_agent agent` dry_run 契约 ✅。
5. 实例配额 3 与 `comp_auto --concurrency 3` 对齐；平台 DNS/hosts 兜底确认。

### P1（本周）
1. **MISC 专项**：把 pcap 解析（scapy/dpkt）、磁盘镜像只读解析、隐写常用操作做成 3 个确定性工具 + 验证 5 张 domain 卡。
2. **镜像一致性**：v9 把 `tools/linux_bootstrap/`（gdb_pwn.py、one_gadget 库、堆 FSOP 偏移表）打进 `/ctfy/tools/`，或在 prompt 里删掉镜像内不存在的路径。
3. **收口硬规则**：预算 80% 强制提交最优候选（coordinator 层），并给 PWN/CRYPTO 单独类别 cap。
4. **上下文去重**：delegate 时背景只注入一次（引用式），把节省的窗口留给推理。
5. **PWN 协议/菜单题**：模板化「本地 tube 跑通全流程再打远端」流程卡，用 easyROPtocol 验证。

### P2（中期）
1. ECC/离散对数工具、格攻击扩展（beta<1 场景）、HNP 配方卡。
2. .NET/Java/APK 反编译（按体积评估）、调用图、符号执行（angr 体积再评估）。
3. AI 题样本收集与能力验证。
4. 自动化解题成功率回归集（固定 20–30 题 + 固定预算，作为 CI 之外的人工门禁）。
5. 成本/耗时看板（按类别、按难度、按 token/解出数）。

## 7. 证据索引（复算）

| 结论 | 文件 |
|---|---|
| 各题型能力范围/工具面/预算 | `docs/capability-report-2026-11-12.md` |
| 120 题大样本成绩（36/122，分类别） | `docs/REPORT-buuctf-120-2026-11-13.md` |
| 24 题矩阵 20/24（分类别） | `docs/test-matrix-2026-11-19.md` |
| v8 镜像补了什么/不装什么 | `docs/capability-pack-v8-2026-11-13.md` |
| PWN 工具（pwnkit/pwnsession/pwndbg-lite/one_gadget 方案） | `docs/pwn-hardening-2026-11-17.md`、`docs/pwn-toolbox-2026-11-17.md` |
| 本地验证缺失的根因（0 次 gdb / 0 次本地跑） | `docs/solving-record-review-2026-11-18.md` |
| 比赛机/自动化/VPN 真实机制 | `docs/competition-machine-2026-11-18.md`、`docs/competition-tpctf-2026-11-19.md` |
| 断网姿势与赛前检查清单 | `docs/offline-posture-2026-11-19.md` |
| 预算烧穿实测（250k/3–4min） | `docs/batch-2026-11-18-linux.md`、`docs/batch-2026-11-18-easy.md` |
| 主回归/CI 口径 | `tests/smoke.py`（180 passed）、`.github/workflows/build-agent-image.yml` |