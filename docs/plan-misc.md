# Misc 方向能力补全：范式调研与实施草案

> 结论先行：Misc 的「优秀范式」在社区有两本公认的「范式书」——**CTF Wiki 杂项卷**（中文社区标准分类法）与 **Trail of Bits CTF Field Guide / Forensics**（西方标准工具链与思路）。
> 两者 + 我们自身约束（纯 stdlib、极裸镜像、10 步预算）收敛出一条主范式：**「一步多查 → 识别 → 剥壳 → 递归」**。
> 我们已有的工具基因（decode_blob / unpack_archive / hexpeek）恰好是这条链的前两层——**misc 补全 ≠ 另起炉灶，= 补齐「识别」与各格式的「解剖器」**。

---

## 1. 调研源与现状盘点

### 1.1 两大权威范式源

| 源 | 定位 | 拿到什么 |
|---|---|---|
| [CTF Wiki 杂项卷](https://ctf-wiki.org/misc/introduction/) | 中文社区标准**分类法** | 完整子类树：信息搜集 / 编码分析 / 取证隐写前置 / 图片分析(PNG/JPG/GIF) / 音频隐写 / 流量包分析(PCAP修复·协议·提取) / 压缩包(ZIP/RAR) / 磁盘内存 / Other(pyc) |
| [Trail of Bits CTF Field Guide — Forensics](https://trailofbits.github.io/ctf/forensics/) | 西方标准**思路与工具链** | 每个子类的问题本质、标准工具、检查清单（详见 §3 各行） |

ToB 指南的关键判断（直接支撑我们的投资方向）：
> 取证/misc 三大核心能力 = **脚本语言 + 二进制字节操作 + 格式/协议/结构/编码的识别**。
> 对 LLM agent：格式识别是模型原生强项（训练语料里全都有），脚本能力已有 shell+python 工具——
> **真正的短板是「确定性二进制操作工具」**。这就是 misc 补全的全部重心。

### 1.2 misc 现状（agent.json 实配，非 DEFAULT_ROLES 兜底）

- **已有工具**（`local`）：http_request / http_map / shell / **decode_blob / unpack_archive / hexpeek** / tcp / tcp_batch / netcheck；`allow_shell: true`，`max_steps: 10`。
- **已有提示词 SOP**：取材 → 判型(头字节+hexpeek) → **强制全量明文扫**（grep flag 正则）→ 多层编码(decode_blob，≤5 层) → 归档与嵌套 → …（已相当完整，补全是**增量**不是重写）。
- **镜像现实**（Dockerfile 实证）：python:3.12-slim + curl/grep/coreutils/bash，**没有** file / strings / xxd / binwalk / exiftool / zsteg / tshark / PIL；运行期无网络；`src/` 只允许 stdlib；镜像大小受限。
- **结论**：判型靠提示词里背的魔数表 + python 一行流；图片/音频/流量/文档四类**零专用工具**，全靠 shell 里手写 python——每类动辄烧 3-5 步，10 步预算根本不够。

---

## 2. 收敛出的优秀范式（7 条，附架构映射）

### 范式 0（我们自己的核心设计准则）：一步多查（Composite Scan）
人类工具是「原语」（zsteg 只做一件事，人脑串流程）；**LLM agent 的工具必须是「体检报告」**——一次调用跑完该格式的整套廉价检查清单，返回结构化判定（结构表 + 异常点 + 隐写快扫 + 建议），让模型一步决策。
理由：misc 角色只有 10 步。一个 `pngtool` 一次返回「chunk 表/CRC 校验/IEND 后附加数据/LSB 快扫/元数据」，等价于人用 exiftool+pngcheck+zsteg+hexdump 四步——**工具做并集，模型做交集**。这是 agent 工具与人类工具的本质差异，也是本项目 misc 补全的第一设计原则。

### 范式 1：套娃模型（识别 → 剥壳 → 递归）
ToB: "mastroshka-like files-within-files"；CTF Wiki 归档与嵌套章。
Misc 题本质是一个**层栈**：编码层/容器层/格式层/隐写层。解法 = 识别顶层 → 用对的剥壳器剥掉 → 对露出的工件递归，带**工作清单**与**回路保护**（同层不重复剥、深度上限）。
架构映射：LLM=调度器（选下一个剥壳器）；本地工具=确定性剥壳器；Ledger=同参数去重（已有）；note=工件登记；report=交付（已有）。**缺的环节：`carve`（按魔数在任意偏移抠出内嵌文件）**——unpack_archive 只认「整文件就是容器」，不认「PNG 尾巴上贴了个 zip」。

### 范式 2：先扫后解，成本阶梯（cheap-first）
ToB: strings/grep/hexdump 是初始分析四件套；我们提示词已强制「全量明文扫先行」。
推广为普适纪律：**每个新工具都必须先给「廉价扫描模式」回答「这里面有没有东西」，再谈深度解析**。命中即停，绝不解包。

### 范式 3：格式解剖而非黑盒（chunk-level parsing & repair）
ToB: pngcheck / zipdetails / zip -FF 修复 / qpdf / 010 Editor 模板 / corkami 图解。
CTF 高频题型是**故意弄坏的结构**（PNG 尺寸被改、CRC 错、zip 中央目录烂、IEND 后藏数据）——黑盒工具直接打不开，解剖器才能看+修。stdlib 对 PNG(chunk)/ZIP(EOCD+CD)/GIF(block)/JPEG(marker) 全部可实现。

### 范式 4：隐写 = 按固定优先级枚举（metadata → LSB → 位面 → 工具族）
ToB: exiftool 永远第一；然后 LSB（zsteg/stegsolve）；难点是「猜中嵌入工具」。
agent 化改造：不猜工具，**按确定性优先级把可枚举面全扫一遍**（注释/EXIF/尾附数据 → 全通道 LSB → 调色板/帧延迟），每项给「命中/未命中」判定。stdlib 可覆盖多数派（LSB + 注释 + 尾附）。

### 范式 5：流量 = 元数据分流 → 对象导出 → 协议专项
ToB: capinfos/会话统计 → tshark 过滤 → Export Objects → packet carving（xplico/networkminer）。
CTF Wiki 专项章：HTTP/HTTPS/FTP/DNS/WiFi/**USB**（TPCTF 高频：USB 键盘 HID 还原 / 鼠标轨迹画 flag）。
stdlib 可实现：pcap/pcapng 解析、会话统计、HTTP 对象导出、USB HID 键码表还原（固定码表，天然适合确定性工具）。

### 范式 6：重工具域降维（内存/磁盘 = cheap-scan + carve）
ToB: volatility/sleuthkit 是重炮，stdlib 复刻不了。
降维策略：CTF 内存盘镜像里的「针」绝大多是**可打印串/内嵌文件**——deepstrings + carve + flag 正则扫即可吃下多数题，别试图复刻 volatility。

### 范式 7（已有，保持）：交付纪律
report/evidence/账本去重/先扫后解已内建——新工具的输出格式必须喂给这套纪律（返回带偏移的原文片段，evidence 可直接复制）。

---

## 3. 子类 × 范式 × stdlib 落地性对照

| 子类 | 社区标准工具（参考） | 我们镜像现实 | stdlib 落地 | 难度 | 优先级 |
|---|---|---|---|---|---|
| 编码剥壳 | CyberChef、base64 | decode_blob ✅ 已有 | — | — | — |
| 容器解包 | binwalk、unzip | unpack_archive ✅ 已有 | — | — | — |
| **识别** | file/libmagic、TrID | **无 file** | `fileid`：偏移魔数签名库（60+ 格式） | 低 | **P0** |
| 明文扫 | strings/grep | 有 grep，**无 strings** | `deepstrings`：可打印串+偏移+UTF16+自定义正则 | 低 | **P0** |
| 嵌套雕刻 | binwalk/dd/scalpel | 无 binwalk | `carve`：多签名扫描+按偏移抠出 | 低-中 | **P0** |
| **PNG** | pngcheck、pngtools | 无 | `pngtool`：chunk 表+CRC 校验/修复+IHDR 尺寸修复+IEND 后附加+IDAT 解压快扫 | 中 | **P1** |
| 图片隐写 | zsteg、stegsolve、exiftool | 无 | 并入 `pngtool`/`imgmeta`：全通道 LSB 快扫+EXIF/COM 注释读取 | 中 | P1 |
| GIF/JPEG | exiftool、Wireshark 媒体解析 | 无 | `giftool`(帧/延迟/注释/尾附)、JPEG marker 走 `imgmeta` | 中 | P1 |
| **ZIP 解剖** | zipdetails、zip -FF、fcrackzip | 无 unzip（stdlib zipfile 可解） | `zipinsight`：加密位/压缩比异常/注释/额外字段/明文攻击线索（已知明文+CRC 双判据） | 中 | **P1** |
| **音频** | Audacity、SoX、multimon-ng、Sonic Visualiser | 无 | `wavtool`(stdlib wave)：PCM LSB+倒放+摩斯包络+DTMF(Goertzel 8 频点)+粗文本频谱 | 中-高 | P2 |
| **流量** | tshark、NetworkMiner、pcapfix | 无 tshark | `pcaptool`：解析/会话统计/HTTP 对象导出/USB HID 键盘鼠标还原/DNS 清单 | 高 | **P2** |
| 文档 | peepdf、oletools、OfficeDissector | 无 | `docscan`：OOXML=zip+XML（极易）；PDF 对象/注释/增量更新扫描（中） | 中 | P3 |
| 磁盘/内存 | SleuthKit、Volatility | 无 | 降维：deepstrings+carve+flag 扫（范式 6） | 低 | P3 |
| QR | qrtools、zbar | 无 | 纯 python 解码需 Reed-Solomon，性价比低 | 高 | 缓议 |
| 信息搜集/OSINT | — | 运行期无网络 | 赛制相关，平台 MCP 外不可达 | — | 不做 |

---

## 4. 工具补全清单（分期）

全部走 [extending-agent.md](extending-agent.md) §5.2 **Recipe A（纯函数扩展工具）**：新文件 `tools_<域>.py`，纯函数+SPEC+注册 `MISC_EXT_SPECS`/`_IMPL`+薄适配，角色 `local` 名单挂名。输出上限 6000、路径锚定沙盒、evidence 友好（带偏移原文）。

- **P0（识别三件套，性价比最高）**：`fileid` / `deepstrings` / `carve`
  - 一步多查样例：`fileid(path)` 返回「魔数判定 + 可打印率 + 尾部附加数据检测 + 内嵌签名提示 + 建议下一步」，一次调用顶现在的 hexpeek+python 判型+grep 三步。
- **P1（高频格式解剖）**：`pngtool` / `zipinsight` / `giftool` / `imgmeta`(JPEG/EXIF/BMP)
- **P2（特色赛道）**：`wavtool` / `pcaptool`（USB HID 键码表是确定性收益，TPCTF 风格）
- **P3（文档与降维）**：`docscan`；磁盘/内存走 P0 工具组合拳

**提示词增量**（在现有 SOP 上加，不重写）：每个新工具在对应阶段插一行「优先调 X 工具（一次返回…），不可用再手写 python」；步数预算重排（P0 落地后判型可压到 1 步，省出 2 步给深解析）。

**验收**：按 [extending-agent.md](extending-agent.md) §9 DOB——每个工具配独立单测（纯函数可直接 import 测），smoke 全绿，misc 角色 local 名单挂名后 `_role_specs` 过滤断言。

---

## 5. 参考源

- [CTF Wiki 杂项卷](https://ctf-wiki.org/misc/introduction/)（分类法；编码/图片/音频/流量/压缩/磁盘内存各章）
- [Trail of Bits CTF Field Guide — Forensics](https://trailofbits.github.io/ctf/forensics/)（取证思路与工具链：file/carving/strings/编码/图像/文件系统/PCAP/内存/PDF/音视频/Office）
