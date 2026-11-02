# 第三方组件声明（THIRD_PARTY_NOTICES）

本仓库自有代码以根目录 `LICENSE`（MIT）授权。**但分发的容器镜像内包含下列独立程序/数据**，
它们各自保留原有许可证，不受 MIT 重新授权；再分发镜像时必须同时履行下列义务。

> 口径：版本、字节数、sha256 全部与 `Dockerfile` 中的构建期钉死值一致（三处齐 → 构建失败即发现被换）。
> 本文件只做声明与索引，不是许可证正文；许可证全文以各上游仓库为准。

---

## 1. ffuf 2.1.0 —— Apache-2.0

| 项 | 值 |
|---|---|
| 上游 | https://github.com/ffuf/ffuf |
| 版本 | 2.1.0（`linux/amd64`） |
| 制品 | `ffuf_2.1.0_linux_amd64.tar.gz` |
| 字节数 | 3478614 |
| sha256 | `fc2c82736c14dcbea4daf3d3cf3878c1c4773008ba45c2bc0fceba7d17b40bb5` |
| 许可证 | Apache License 2.0 |
| 安装位置 | `/usr/local/bin/ffuf` |
| 使用方式 | 仅由 `tools_webscan.run_fuzz` 以子进程 argv 调用（模型不直接拼命令行） |

**义务**：保留版权与许可证声明；若修改其源码需标注变更（本项目未修改，仅二进制分发）。
Apache-2.0 全文：https://www.apache.org/licenses/LICENSE-2.0

## 2. SecLists（三张词表）—— MIT

| 项 | 值 |
|---|---|
| 上游 | https://github.com/danielmiessler/SecLists |
| 固定提交 | `758fecf4925900dd1bd0a3cba39551d6b4f2da8c` |
| 取用文件 | `Discovery/Web-Content/common.txt`（38536 B）、`burp-parameter-names.txt`（54157 B）、`raft-small-words.txt`（348619 B） |
| 许可证 | MIT |
| 安装位置 | `/opt/wordlists/{common,params,small}.txt` |

**义务**：保留 MIT 版权与许可声明（本仓库随附 MIT 全文即可满足；上游版权人为 SecLists 贡献者）。

## 3. sqlmap 1.10.10 —— GPLv2 或更高（GPL-2.0-or-later）

| 项 | 值 |
|---|---|
| 上游 | https://github.com/sqlmapproject/sqlmap |
| 版本 | 1.10.10 |
| 制品 A | `archive/refs/tags/1.10.10.tar.gz`，8373244 B，sha256 `7dd690c62fb1ae58bd8555b91012896863c47fc245dc6d39ba300362a3368d17` |
| 制品 B | `raw/.../1.10.10/sqlmap.py`，30040 B，sha256 `311eb1962efd1ceb1b26a6086b639f8e85f287888717c5eb7dbd28af63cdb56b` |
| 许可证 | GNU GPL v2 或更高版本 |
| 安装位置 | `/opt/sqlmap/`（已删 `doc/ tests/ .github/`） |
| 使用方式 | 仅由 `tools_websql` 以子进程 argv 调用 |

**义务（GPL 传播）**：分发含 sqlmap 的镜像即构成 GPL 意义上的分发，必须
① 附上 GPL 全文与版权声明；② 提供获取对应源码的途径（本节已给出上游 tag 与校验值，
可复现构建下载）；③ 若修改 sqlmap 源码，需以 GPL 公开修改部分（本项目未修改）。

**重要边界（避免污染自有代码）**：sqlmap 以**独立程序**形式安装（不在 `site-packages`、不被
`import`），自有代码只通过子进程 argv 调用它。本仓库的 MIT 授权只覆盖自有代码，
不覆盖上述第三方程序；反之这些第三方许可证也不延伸到本仓库自有代码。

## 4. 基础镜像与系统包

| 组件 | 许可证 | 说明 |
|---|---|---|
| `python:3.12-slim`（Debian） | PSF / 各包自带 | 见镜像内 `/usr/share/doc/*/copyright` |
| `curl grep coreutils ca-certificates bash passwd`（apt） | GPL/LGPL/OpenSSL 等 | 同上 |

## 5. 未随镜像分发的参考实现（仅阅读借鉴，无代码引用）

- `ctf-forge`（AGPL-3.0，作者私有仓库）：仅借鉴**机制思路**（实例池清扫、有界输出收割、墙钟预算、
  输入指纹、隔离自检），**未复制任何源码/注释/文档原文**。因此本仓库不因之受 AGPL 约束。
- 开源 agent 的机制调研（Cybench / HackSynth / PentestGPT / D-CIPHER / EnIGMA）见
  `PROGRESS.md §3.5`，同样只借鉴思路。

---

*维护规则：镜像内新增任何第三方程序或数据 → 必须在构建期钉死版本+校验值，并在此登记许可证与义务。
`tools/secret_scan.py` 与 CI 只校验「将要提交的文件」，不代替这份人工判断。*
