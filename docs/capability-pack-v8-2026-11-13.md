# 能力补齐 v8（2026-11-13，120 题 BUUCTF 跑批驱动）

> 结论：本轮跑批暴露的「不是策略、是缺工具」的缺口，已按「能进镜像 + 可反复验证」的口径补进
> `Dockerfile`（v8 能力段），并配套 `tools/check_capabilities.py` 做**功能级**自检。
> 本机（Windows、无 WSL/Docker）无法真正构建镜像，镜像内验证由构建期 `RUN` 强约束 + CI 构建兜底。

## 1. 缺口从哪来（证据）

来源：`runs/_big/notes.md` B 段、`runs/_big/analysis2.md` 未解清单、逐题 transcript。

| # | 缺口 | 跑批里的具体表现 | 证据 |
|---|---|---|---|
| B1 | 平台附件不给 URL | 大附件题沙盒里只有 `_attachments.json`，无文件 | notes B1（53MB `attachment.zip`、`sydmm.zip`） |
| B2 | ZipCrypto 已知明文攻击 | 「二师兄呢？」类题没有 bkcrack，只能猜口令 | notes B2 |
| B3 | 二维码解码 | 沙盒连 Pillow 都没有，qr 题只能 strings | 多道 MISC 卡在「无法解码二维码」 |
| B4 | 流量分析（pcap/webshell） | 无 scapy/dpkt，专家手写 pcap 解析，多次 wall_clock | 「菜刀流量」「Webshell Plus」等 |
| B5 | REVERSE Hard 能力 | 无符号执行/反编译器；无 Linux gdb（本机） | REVERSE Hard 多题 coordinator 收口 |
| B6 | 隐写/文件雕刻/压缩包 | 无 binwalk/foremost/steghide/exiftool/7z | MISC 隐写、嵌套压缩包题 |

## 2. 补了什么（进镜像的部分）

### 2.1 Python 库（`Dockerfile` v8 段，pip）

| 包 | 版本 | 钉法 | 用途 |
|---|---|---|---|
| pillow | 12.3.0 | 版本 + sha256 | 图片读写（镜像里原本没有 Pillow，图片题直接退化） |
| zxing-cpp | 3.1.1 | 版本 + sha256 | 二维码/条码解码 |
| pycryptodomex | 3.24.2 | 版本 + sha256 | pyzipper 的 Cryptodome 命名空间依赖 |
| qrcode | 8.2 | 版本 | 生成二维码（自检用；也可给题面造码） |
| scapy | 2.8.0 | 版本 | pcap 读写/构造 |
| dpkt | 1.9.8 | 版本 | 轻量 pcap 解析 |
| pyzipper | 0.4.0 | 版本 | zip AES 加密/解密（stdlib zipfile 不支持 AES） |
| uncompyle6 | 3.9.3 | 版本 | 旧版 .pyc 反编译（REVERSE 类） |

C 扩展（pillow/zxing-cpp/pycryptodomex）沿用 capstone/pwntools 的口径：`--require-hashes` 锁 sha256，
其余纯 Python 包只锁版本。

### 2.2 命令行工具（apt）

`binwalk foremost steghide libimage-exiftool-perl p7zip-full poppler-utils john`

安装后立即 `command -v` 逐个校验，缺一个就让构建失败。

**刻意不装**（体积门禁 400MB，v7.3 实测 251.7MB）：

| 项 | 原因 |
|---|---|
| tesseract-ocr（+语料） | OCR 题在本题库占比极低，+18MB 不划算 |
| 7zip | 与 p7zip-full 都提供 `/usr/bin/7z`，同时装有文件覆盖风险 |
| SageMath | 500MB+，明确不做 |
| bkcrack | 需源码编译；可作为 v9 候选（见 §5） |
| angr / androguard | angr 依赖栈大（可能顶到体积门禁）、androguard 传递依赖含 IPython（+40MB）；v9 再评估 |

### 2.3 自检脚本 `tools/check_capabilities.py`

不是「import 一下」而是**功能往返**：

- qr：qrcode 生成 3 组不同 payload 的 PNG → zxingcpp 解码 → 文本比对；
- pcap：scapy 造 2 个包写 pcap → scapy 读回 payload + dpkt 读回包数/字节；
- zipaes：pyzipper AES 加密写 → 读回比对；
- z3/pwntools/pycryptodome/gmpy2/sympy：与镜像既有 selftest 同口径的小断言；
- androguard/uncompyle6：解析器/反编译器可加载；
- `--binaries`：binwalk/foremost/steghide/exiftool/7z/poppler/john，其中
  **steghide 做 BMP 嵌入→提取真实往返、7z 做加密压缩→解压真实往返**；
- `--rounds N`：同一套探针重复 N 轮（构建期 `--rounds 3`），避免「第一次碰巧过」。

构建期接线（`Dockerfile`）：

```dockerfile
COPY tools/check_capabilities.py /ctfy/tools/check_capabilities.py
RUN python /ctfy/tools/check_capabilities.py --binaries --rounds 3
```

## 3. 验证记录

### 3.1 本机功能验证（3 轮，18/18 通过）

Windows 本机 + `tools/pylibs` 外挂库，`--local` 跳过 3 个被 Smart App Control 拦未签名 `.pyd` 的探针
（z3/pwntools/zipaes；镜像里是 manylinux wheel，不受影响）：

```
python tools/check_capabilities.py --rounds 3 --local
结果：18 通过 / 0 失败
能力自检全部通过
```

QR、pcap、crypto、gmpy2/sympy、androguard、uncompyle6 均为真实往返；3 轮结果一致。

### 3.2 「能进镜像」验证：Linux wheel 逐个下载 + sha256 比对（9/9 通过）

`runs/_big/verify_cap_wheels.py` 直接按 Dockerfile 里钉死的 sha256 从 PyPI 下载**Linux x86_64
cp312/abi3** 的 wheel 并校验字节数：

```
[ok] pillow==12.3.0 pillow-12.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl 6940830 bytes
[ok] zxing-cpp==3.1.1 zxing_cpp-3.1.1-cp312-abi3-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl 1096795
[ok] pycryptodomex==3.24.2 pycryptodomex-3.24.2-cp37-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl 2280278
[ok] qrcode==8.2 / scapy==2.8.0 / dpkt==1.9.8 / pyzipper==0.4.0 / uncompyle6==3.9.3 / xdis==6.1.8
结果：9/9 个 wheel 校验通过
```

即：CI 构建时 pip 拉到的就是这个文件，sha256 与钉死值一致才能构建成功。

### 3.3 apt 包存在性（Debian bookworm = `python:3.12-slim` 基座）

`packages.debian.org/bookworm` 查证：`p7zip-full 16.02+really26.02`、`binwalk 2.3.4`、
`john 1.9.0` 均在 bookworm main；`foremost/steghide/libimage-exiftool-perl/poppler-utils` 为长期存在的基础包。

### 3.4 本机为什么没有真正 `docker build`

Windows 本机 Docker Desktop 的 Linux engine 未起来：`wsl -l -v` 报 `Wsl/E_ACCESS_DENIED`（WSL 未安装/不可用），
`docker info` 连不上 `dockerDesktopLinuxEngine`。因此镜像内验证的兜底是：

1. `Dockerfile` 构建期 `RUN python /ctfy/tools/check_capabilities.py --binaries --rounds 3` —— 任何一项失败构建即失败；
2. `tools/container_check.py` 在 CI 里仍会校验体积门禁/非 root/五条契约路径；
3. 若 CI 构建失败，日志会直接指到具体探针名（脚本每项独立 fail，不会第一项就退出）。

## 4. 与并行会话的边界

改动仅限：`Dockerfile`（v8 能力段）、`tools/check_capabilities.py`（新增）、`tools/fetch_pylibs.py`
（DEFAULT_PACKAGES 补新库）、本文件、`docs/submit-image-runbook.md` 的 v8 说明段与
`docs/REPORT-buuctf-120-2026-11-13.md`。`src/` 未改，`tests/smoke.py` 不受影响。

## 5. 遗留与 v9 候选

1. **bkcrack**（ZipCrypto 已知明文攻击）：需 `git clone + cmake`；建议 v9 用多阶段构建编译二进制后拷入。
2. **angr**（符号执行）：对 REVERSE Hard 增益最大，但依赖栈大，需要先量体积再决定。
3. **androguard**（APK 逆向）：本机已验证可用（4.1.4），但传递依赖含 IPython（约 +40MB），v9 评估。
4. **tesseract**（OCR）：按需再补。
5. 平台侧缺陷（非镜像能修）：大附件不给 URL、`【已停止提交】` 题仍可被选、部分 PWN 实例 502。