# 各方向解题能力盘点（2026-11-13）

> 只读分析：数据来自 `runs/`、`runs/_big/` 产物、`config/agent.json`、`Dockerfile`、`docs/` 已有报告。
> 口径：**同一题按最近一次真尝试**统计；跨 run 重复只保留最新；QA/选择题单列；CyBench 等「category=web」
> 但实为 rev/pwn/crypto 的题单独标注（平台分类标签会污染方向统计）。

## 1. 数据源与可信度

| 数据 | 规模 | 时间 | 镜像/代码 | 用途 |
|---|---|---|---|---|
| BUUCTF 122 题跑批（`runs/_big/`） | 122 题（crypto/misc/pwn/reverse 各 30+） | 11-12~13 | 宿主环境 + 当时已装的 `tools/pylibs`；镜像 v8/v9 能力包是跑批**之后**才补进 Dockerfile 的（收益未复验） | 主证据：分方向解出率/根因/容器 vs 静态 |
| CTFY 平台练习场（`runs/plat`） | 43 题（web/rev/misc/pwn/crypto/forensics） | 11-10~12 | v6~v7.3 | 方向覆盖与实例链路 |
| CTFARENA 主批次（`run2`/`run4b`/`02`/`run5`） | 115 / 19 / 29 / 34 题，全部 web | 11-05 | v6.x | web 能力基线（旧模型，偏保守） |
| sqlilabs（`runs/sqlilabs`） | 69 个 SQL 注入关卡（10 个唯一靶标） | 11-09 | v6.x | web/SQLi 强项 |
| SAMPLE 自建银行（`runs/sample`） | 18 题（解出 15：web 6/6、rev 4/4、misc 4/4、pwn 1/3、crypto 0/1） | 11-09 | v6.x | 链路回归基线 |
| CTFARENA AI 安全题（category=web） | 19 题（hard 7 / medium 8 / easy 1 等） | 11-05~07 | v6~v7 | AI/LLM 方向唯一历史样本 |
| 2h BUUCTF 抽样（`docs/REPORT-buuctf-2h-2026-11-12.md`） | 8 题（4 类型 × Easy/Medium） | 11-12 | v7.3 | 时间预算/实例问题证据 |
| 全库聚合（536 个真实 run 去重） | 436 题 | 截至 11-13 | 混合 | 粗口径（跨库同名/标签污染），只看天花板 |

独立复算（`runs/_big` 的 manifest + 三轮结果 + `problems2.json`）：**36/122 = 29.5%**，与报告一致。

## 2. 总览（BUUCTF 122 题，跑批时环境）

| 分类 | 题数 | 解出 | 解出率 | 已解平均墙钟 | 未解根因（主） |
|---|---|---|---|---|---|
| CRYPTO | 30 | 16 | **53.3%** | ~114s | 协调者收口 8、墙钟 4、未派到 1、LLM 1 |
| MISC | 30 | 9 | 30.0% | ~196s | 协调者收口 13、墙钟 8 |
| REVERSE | 31 | 8 | 25.8% | ~232s | 协调者收口 13、墙钟 10 |
| PWN | 31 | 3 | **9.7%** | ~214s | 墙钟 21、协调者收口 5、未派到 2 |
| （合计） | 122 | 36 | 29.5% | — | 墙钟 43 / 收口 39 / 未派到 3 / LLM 1 |

**两个决定性结构事实**：
- **容器题 6/49 = 12.2% vs 静态题 30/73 = 41.1%**——依赖实例的题成功率只有静态题的 1/3；
- **未解题吞掉 92% token、93% 墙钟**（2h 轮实测），「能解」的题通常 1~2 步、30~200s 内收口。

难度×分类（解出/尝试）：CRYPTO easy 11/20、medium 2/4、hard 3/6；MISC easy 8/20、hard 1/6、medium 0/4；
REVERSE easy 6/21、hard 1/7、medium 1/3；PWN easy 3/28、medium 0/1、hard 0/2。

## 3. 各方向：强势 / 弱势 / 缺什么

### 3.1 WEB —— ✅ 最强方向（工具面最全，但 Hard 专项批次会掉）
**数据**（按真实批次拆，避免跨库同名/标签污染）：
- `run2` 83/115 = **72.2%**（easy 85.7% / medium 75% / hard 68.3%）
- `run4b` 16/19 = 84.2%；`02` 批次 20/29 = 69.0%
- **`run5` 4/34 = 11.8%**——这批是 Hard 为主（20 hard / 13 medium / 1 easy），说明「浅中难稳定、Hard 批量明显掉」
- sqlilabs 69/69 关卡（10 个唯一靶标）、CTFY 练习场 9/9、SAMPLE 6/6

**强**：`http_request/http_map/http_burst/fuzz/authdiff/jsrecon/sqli/jwt_forge/traffic/listen/udp` 全链路；
25 张 web 方法卡 + 21 张 AI 卡；提交纪律好（2h 轮 4 题全部首次 accepted，rejected 极少）。
**弱 / 缺**：① 无浏览器/JS 引擎（纯前端、需执行 JS 才能出结果的题只能读 bundle）；② 无 XSS 真实利用验证
（不能证明弹窗/窃取）；③ 无自动 WAF 绕过库（靠模型手试）；④ 运行期无外网（OOB 只能走平台回调）；
⑤ 复杂多阶段题（SSO/云元数据/鉴权链）容易在 40 步/15 分钟内耗尽；⑥ Hard 批量批次只有 11.8%（run5）。
**结论**：不再投工具，优先修实例与环境链路 + 给 Hard 单题更长时间。

### 3.2 REVERSE —— ✅ 静态强 / ❌ 高难弱
**数据**：CTFY 10/10、SAMPLE 4/4；BUUCTF 8/31（easy 6/21、medium 1/3、hard 1/7，Hard 解出的是 REVERSE2）；2h 轮 Easy 1/1。
**强**：`elfpeek`（info/sections/symbols/strings/disasm/gadgets/bytecode）+ capstone/objdump/readelf +
`hexpeek` + `z3solve` + `pwnrop` + gdb 批处理 + gcc；v8 补了 `uncompyle6/xdis`（旧 pyc 反编译）；
方法卡写明「先魔数判型再选路线」。简单 ELF/PE/脚本/pyc/base64 变形题稳定解出。
**弱 / 缺**：① **无反编译器**（Ghidra/IDA 不装）——长函数靠读汇编，大二进制步数很快耗尽；
② **无符号执行**（angr 未装）——校验器/VM/多分支题只能硬推；③ .NET/Java/APK 专用反编译缺失
（androguard 本机有、镜像未装）；④ 跨架构/需模拟执行的题（MIPS 等）缺 qemu；
⑤ BUUCTF Hard 只解出 1/7，是「能力天花板」而不是时间问题（Hard 题多数需要反编译/符号执行）。
**结论**：Hard 题最大增益是 angr（先量体积，门禁 400MB 目前 ~300MB 量级）。

### 3.3 PWN —— 🔴 最大短板
**数据**：BUUCTF 3/31（9.7%，全部是 Easy 容器题：bbys_tu_2016 / rip / warmup_csaw_2016）；
CTFY 2/6；SAMPLE 1/3；Medium/Hard 0。
**强**：容器/实例链路已证明可用（起实例→nc→拿动态 flag→提交，40~400s 收口）；工具链齐
（pwntools/elfpeek/pwnrop/pwngen/pwnfind/tcp/tcp_batch/gdb/patchelf）。
**弱 / 缺**：① **无堆利用辅助**（tcache/UAF/IO_FILE 全靠模型手写 exp）；② 无 libc 版本库/one_gadget
（泄漏后靠猜偏移）；③ 无本地调试器增强（pwndbg 不装，只有 gdb batch）；④ 无 qemu 跨架构；
⑤ 时间：21/28 未解因 10 分钟墙钟被打断，Medium/Hard 需要 20~40 分钟；
⑥ 部分实例平台侧 502/Target unavailable（环境问题）。
**结论**：先给 PWN 单题更长时间 + libc 小表；堆题短期不追。

### 3.4 CRYPTO —— 🟡 中等偏强（本轮比例最高）
**数据**：BUUCTF 16/30（53.3%，easy 11/20、medium 2/4、hard 3/6）；SAMPLE 0/1；
CTFY 练习场 0/5（复核：1 题预算耗尽 budget_exhausted、3 题协调者收口、1 题结论未过；当时镜像尚无
z3/pycrypt/gmpy2 也有影响，不能全归因「实例/环境」）；历史多题判错（练习场会重签发公开 flag，
dice{...} 类必判错，属机制性而非模型错）。
**强**：RSA 经典攻击（small_e/共模/Wiener/Fermat/pollard/CRT）、AES/ECB/CBC/CTR、PRNG（MT/LCG 恢复）、
`pycrypt`（GCM/CFB/OFB/EAX/3DES）、z3、gmpy2/sympy、`lattice_math`（LLL + 一元 Coppersmith）；
已完成多道 Hard（Blind Signature、HAVE_FUN、Yusa 课后习题）。
**弱 / 缺**：① **无 Sage/cypari2**（明确不装）——二元 Coppersmith、ECC/离散对数、大格题天花板；
② LLL 护栏维数 ≤24、位长 ≤2^17，高维格跑不动；③ beta<1（已知 p 高位）场景明确不支持；
④ 试错多，240 调用/800k token 容易打满；⑤ CTFY 的 crypto 题卡实例可达性（VPN）。
**结论**：保持现状；高难 crypto 靠「模型手写 + 小工具」而不是装 Sage。

### 3.5 MISC / FORENSICS —— 🟡 中（v8/v9 补强后待复验）
**数据**：BUUCTF 9/30（easy 8/20、hard 1/6、medium 0/4）；CTFY misc 7/9、forensics 2/4；SAMPLE 4/4；
历史聚合 misc 5/8。
**强**：编码链 `decode_blob`、解包 `unpack_archive`、`hexpeek`、QR（v8 pillow+zxing/qrcode）、
pcap（v8 scapy+dpkt）、zip AES（pyzipper）、Office 解密/PE/RAR/OCR/国密（v9）、
binwalk/foremost/steghide/exiftool/7z/poppler/john/tesseract；SAMPLE 4/4、CTFY 7/9。
**弱 / 缺**：① **磁盘镜像/文件系统解析**（ext/FAT 分区挂载）缺；② **内存取证**（volatility 类）缺；
③ `zsteg`（PNG LSB）未装；④ **bkcrack**（ZipCrypto 已知明文）缺（2 题卡这）；⑤ 音频频谱工具缺（sox 未装）；
⑥ **大附件平台不给 URL**（53MB/3.7MB 附件题直接废，平台缺陷）；⑦ 平台 `【已停止提交】` 题仍可被选中（白烧）。
**结论**：Medium/Hard 主要卡「取证类深解析 + 大附件」，v8/v9 已补的 QR/pcap/zip/OCR 需在正式镜像上复验。

### 3.6 AI / LLM 安全 —— 🟡 有历史基线（63%），但未进最近一批回归
**数据**：AI 安全题不在 BUUCTF 3244 题里（BUUCTF = web/misc/rev/crypto/pwn）；**CTFARENA 题库以
`category=web` 收录 19 道**（tag: `owasp_2026_asiNN` / `agentic_ai`，含 7 hard、8 medium、若干 easy），
历史 **12/19 解出（63%）**：SupportMind / OrdersBot / TokenRelay / PluginForge / DataScribe / AgentMesh /
AutoRecover / ConfideDesk / AdGuardian / MonoBuild / ModelGateway 等；未解 7 道（SnackBox、TravelMate、
ExpenseDesk、Initech Ops Copilot、PagerPilot、NoteMind、ShadowAgent）。
**强**：对话式越狱/注入、越权、工具滥用类打法已在（21 张 AI 方法卡 + `chat/http_request/http_map` 工具面）。
**弱 / 缺**：① 未进 BUUCTF 122 批，也不是当前代码/镜像的复验结果；② 无多模态；③ 无 RAG/向量库专用工具
（可直接 HTTP 打，暂不需要）。
**结论**：AI 不是「未验证空白」，而是「有 63% 历史基线、最近一批没覆盖」；下次跑批带 5~10 道 AI 题即可补齐。

### 3.7 QA / 选择题 —— ✅ 100%
**数据**：15/15（MMLU-CS/SECQA 类，平均 ~2k token/题，1~2 步收口）。`is_qa_only` 走 QA 通道，稳定。

## 4. 横向短板（所有方向共享）

1. **时间预算与难度不匹配**：122 题里 43 题（35%）wall_clock；2h 轮 4 题全在「第 2~3 个假设」被砍。
   建议：Easy 8~10min、Medium 20~25min、Hard 40min（按实测难度而非平台标签）。
2. **容器/实例是第一约束**：容器题 12.2% vs 静态 41.1%；练习场 1 个实例槽、实例 TTL 实测 3600s、
   `renew_instance` 只在协调者工具面、**没有自动续期逻辑**；残留实例泄漏曾直接废题（已修 P1）。
3. **VPN/内网可达性**：`tcp_access=vpn` 的题本机必不通（无隧道），agent 只能按「接入层不可达」收口；
   平台提供 per-instance `vpn-config` REST（已确认可用），但本地/容器内的隧道尚未打通。
4. **平台标签不可信**：difficulty 非单调（Easy 的 rot 比 Medium 的 newbie_rsa 难）；category 会串
   （CyBench 的 rev/pwn/crypto 全标 web）；选题应按「历史成功率 + 附件/容器特征」而不是标签。
5. **Token 效率**：未解题吞 92% token；重复动作断路器已有（4 次同参/3 次同结果），但「长链推理」
   仍然贵。建议按难度动态分配预算，而不是每题 800k 打满。
6. **镜像能力包尚未在正式镜像复验**：v8（QR/pcap/zip/stego/PDF）与 v9（Office/PE/RAR/OCR/国密）
   都只在本机 + 构建期自检验证过；122 题成绩是 **v8/v9 之前的跑批环境**，实际收益需 CI 构建后复跑。
7. **比赛形态缺口**（非解题能力，但影响得分）：出题能力为 0（G7）、无 VPS 常驻调度器（G10）、
   无 task.json 缺失兜底（G9）、无离线知识库（G8）、无「禁止扫平台/内网」硬护栏（G5/G6）。

## 5. 「缺少什么」清单（按性价比排序）

| # | 缺口 | 卡住什么 | 现状 | 建议 |
|---|---|---|---|---|
| 1 | **bkcrack**（ZipCrypto 已知明文） | MISC「二师兄呢？」类 | ❌ 缺 | v10 多阶段构建编译（几 MB 二进制） |
| 2 | **angr**（符号执行） | REVERSE Hard、校验器/VM | ❌ 缺 | 先量体积（v7.3 251.7MB + v8/v9 新增，距 400MB 门禁需实测） |
| 3 | **磁盘镜像/内存取证**解析 | MISC/FORENSICS Hard | ❌ 缺 | 纯 stdlib FAT/ext 头解析小工具；volatility 体积大，评估 |
| 4 | **libc 数据库/one_gadget** 小表 | PWN ret2libc 偏移 | ❌ 缺 | 纯数据表（几 MB），收益直接 |
| 5 | **qemu-user**（跨架构） | MIPS/ARM pwn、跨架构 rev | ❌ 缺 | 评估体积（~30-60MB） |
| 6 | **zsteg / sox** | PNG LSB、音频隐写 | ❌ 缺 | 小体积，v10 顺手补 |
| 7 | **自动 renew_instance** | 长题实例中途过期 | ⚠️ 只有工具面，无自动逻辑 | 代码层几行（`remaining_seconds<300` 时续期） |
| 8 | **VPN 隧道打通**（本机/容器） | `tcp_access=vpn` 类题 | ⚠️ REST 可取配置，未落地 | 外部连隧道 + 代码前置探测（见 `docs/limits`/playground 文档） |
| 9 | **大附件直取** | 50MB+ 附件题 | ❌ 平台缺陷 | 只能反馈平台；本地走 `_attachments.json` 兜底 |
| 10 | **反编译器（Ghidra/IDA）** | REVERSE 大二进制 | ❌ 明确不做 | 用 elfpeek+脚本替代，接受天花板 |
| 11 | **Sage/cypari2** | 高难 CRYPTO（格/ECC） | ❌ 明确不做（+1GB） | 接受天花板，靠手写 |
| 12 | **浏览器 JS 引擎** | 纯前端题 | ❌ 明确不做 | 接受，读 bundle 替代 |

## 6. 结论（一句话）

**WEB / QA / REVERSE(静态) / CRYPTO(经典) 是强项（AI 安全题有 63% 历史基线，但没进最近一批覆盖）；
PWN 与「容器依赖 + 取证深解析 + 高难 REVERSE」是短板；当前 29.5% 的整体解出率里，约一半失败是
环境/时间（实例、VPN、墙钟、平台缺陷），另一半才是工具/模型能力。** 最划算的下一步不是再加通用工具，
而是：① 打通实例/VPN 链路 + 自动续期；② 按难度动态分配时间；③ 补 bkcrack/angr/libc 表这三样高杠杆
小件；④ 用 v8/v9 正式镜像复跑 122 题（并带 5~10 道 AI 题）验证收益。
