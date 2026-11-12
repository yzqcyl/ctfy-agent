# Misc 方向能力补全：范式调研 × 改进方案（整合版）

> 一个文件说清三件事：**① 社区优秀范式长什么样（含来源内容汇编）；② 对照我们的约束缺口在哪；③ 补什么、怎么补、怎么验**。
> 本文自包含——开发时不需再回外网查资料；外源内容已在 §2 摘编消化，均标注出处。

---

## 0. TL;DR

- 社区两本公认「范式书」：**CTF Wiki 杂项卷**（中文标准分类法）+ **Trail of Bits CTF Field Guide / Forensics**（西方标准思路与工具链）。
- 共同收敛出的主范式：**「一步多查 → 识别 → 剥壳 → 递归」**（套娃模型，见 §3 范式 0/1）。
- 我们已有的 `decode_blob / unpack_archive / hexpeek` 恰好是「剥壳」两层的实现——**misc 补全 ≠ 另起炉灶，= 补齐「识别」与各格式的「解剖器」**。
- ToB 核心判断：取证三大能力 = 脚本 + 字节操作 + 格式识别。LLM 原生会格式识别，脚本有 shell，**唯一短板是确定性二进制工具**——这就是全部投资方向。
- 镜像现实（Dockerfile 实证）：无 `file/strings/xxd/binwalk/exiftool/zsteg/tshark/PIL`，运行期无网络，`src/` 只许 stdlib → 一切新能力必须是**纯 stdlib 本地工具**（extending-agent.md Recipe A 路线）。

---

## 1. 信息来源清单

| # | 源 | 定位 | 原文 |
|---|---|---|---|
| S1 | CTF Wiki 杂项卷 | 中文社区标准**分类法**（子类树 = 解题路线图） | <https://ctf-wiki.org/misc/introduction/> |
| S2 | Trail of Bits CTF Field Guide — Forensics | 西方标准**思路与工具链**（每类问题的本质/工具/检查点） | <https://trailofbits.github.io/ctf/forensics/> |
| S3 | 本仓库 Dockerfile | 镜像能力边界实证 | `Dockerfile`（apt 仅 curl/grep/coreutils/bash；ffuf+SecLists+sqlmap 为 web 烤入） |
| S4 | 本仓库现有工具 | misc 基因盘点 | `tools_misc_ext.py`（decode/unpack/hexpeek/jwt）、`tools_domain_ext.py`（tcp/elfpeek/netcheck）、`config/agent.json` roles.misc |

---

## 2. 来源内容汇编（Digest）

### 2.1 S1 分类法：Misc 子类树（解题路线图）

```
信息搜集技术            （赛制相关；我们运行期无网 → 平台 MCP 外不可达，不做）
编码分析                （通信编码/计算机编码/现实编码）
取证隐写前置技术        （前置：文件结构/进制/编码常识）
图片分析                （PNG / JPG / GIF）
音频隐写
流量包分析              （PCAP 修复；协议：Wireshark/HTTP/HTTPS/FTP/DNS/WiFi/USB；数据提取）
压缩包分析              （ZIP / RAR）
磁盘内存分析
Other                   （pyc 文件）
```

要点：USB 协议在 S1 有专章（键盘 HID 还原是经典题型）；PCAP **修复**独立成节（坏包是常客）。

### 2.2 S2 逐类范式摘编（思路 / 标准工具 / 对我们的启发）

**① 文件识别（magic bytes）**
- 启发式 `libmagic`（即 `file`）+ 更全的 `TrID`（带置信度、专有格式多）。
- ⚠️ 启发式可被故意欺骗；容器类文件只报外层类型——「识别」永远只是起点。
- → 我们无 `file`：需要 `fileid` 自带签名库（见 §5）。

**② 文件雕刻（file carving）**
- `binwalk`（固件/嵌套首选）、`scalpel`（SleuthKit 系，前身 Foremost）、手工 `dd skip/count` 按偏移抠。
- 「文件套文件」是 misc 题核心套路（S2 原话：mastroshka-like files-within-files）。

**③ 初始分析四件套**
- `strings`（`-o` 带偏移、`-e` 选编码）、`grep`、`bgrep`（二进制模式搜）、`hexdump -C`。
- ⚠️ S2 引用社区警告：strings 有编码陷阱，简单任务用没问题。
- → 我们无 `strings`：`deepstrings` 替身（P0）。

**④ 二进制↔文本编码**
- 靠**字符集特征**认编码：base64（[A-Za-z0-9+/=]、4 的倍数）、hex（[0-9a-f] 偶长）、base32（A-Z2-7）。
- 在线工具 CyberChef 的「配方链」思想 = 逐层剥壳+每层校验——我们 `decode_blob` 已实现同思想。

**⑤ 压缩包（ZIP 为主）**
- 工具：`zipdetails -v`（字段级）、`zipinfo`（不解压列表）、`zip -F/-FF`（修复）、`fcrackzip`（<7 位爆破）。
- S2 两条高频考点：**ZipCrypto 不加密文件名和原始尺寸**（RAR/7z 加密）；**已知明文攻击**——手里有包内任一文件的未压缩原文即可攻（AES-256 无此弱点）。
- → `zipinsight` 的判定依据全在这两条 + 注释/额外字段藏数据。

**⑥ 图片**
- **第一步永远是 `exiftool`**（元数据可能是金矿）；PNG 因无损最常被藏数据。
- 修复：`pngcheck`（校验/修损坏 PNG）；Wireshark 也能解剖 PNG/GIF/JPG。
- 隐写：`zsteg`（PNG/BMP LSB 全枚举）、`stegsolve`（位面/通道）；难点=「猜中嵌入工具」→ agent 化改造为**确定性优先级全枚举**（§3 范式 4）。
- GIMP 用 raw 模式开「无头像素数据」（内存里抠出的图像帧）；ImageMagick `compare` 找两图差异；`qrtools` 解 QR。
- → `pngtool/giftool/imgmeta` 的检查单来源（§5）。

**⑦ 文件系统/磁盘**
- `mount` + `tree` 三览；隐藏卷/unallocated/已删文件/NTFS ADS（交替数据流）是四大藏身处。
- `extundelete`（ext3/4 删档恢复）、`TestDisk`（分区表修复/FAT-NTFS undelete）、`SleuthKit+Autopsy`（全盘关键字+unallocated）。
- 嵌入式固件：Squashfs + `binwalk`/firmware-mod-kit。

**⑧ 流量包（PCAP）**
- 先看全局：`capinfos`/统计与会话视图 → 过滤（tshark 语法） → **对象导出**（Wireshark: Export Objects → HTTP）。
- 「packet carving」：`Xplico`/`NetworkMiner`/`tcpxtract`/`Foremost` 从流量里抠文件；坏包用 `pcapfix`。
- ⚠️ S2 提到的在线服务（PacketTotal/pcapfix web/DialABC）我们**运行期离线不可用**——能力必须本地化。
- S1 专章：**USB**（键盘 HID 码表还原 / 鼠标轨迹画 flag）、DNS、WiFi——TPCTF 风格高频。
- → `pcaptool` 检查单来源（§5）。

**⑨ 内存镜像**
- 重炮 `Volatility`（profile → 插件：进程/密码/网络/浏览器历史）。
- S2 降维暗示：CTF 内存题的「针」多为可打印串/内嵌文件 → 我们用 ③+② 组合拳吃多数题（范式 6）。

**⑩ PDF**
- 藏点清单（S2 列举，可直接变检查单）：**不可见图层 / XMP 元数据 / 增量更新（旧版本残留）/ 白底白字 / 图片压字 / 图压图 / 不显示的注释**。
- 工具：`qpdf`（解构/变换）、`peepdf`（py）、corkami PDF tricks 图解。

**⑪ 音视频**
- 先 `mediainfo`/exiftool 元数据；波形/频谱（Audacity、更专业 Sonic Visualiser）——文本藏频谱图是经典。
- LSB（离散 chunk 低位）；**DTMF 双音 / 摩尔斯码** → `multimon-ng` 解码。
- 视频=容器：`ffmpeg -i` 流概览/demux。
- → `wavtool`（stdlib `wave`：LSB/倒放/摩斯包络/DTMF Goertzel/粗文本频谱）。

**⑫ Office 文档**
- 两代格式：OLE（doc/xls/ppt/rtf）与 OOXML（docx/xlsx/pptx = **zip 容器**）→ **unzip 即检**是最易的一手。
- 宏分析：oletools；S2：恶意 VBA 通常只是跳板（下载 PowerShell），不复杂。
- OOXML 隐写有专门论文（2011），藏点=注释/设置 XML/docProps。

**S2 总纲（原话摘译）**：三大核心能力 = 脚本语言（Python）+ 二进制字节操作 + 格式/协议/结构/编码识别；理想环境是 Linux。**对 agent：第一条有 shell，第三条模型原生会，第二条=本文全部投资。**

---

## 3. 收敛出的优秀范式（7 条，附架构映射）

### 范式 0（我们的核心设计准则）：一步多查（Composite Scan）
人类工具是「原语」（zsteg 只做一件事，人脑串流程）；**LLM agent 的工具必须是「体检报告」**——一次调用跑完该格式的整套廉价检查清单，返回结构化判定（结构表+异常点+隐写快扫+建议），让模型一步决策。
理由：misc 角色仅 10 步预算。`pngtool` 一次返回「chunk 表/CRC/IEND 尾附/LSB 快扫/元数据」≈ 人用 exiftool+pngcheck+zsteg+hexdump 四步——**工具做并集，模型做交集**。

### 范式 1：套娃模型（识别 → 剥壳 → 递归）
S2 ②：files-within-files 是核心套路。解法=识别顶层→剥掉→对露出的工件递归，带**工作清单**与**回环保护**（同层不重复剥、深度上限）。
架构映射：LLM=调度器；本地工具=确定性剥壳器；Ledger=同参数去重（已有）；note=工件登记；report=交付。**缺环：`carve`（按魔数在任意偏移抠内嵌文件）**——`unpack_archive` 只认整文件容器。

### 范式 2：先扫后解，成本阶梯（cheap-first）
S2 ③ 四件套即「廉价优先」；我们提示词已强制全量明文扫。推广：**每个新工具必须先给廉价扫描模式回答「有没有东西」，再谈深解析**；命中即停。

### 范式 3：格式解剖而非黑盒（chunk-level parsing & repair）
S2 ①⑤⑥：CTF 高频=故意弄坏的结构（PNG 尺寸改/CRC 错/zip 目录烂/IEND 尾附）——黑盒直接打不开，解剖器才能看+修。stdlib 对 PNG(chunk)/ZIP(EOCD+CD)/GIF(block)/JPEG(marker) 全可实现。

### 范式 4：隐写 = 确定性优先级全枚举（metadata → LSB → 位面 → 工具族）
S2 ⑥：难点是「猜中嵌入工具」。agent 化改造：不猜，按固定优先级把可枚举面扫一遍（注释/EXIF/尾附 → 全通道 LSB → 调色板/帧延迟），每项给命中/未命中。

### 范式 5：流量 = 元数据分流 → 对象导出 → 协议专项
S2 ⑧ + S1 USB 专章：先会话统计 → 对象导出 → 协议还原（HTTP 对象/DNS 清单/**USB HID 键鼠**——键码表固定，天然适合确定性工具）。

### 范式 6：重工具域降维（内存/磁盘 = cheap-scan + carve）
S2 ⑦⑨ 的 volatility/SleuthKit 复刻不了；但 CTF 内存盘里的「针」绝大多是可打印串/内嵌文件 → `deepstrings + carve + flag 扫` 吃多数题。

### 范式 7（已有，保持）：交付纪律
report/evidence/账本去重已内建。新工具输出必须**带偏移的原文片段**，让 evidence 可直接复制。

---

## 4. 子类 × 范式 × stdlib 落地性对照

| 子类 | 社区标准工具 | 我们镜像现实 | stdlib 落地 | 难度 | 优先级 |
|---|---|---|---|---|---|
| 编码剥壳 | CyberChef 思想 | decode_blob ✅ | — | — | — |
| 容器解包 | binwalk/unzip | unpack_archive ✅ | — | — | — |
| **识别** | file/TrID | **无 file** | `fileid`：偏移魔数签名库（60+ 格式） | 低 | **P0** |
| 明文扫 | strings/grep | 有 grep 无 strings | `deepstrings`：可打印串+偏移+UTF16+正则 | 低 | **P0** |
| 嵌套雕刻 | binwalk/dd/scalpel | 无 binwalk | `carve`：多签名扫描+按偏移抠出 | 低-中 | **P0** |
| **PNG** | pngcheck/pngtools | 无 | `pngtool`：chunk 表+CRC 校验/修复+尺寸修复+尾附+IDAT 解压快扫 | 中 | **P1** |
| 图片隐写 | zsteg/stegsolve/exiftool | 无 | 并入 pngtool/imgmeta：全通道 LSB+EXIF/COM 注释 | 中 | P1 |
| GIF/JPEG | exiftool/Wireshark | 无 | `giftool`(帧/延迟/注释/尾附)、JPEG marker 走 imgmeta | 中 | P1 |
| **ZIP 解剖** | zipdetails/zip -FF/fcrackzip | 无 unzip（stdlib 可解） | `zipinsight`：加密位/压缩比/注释/已知明文线索（CRC 预判） | 中 | **P1** |
| **音频** | Audacity/SoX/multimon-ng | 无 | `wavtool`：LSB/倒放/摩斯包络/DTMF(Goertzel)/粗频谱 | 中-高 | P2 |
| **流量** | tshark/NetworkMiner/pcapfix | 无 tshark | `pcaptool`：解析/会话统计/HTTP 对象/USB HID 键鼠/DNS | 高 | **P2** |
| 文档 | peepdf/oletools | 无 | `docscan`：OOXML(zip+XML，易)；PDF 藏点清单扫描(中) | 中 | P3 |
| 磁盘/内存 | SleuthKit/Volatility | 无 | 降维：deepstrings+carve+flag 扫（范式 6） | 低 | P3 |
| QR | qrtools/zbar | 无 | 纯 python 需 Reed-Solomon，性价比低 | 高 | 缓议 |
| 信息搜集 | — | 运行期无网 | 平台 MCP 外不可达 | — | 不做 |

---

## 5. 工具补全清单（P0-P3 分期 + 每工具「一步多查」检查单）

统一走 [extending-agent.md](extending-agent.md) §5.2 **Recipe A**：新文件 `tools_<域>.py`，纯函数+SPEC+注册 `MISC_EXT_SPECS`/`_IMPL`+LocalTools 薄适配+角色 `local` 挂名。输出 ≤6000、路径锚定沙盒（`_resolve_path`）、返回带偏移原文（evidence 友好）。

### P0 识别三件套（最高性价比，每题都用）

**`fileid(path)`** —— 替代 file/TrID
- 魔数签名库（含偏移）：PNG `89504e47` / JPEG `ffd8ff` / GIF `GIF8` / ZIP `PK\x03\x04`+EOCD`PK\x05\x06` / RAR / 7z / gzip / bzip2 / xz / tar / PDF `%PDF` / WAV `RIFF…WAVE` / MP4 `ftyp` / FLAC / OGG / pcap `d4c3b2a1`+pcapng / ELF / PE / Mach-O / pyc / sqlite / ICO / WEBP / class…
- 一步多查：类型判定（含置信）+ 可打印率 + **EOF 标记后附加数据检测**（IEND/EOCD/%%EOF 之后还有字节=头号异常）+ 内嵌签名命中列表 + 熵提示（高熵=加密/压缩）+ 建议下一步。

**`deepstrings(path, min_len=4, regex=None, encoding="ascii|utf16")`** —— 替代 strings
- 可打印串+文件偏移；UTF16LE/BE；`regex` 过滤（默认预置 flag 正则，复用 `ledger.FLAG_RE`）；最长 top-N。

**`carve(path)`** —— 补套娃缺环
- 多签名扫描 + 边界启发（ZIP 向后找 EOCD；gzip inflate-until-error 定界；PNG 到 IEND；JPEG 到 FFD9）→ 落盘 `sandbox/carved/` 并回清单（偏移+类型+大小）。

### P1 高频格式解剖

**`pngtool(path)`**：chunk 表（type/len/CRC ok?）+ CRC 修复值 + IHDR 尺寸 vs IDAT 实际尺寸（修复建议）+ IEND 后尾附 dump + tEXt/zTXt/iTXt 全文 + IDAT 解压→unfilter 后的**全通道×全位面 LSB 快扫**（每组合给「可打印命中率」）。
**`zipinsight(path)`**：条目表（name/method/尺寸/压缩比/加密位）+ ZipCrypto vs AES 判定 + **文件名/尺寸可见性提示**（S2 考点）+ 注释/额外字段 + **已知明文可行性**（用户给明文→CRC32 预筛）+ 中央目录损坏→本地头扫描修复提示 + 嵌套签名。
**`giftool(path)`**：帧数/延迟序列/注释扩展/明文扩展/尾附/帧间差异提示（隐藏帧=延迟异常）。
**`imgmeta(path)`**：JPEG marker 走表（COM/APPn 全文）+ EXIF IFD（Make/Model/Comment/GPS）+ FFD9 后尾附；BMP 头+LSB。

### P2 特色赛道

**`wavtool(path)`**（stdlib `wave`，仅 PCM）：格式参数 + 每通道×位面 LSB + 倒放预览（首字节/哈希）+ 摩斯包络检测（dot/dash 序列）+ DTMF（Goertzel 8 频点序列）+ 粗文本频谱（行=频带，列=时间，`#` 密度）——频谱藏字直接在终端可读。
**`pcaptool(path)`**：全局头/链路类型 + 会话统计（对端×字节数×包数，分流）+ HTTP 对象导出落盘 + DNS 查询清单 + **USB HID 还原**（键盘码表→文本；鼠标位移→ASCII 画布）+ 明文协议 dump。pcap/pcapng 双格式。

### P3 文档与降维

**`docscan(path)`**：OOXML=zip 内 docProps/comments/settings 扫描（S2 ⑫）；PDF 藏点七项检查单（§2.2 ⑩，逐项命中/未命中）+ 增量更新段检测。
磁盘/内存：不新增工具，`deepstrings + carve + fileid` 组合拳（范式 6）。

### 提示词增量（在现有 SOP 上插行，不重写）
每工具在对应阶段加一行「优先调 X（一次返回…），不可用再手写 python」；P0 落地后判型压到 1 步，省出 2 步给深解析；步数预算重排。

### 验收
按 [extending-agent.md](extending-agent.md) §9 DOB：每工具独立单测（纯函数可 import 直测；PNG/ZIP/GIF 夹具用 stdlib zlib+struct 现造）；smoke 全绿；misc 角色 local 挂名后 `_role_specs` 过滤断言；输出超限/路径越界用例各一条。

---

## 6. 参考

- S1：[CTF Wiki 杂项卷](https://ctf-wiki.org/misc/introduction/)
- S2：[Trail of Bits CTF Field Guide — Forensics](https://trailofbits.github.io/ctf/forensics/)
- S2 内关键引用（已在正文消化）：pngcheck、zsteg、Stegsolve、binwalk、scalpel、zipdetails、fcrackzip、Volatility、SleuthKit、Xplico/NetworkMiner、qpdf/peepdf、multimon-ng、oletools、CyberChef、corkami 图解
