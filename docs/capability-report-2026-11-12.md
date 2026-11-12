# Agent 能力评估报告（2026-11-12）

> 口径：本报告只陈述**可核对的事实**（平台题库状态文件 + 代码里的工具面/预算/契约），
> 推断处显式标注「推断」。所有数字都可按 §2 的路径复算。

## 1. 结论摘要

| 题型 | 判定 | 一句话 |
|---|---|---|
| **WEB** | ✅ 强 | 工具面最完整（请求/地图/爆破/鉴权对照/JS 侦察/SQLi/JWT/流量检索/回调监听），自建题 9/9 |
| **REVERSE** | ✅ 强 | 静态全链路（elfpeek+capstone+objdump+z3solve），真实外部题 8/8 |
| **MISC / FORENSICS** | 🟡 中 | 编码/解包/隐写基本功扎实，真实外部题 5/9；磁盘镜像、内存取证、pcap 深解析是空白 |
| **PWN** | 🔴 短板 | 真实外部题 **0/4**；工具链（pwntools/ROP）刚补齐、堆利用与调试能力弱，且受实例可达性拖累 |
| **CRYPTO** | 🔴 短板 | 真实外部题 **0/5**；RSA/分组/PRNG/z3/pycrypt 已就位但**未复验**，格攻击/ECC 空白 |
| **AI** | ⚪ 未验证 | 角色与 `chat`/`http_request` 工具在，但题库里没有 AI 题样本，能力未测 |

**根因三分**（不要混为一谈）：
1. **工具链缺口**（已修，未复验）——v6 镜像里没有 pwntools/pycryptodome/gmpy2/sympy/z3；
2. **验证数据污染**——外部公开题（DiceCTF 等）在练习场会**重签发 flag**，公开仓库里的 flag 必被判错，
   「解出但交不上」不等于能力差；
3. **环境/预算限制**——实例 `access_ready=false`、每题 5 次 start 上限、160–240 次模型调用上限。

## 2. 数据来源与口径

| 项 | 值 |
|---|---|
| 题库状态 | `runs/plat/state.json`（本地 gitignored，43 题） |
| 自建验证银行 | `SAMPLE-*` 共 **17 题**（web 9 / misc 4 / reverse 2 / pwn 2），**17/17 已提交** |
| 外部真实题库 | 26 题（DiceCTF 2024/2025 等公开题）：REVERSE 8、PWN 4、CRYPTO 5、FORENSICS 4、MISC 5，**13/26 已提交** |
| 数据时点 | 2026-11-10（当时镜像 v6.x：无 pwntools/pycryptodome/gmpy2/sympy/z3 内置） |
| 未复验项 | v7/v7.2 镜像与 `z3solve/pycrypt/pwnrop` 工具接入（2026-11-12）**都发生在这批数据之后** |

⚠️ 三个口径提醒：
- SAMPLE 银行是**自建低难度基线题**，SAMPLE 17/17 只说明链路通，不代表正赛难度；
- 外部题的 `rejected_answers` 里出现 `dice{...}` 这类公开 flag——练习场判错是**机制性**的，不是模型错；
- PWN/CRYPTO 的 0 分里同时叠加了「无库」「实例不可达」「预算耗尽」，单一归因会误判。

## 3. 角色与工具面（当前 `config/agent.json`）

| 角色 | 本地工具（实际派发清单） | 平台工具 | max_steps | 备注 |
|---|---|---|---|---|
| coordinator | —（只有 note/report 特殊工具） | read / instance / oracle / submit | 40 | 只做路由与提交，不自己解题 |
| web | http_request http_map http_burst fuzz authdiff jsrecon sqli jwt_forge decode_blob shell write_file listen (+traffic 默认) | get_challenge 及附件/提示/实例/verify 共 10 个 | 40 | 工具面最全 |
| ai | http_request http_map chat shell write_file decode_blob | 同 web 10 个 | 40 | `chat` 专治 LLM 应用题 |
| rev | shell write_file elfpeek hexpeek tcp tcp_batch netcheck listen z3solve | read / instance / oracle | 40 | 反汇编走 capstone/objdump；gdb 见 §9 |
| crypto | shell write_file hexpeek rsatool aesblock prng tcp tcp_batch netcheck listen z3solve pycrypt | read / instance / oracle | 40 | 大整数走 gmpy2；格攻击走 rsatool 的 lll/coppersmith（§9） |
| pwn | shell write_file elfpeek hexpeek pwngen pwnfind tcp tcp_batch netcheck listen z3solve pwnrop | read / instance / oracle | 40 | pwntools 走 pwnrop + shell 里的 `import pwn` |
| misc | http_request http_map shell write_file decode_blob unpack_archive hexpeek tcp tcp_batch netcheck listen z3solve | read / instance | 40 | 无 oracle/submit 权（结论交协调者） |

> 更正（2026-11-12 复核）：本表此前写「12 上限」——**代码里没有硬校验**，12 只是历史上各角色
> 派发数的经验值（crypto/pwn/misc 当前恰好 12 个，web 12 个）。真正影响成本的是「每步工具调用 ≤8」
> 与模型自身的选择面，加工具时不需要改任何门禁代码。

平台侧另有全局工具（`list_instances`/`stop_instance`/`renew_instance`/`get_scoreboard`…）只对 host 侧
CLI 开放，**不给模型**（防误操作烧配额）。已核对接线无孤岛：实现了的 30 个本地工具里 27 个按角色派发，
`note/report/traffic` 为特殊通道（前两个恒可用，`traffic` 只给 web 默认）。

## 4. 分题型详评

### 4.1 WEB（强）
- **能做**：目录/隐藏参数发现（`fuzz`，内置软 404 校准）、站点结构+表单+JS 接口（`http_map`）、
  批量/竞态（`http_burst`）、鉴权对照四变体（`authdiff`）、JWT 伪造（`jwt_forge`，含 RS256→HS256 混淆与
  `alg=none`）、SQLi 六类技术（`sqli`）、前端 bundle 静态侦察（`jsrecon`）、全量流量检索（`traffic`）、
  回连接收（`listen`）、原始请求窗口翻页（`http_request`，默认 5000 字符窗口）。
- **数据**：SAMPLE 9/9。
- **做不到 / 缺口**：① 无浏览器/JS 引擎——纯前端或需执行 JS 才能出结果的题只能靠读 bundle；
  ② 无 XSS 实际利用验证（无法证明弹窗/窃取动作）；③ 运行期无外网（只能打本题实例，DNS/OOB 外带受限）；
  ④ 无自动 WAF 绕过策略库（靠模型逐次手试）。
- **建议**：不加引擎（体积/合规风险），补「JS 静态执行」小工具（用 Python 跑简单表达式）优先级低。

### 4.2 REVERSE（强）
- **能做**：`elfpeek`（info/sections/symbols/strings/disasm/gadgets/bytecode，capstone 反汇编，
  支持 .pyc/PyInstaller PYZ 提取）、`hexpeek` 字节级检查、`z3solve` 解 flag 校验约束、
  `shell` 里跑自写脚本（镜像有 gcc/binutils/python，可编译重放算法）。
- **数据**：真实外部题 8/8（time-travel、not-baby-parallelism、parallelism、ExceLLM、RomanRomance、
  PalatinePack、missioncritical1、PlutoChat 全提交）。
- **做不到 / 缺口**：① 无 gdb 动态调试（只能静态读 + 自写脚本模拟）；② 无 Ghidra/IDA 反编译
  （长函数靠模型读汇编，大二进制容易步数耗尽）；③ 无 .NET/Java/APK 专用反编译工具（题目碰到就吃力）。
- **建议**：REV 是当前最稳的方向，不建议再投入；若正赛出现 .NET 题再加工具。

### 4.3 PWN（短板）
- **能做**：`elfpeek`/`pwnrop` 做 checksec/plt-got/gadget/ROP 链（pwntools ROP，v7 镜像内置）、
  `pwngen`+`pwnfind` 做 cyclic 偏移、`tcp`/`tcp_batch` 与服务往返与批量盲打、`listen` 接回连、
  `shell` 可 `import pwn` 写完整 exp（镜像有 gcc 可编译）。
- **数据**：真实外部题 **0/4**（unipickle、IRS、Pike、diligent-auditor）；SAMPLE 2/2 只是 nc 签到级。
- **做不到 / 缺口**：
  ① **实例可达性**——`unipickle` 实测 instance 恒为 `access_ready=false`，5 次 start 配额烧完后整题作废
     （环境问题，非能力）；② 无 gdb/pwndbg，无法本地调二进制（只能静态+盲打）；
  ③ 无 libc 版本库/one_gadget，泄漏后靠人工猜偏移；④ 无 patchelf，库版本不匹配时改不了 ELF；
  ⑤ **堆利用（tcache/UAF/IO_FILE）几乎全靠模型手写 exp**，无堆风水辅助；
  ⑥ 每步 120s shell 超时、40 步上限，长交互题容易断。
- **建议**（性价比排序）：① 实例可达性纳入开题前置检查（已有 `instance_absent` 负缓存，扩大覆盖面）；
  ② 加 libc 版本识别（泄漏地址+已知偏移反查，可用内置 libc 数据库小表）；③ 堆利用先不碰。

### 4.4 CRYPTO（短板）
- **能做**：`rsatool`（small_e / common_modulus / wiener / fermat / pollard_pm1 / pollard_rho / 共模…）、
  `aesblock`（纯 stdlib ECB/CBC/CTR/XOR）、`pycrypt`（AES-GCM/CFB/OFB/EAX、3DES，v7 起可用）、
  `prng`（MT 状态恢复/预测、LCG 恢复）、`z3solve`（模方程/位向量约束）、`tcp_batch`（padding oracle 盲打）。
- **数据**：真实外部题 **0/5**（winter、satisfied、winxy-pistol、vorpal-sword、spn）。
- **归因**（重要）：这批数据里 5 题全部停在「工具链/flag 有效性」层，不是数学思路层——
  `winter`/`satisfied`/`winxy-pistol` 的 notes 显示**公开 flag 已拿到却被平台判错**（练习场重签发），
  另有 `budget_exhausted`（160 次调用打满）。工具链缺口（无 z3/pycryptodome/gmpy2）已由 v7 镜像 + 11-12 补丁关闭。
- **做不到 / 缺口**：① **格攻击（LLL/Coppersmith）无工具**——rsatool 只覆盖经典攻击，HNP/小根问题做不了；
  ② 无 ECC/离散对数专用工具（靠 shell 手写）；③ 无 SageMath（体积 500MB+，明确不装）；
  ④ 预算：CRYPTO 题试错多，160/240 次调用 + 800k tokens 容易打满。
- **建议**：① 先做 §7 的复验（z3/pycrypt 接入后 CRYPTO 能不能从 0 起）；② 若复验有效，再考虑
  手写 Coppersmith（纯 Python 小整数版，复杂度可控）作为唯一新增；③ Sage 明确不做。

### 4.5 MISC / FORENSICS（中）
- **能做**：`decode_blob`（多层编码链自动剥离）、`unpack_archive`（按魔数识别 zip/tar/gz/bz2/xz，
  Zip Slip 与解压炸弹防护）、`hexpeek`、`write_file`/`shell` 写脚本处理任意格式、`listen`（OOB）、
  `tcp_batch`、`http_map`。SAMPLE 4/4、外部 5/9。
- **做不到 / 缺口**：① 无磁盘镜像解析（分区表/文件系统挂载，misc 里 `TranscendentRenovation` 类题就卡这）；
  ② 无内存取证（volatility 类，镜像/内存 dump 题做不了）；③ 无 pcap 深解析（只有 shell 手写 struct 解析）；
  ④ 无图片/音频隐写工具（zsteg/steghide/sox 类，只能手写 LSB/频谱脚本）；⑤ 无 PDF/Office 结构化解析。
- **建议**：① 加「分区/文件系统只读解析」小工具（纯 stdlib 解析 FAT/ext 头部足够应付多数题）；
  ② pcap 加一个只读解析器（纯 stdlib 解 pcap/pcapng，提取 HTTP/TCP 流）——两项都比装大工具便宜。

### 4.6 AI（未验证）
- **能做**：`chat`（多轮对话靶子，含上下文管理与引文引用技巧）、`http_request`/`http_map`
  （打 LLM 应用接口）、`decode_blob`。
- **缺口**：题库无 AI 样本，能力未测；无 RAG/向量库题工具（可直接 HTTP 打）；无多模态。
- **建议**：等有 AI 题样本再评估，不提前投入。

## 5. 横向硬约束（所有题型共享）

| 约束 | 值 | 影响 |
|---|---|---|
| 每角色步数 | `max_steps=40` | 长题容易耗尽步数（协调者同 40） |
| 每步工具调用 | `max_calls_per_step=8` | 一次可并行 8 个探针 |
| 单题模型调用 | `max_llm_calls=240`（实测曾有 160 口径） | CRYPTO/PWN 试错多，易打满 |
| 单题 tokens | `max_tokens_per_challenge=800000` | 同上 |
| oracle 调用 | `oracle_limit=100` | judge 类题有上限 |
| 提交 | 本地 `submit_limit=3`/题号 + 平台 `platform_submit_limit=5` 轮，attempt 上限 `limit*16` | 试错空间有限 |
| 实例 | 每题 `instance_ops_limit=5` 次 start；团队 1 槽位；ctf2 无 `list_instances` | 实例泄漏/不可达直接毁题 |
| 连接 | `connect_timeout=40s`，`connect_deadline=5400s` | 平台故障窗要熬 60–90 min |
| shell | 单次 120s 超时；**命令无白名单**（角色 `allow_shell=true` 即任意命令，隔离靠容器） | 长跑脚本要拆；别拿 shell 干网络扫描 |
| 网络 | 运行期无外网（只能打本题实例） | 不能上网抄题解/取附件（除非平台给） |
| 平台契约 | v3：预下发实例、单题 token、每轮重写 result.json、最多 5 轮 | 见 `HANDOFF-2026-11-10-r2 §3~§5` |

## 6. 当前镜像能力（v7.3：v7.2 + 2026-11-12 补丁 + PWN/CRYPTO 短板补齐）

- 基础：`python:3.12-slim` + bash/curl/grep/coreutils + gcc/libc6-dev/binutils（`as/objdump/readelf` 构建期断言）
  + **gdb / patchelf（v7.3 补，构建期 `gdb --version` / `patchelf --version` 断言）**。
- 内置库：capstone 5.0.3、pwntools 4.15.0、pycryptodome 4.4.0、gmpy2 2.3.2、sympy 1.14.0、
  **z3-solver 5.4.1.0**、**格攻击（LLL + 一元 Coppersmith）为项目内纯 stdlib 模块 `lattice_math.py`（v7.3 新增）**。
  ffuf 构建期烤入。
- 明确不含：Sage、Ghidra/IDA、volatility、浏览器内核、pwndbg（只用 gdb 批处理）、ROPgadget（用 `pwnrop`）；
  GUI 与多平台适配器（见 runbook 红线）。
- 体积：v7.2 实测 197.7 MB；补 z3 后约 220 MB；v7.3 再补 gdb/patchelf 约 **230~240 MB**（门禁 400 MB）。

## 7. 缺口优先级与处理状态

| # | 缺口 | 影响 | 状态 | 建议 |
|---|---|---|---|---|
| 1 | PWN/CRYPTO 真实题 0 分**未复验** | 最大不确定性：不知道 libs 接入是否真提升 | ⏳ 待批（烧配额） | 各 1 题复验，见 §8 |
| 2 | v7.2 漏装 z3-solver | `z3solve` 在官方镜像恒失败 | ✅ **已修**（Dockerfile，待 v7.3 重建） | 重建时看构建日志 `z3 selftest ok` |
| 3 | 实例可达性（access_ready=false） | 配额烧光、整题作废 | 🟡 部分（负缓存已做） | 开题前置探测 + 早退 |
| 4 | 格攻击（LLL/Coppersmith） | CRYPTO 高难度题天花板 | ✅ **已修**（`lattice_math.py` + `rsatool` op=lll/coppersmith，v7.3 待重建验证） | 见 §9；二元/ECC 仍靠 shell 手写 |
| 5 | 磁盘镜像 / 内存取证 | FORENSICS 中高难度题做不了 | ❌ 未做 | 加只读解析小工具（纯 stdlib） |
| 6 | 无 gdb / 无 libc 版本库 | PWN 调试与偏移推断 | 🟡 **gdb/patchelf 已装**（v7.3）；libc 版本库仍无 | gdb 走 `-batch` 批处理；libc 小表待做 |
| 7 | 无浏览器 JS 引擎 | 纯前端题受限 | ❌ 未做（有意取舍） | 不加 |
| 8 | Sage | 格/ECC 终极解 | ❌ 明确不做 | 体积 500MB+ |

## 8. 下一步验证方案（待用户批）

**目标**：回答唯一关键问题——「libs 接入（pwntools/pycryptodome/gmpy2/sympy/z3）+ 11-12 的 P2 修复，
是否把 PWN/CRYPTO 从 0 分抬起来」。

**方案**：同一题库里 PWN、CRYPTO **各挑 1 题**（优先选有附件 + 实例可达的），跑完整解题轮：

| 项 | 估计 |
|---|---|
| 题量 | 2 题（PWN 1 + CRYPTO 1） |
| 单题墙钟 | 40–45 min（含实例启动/退避） |
| 显式成本 | 每题 ≤5 次实例 start、≤5 轮提交、≤240 次模型调用、≤800k tokens |
| 判据 | ① 是否用上新库（transcript 出现 z3solve/pycrypt/pwnrop 成功）；② 是否有真实 flag 被平台判对；③ 卡点是否仍是环境/预算而非能力 |
| 前提 | 任务和实例可达性先手工确认（避免再踩 unipickle 那种空实例） |

**风险提示**：外部公开题的 flag 在练习场会被判错，因此判据必须包含「平台是否重签发 flag」这一前置确认；
否则 0 分结论不可用。若两题都是环境/flag 机制问题，复验无意义，需改用 SAMPLE 银行加难度的自建题。
## 9. v7.3 增补：PWN/CRYPTO 短板补齐（2026-11-12 晚）

用户在复验前拍板「先补能力短板再重建」，本节记录这一轮实际做了什么、边界在哪、怎么验证。

### 9.1 CRYPTO：格攻击（LLL + 一元 Coppersmith）

| 项 | 内容 |
|---|---|
| 位置 | `src/ctfy_agent/lattice_math.py`（纯 stdlib，无第三方依赖）；接线在 `tools_domain_ext.rsatool` 的 `op=lll` / `op=coppersmith` |
| 为什么塞进 rsatool | crypto 角色本地工具已是 12 个（crypto=12/pwn=12/misc=12），加第 13 个就要动 config 与派发；格攻击本质是 RSA 攻击的延伸，做成 op 最省 |
| LLL | δ-LLL（默认 3/4），Decimal 80 位有效数字做 Gram-Schmidt（第一版 Fraction 精确版在 11 维/2000 位元素上要 5 分钟，不可用）；维数 ≤24、单元素位长 ≤2^17 的护栏 |
| Coppersmith | Howgrave-Graham 一元构造（N^{m-i}·x^j·f(x)^i + x^k·f(x)^m），自动在 (精度, m, t) 小阶梯上试，命中即停，总预算 12 s |
| 实测可靠区间 | deg=2、N 511 bit 时根位长 58 bit 稳定命中（0.2~1 s）；根上界取到 2^120 可解、2^200 不可解。**默认 X=N^(1/2δ)** 是保守值 |
| 明确不支持 | ① **beta<1**（根模 N 的未知因子，如「已知 p 高位」）——该场景需 m≈25+ 维才到理论界，秒级工具跑不动，直接报错并给出替代打法；② 二元 Coppersmith（boneh-durfee/部分密钥）；③ ECC/离散对数 |
| 求根 | 先用短向量两两 gcd 榨线性因子（纯 stdlib）；有 sympy（镜像内置）时用 `ground_roots` 兜底高次 gcd 情形 |
| 回归 | `tests/smoke_lattice.py`（30 项）：LLL 行列式/整数组合/Lovász/范数上界/非法输入 + Coppersmith 正例/负例/非首一/beta 拒绝 + 工具层接线 |

### 9.2 PWN：gdb + patchelf

| 项 | 内容 |
|---|---|
| 改动 | `Dockerfile` apt 行加 `gdb patchelf`，并在同一层做 `gdb --version` / `patchelf --version` 断言（风格同 as/objdump/readelf 断言） |
| 用法 | 方法卡写明批处理式：`gdb -batch -q -ex run -ex 'info registers' -ex 'x/8gx $rsp' -ex bt ./poc < payload.bin`——容器里不需要 pwndbg/交互式 |
| patchelf | ret2libc 题把题目自带 libc 的 interpreter/rpath 改成本机 loader 才能本地复现 |
| 体积 | apt 层约 +25 MB，估算总镜像 230~240 MB（门禁 400 MB），构建日志会打实际大小 |

### 9.3 本篇报告的自我更正（复核 2026-11-12）

1. §3 工具表：web 角色漏了 `listen`；「12 上限」实际**无代码校验**，只是经验值（已加注）。
2. §5：「shell 白名单命令」**错误**——shell 是 `shell=True` 任意命令，隔离靠容器；已更正。
3. §4.3 缺口③④「无 libc 版本库/无 patchelf」：patchelf 已在 v7.3 装上，libc 版本库仍未做。
4. §6 镜像清单：v7.2 曾漏 z3、v7.3 补 gdb/patchelf，均已按「构建期断言」方式固化，避免再次静默漏装。

### 9.4 仍未验证的部分（等用户批测试）

格攻击与 gdb 都只是**工具可用**，没有任何真实题目的效果数据；PWN/CRYPTO 仍停在外部题 0/4 与 0/5 的旧口径。
按 §8 的方案各挑 1 题复验（40–45 min/题、需用户批配额），并优先确认「平台是否重签发 flag」这一前置问题。
