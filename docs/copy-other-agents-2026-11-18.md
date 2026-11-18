# 抄作业：其他 CTF Agent 的做法与本项目采纳记录（2026-11-18）

> 用户指示：**「解不出来就去抄其他的 agent，不要在这浪费我的钱」**。
> 本文记录：① 公开最强的几个 CTF agent 怎么做；② 我们逐条对照缺什么；③ 已经抄进来的改动；
> ④ 明确不抄的部分与原因。原则：**先抄结构与工具，不再用付费批次试错**。

## 1. 调研对象（2026-11-18 检索）

| 项目 | 成绩 / 定位 | 关键做法 |
|---|---|---|
| **verialabs/ctf-agent**（[GitHub](https://github.com/verialabs/ctf-agent)） | **BSidesSF 2026 CTF 第一名，52/52 全解**（$1,500），作者是 CCTF 2024/2025 美国第一战队成员 | ① **Coordinator LLM** 管全局，**solver swarms** 逐题打；② 每题**多个模型并行竞速**，先拿到 flag 的赢；③ 每个 solver 在**独立 Docker 沙箱**里，预装 radare2/GDB/pwntools/ROPgadget/angr/unicorn/capstone、SageMath/RsaCtfTool/z3/gmpy2、volatility3/Sleuthkit/foremost/exiftool、stegseek/zsteg/ImageMagick/tesseract、ffmpeg/sox/PyTorch；④ **「solvers never give up」**——持续换方法直到拿到 flag；⑤ 模型间**共享发现（message bus）**；⑥ 比赛期间操作员可**中途给提示** |
| **EnIGMA**（ICML 2025，[论文](https://arxiv.org/abs/2409.16165)） | NYU CTF 13.5% / InterCode-CTF 72% / CyBench 20% | Interactive Agent Tools：gdb 调试器 IAT + server-connection IAT；分方向 prompt（ctf_pwn.yaml / ctf_rev.yaml）——**本项目 2026-11-17 已整篇移植**（见 `docs/open-source-ctf-agents-2026-11-17.md`） |
| **T3MP3ST**（[AtomGit 镜像](https://atomgit.com/gh_mirrors/t3/T3MP3ST)） | 多智能体 offensive-security 框架，自带 cybench 工具镜像 | 每题一个工具容器 + bench harness；工具镜像里同样塞满 pwntools/angr/radare2 |
| **CTF-Dojo**（[arXiv 2508.18370](https://arxiv.org/html/2508.18370)） | 658 道真实 CTF 挑战 + 可复现 Docker + 验证过的 flag | 给了「**离线可复现基准**」的思路：调 agent 不一定非要打线上平台（省钱） |
| **Retrieval-Augmented Reasoning for Offensive Security**（[IEEE](https://ieeexplore.ieee.org/document/11391064)） | CTF agent + 检索增强 | 把 writeup/知识检索进上下文能显著提升解题率（我们已有 423 条已解记录 + 复现库，但还没做检索注入） |

## 2. 逐条对照：我们缺什么

| 能力 | verialabs | 我们（改前） | 结论 |
|---|---|---|---|
| 多模型竞速 | ✅ 每题并行多模型 | ❌ 单模型（每角色一个） | **抄**：先做顺序接力（并行受实例额度/预算限制） |
| 工具齐全 | ✅ radare2/ROPgadget/angr/volatility/stegseek/zsteg/SageMath… | 部分：pwntools/gdb/capstone/z3/binwalk/foremost/steghide/exiftool/p7zip/john/upx | **抄**：补 ROPgadget；angr/radare2 因体积/许可暂不装 |
| 本地执行闭环 | ✅ 独立沙箱随便跑 | ❌ 到 2026-11-18 才补 `pwnlocal`（此前 0 次本地跑） | **已抄**（pwnlocal + 开跑强制注入） |
| never give up | ✅ 不放弃 | 有 80%/收口、ledger 刹车 | 不改：我们的预算是真金白银，保留收口纪律 |
| 跨 solver 共享发现 | ✅ message bus | 部分：ledger + facts 黑板 | 暂不扩（协调者已有账本注入） |
| 中途人工提示 | ✅ operator messaging | ❌ | 低成本可加（GUI 已有事件通道），暂缓 |
| writeup 检索增强 | —（他们靠模型能力） | ❌ 有 423 条已解记录但没检索 | **下一步抄**：本地关键词/TF-IDF 检索工具（无 embedding 依赖） |

## 3. 已抄进来的改动（本轮）

| # | 改动 | 文件 | 借鉴自 |
|---|---|---|---|
| 1 | **多模型接力**：`extra.swarm_models=[...]` 配置后，首选模型未解出自动换下一个模型再战（共享预算/实例，`force_rerun=True` 绕过已解跳过）；未配置时零成本、行为不变 | `src/ctfy_agent/gui/platform_worker.py` | verialabs 的 multi-model racing（降级为顺序接力） |
| 2 | 镜像补 **ROPgadget 7.7**（PWN gadget 自动化，体积很小） | `Dockerfile` | verialabs 工具清单 |
| 3 | 之前已落地的 **pwnlocal**（本地跑 ELF + gdb 批处理）与「本地优先」强制注入 | `tools_local.py` / `crew.py` / `solver.py` / `config/agent.json` | EnIGMA IAT + verialabs「沙箱里随便跑」 |
| 4 | **CI 全绿修复**（口径漂移/占位符误报/40 步预算行），使 v8 镜像能构建 | 多个测试与配置 | — |

配置示例（比赛时打开，练习默认关闭）：

```json
// config/agent.json 的 extra 里
"swarm_models": ["deepseek-v4.1-flash-1017", "<第二个模型名>"]
```

## 4. 明确不抄 / 暂缓

| 项 | 原因 |
|---|---|
| **并行**多模型竞速（同题同时跑 2–3 个模型） | 每题实例配额有限（平台侧 1 个）、token 预算共享；并行会把成本翻倍且互相抢实例。先顺序接力 |
| angr / radare2 / SageMath / cado-nfs / volatility3 | 镜像门禁 400MB：angr ~150MB、SageMath GB 级；radare2 不在 Debian bookworm 主源。用 z3 + capstone + unicorn + 现有工具替代 |
| stegseek / zsteg / ImageMagick / ffmpeg | 体积换收益不划算（隐写题占比低），先用 binwalk/foremost/steghide/exiftool |
| 无限重试（never give up） | 预算是自有 key；保留 80% 收口与 ledger 刹车 |
| 中途人工提示 | 比赛时人工盯盘可走 GUI 手动暂停/续跑，暂不做协议层 |

## 5. 下一步（不花钱的部分）

1. **writeup 检索增强**：用本地 423 条已解记录 + `docs/` 复现做关键词/TF-IDF 检索工具
   （纯 stdlib，无 embedding），在 pwn/rev 专家开跑时注入 top-3 相关片段——这是 IEEE 那篇的核心结论，
   且完全离线、零 API 成本。
2. **离线基准**（抄 CTF-Dojo 思路）：把已下载的附件 + 本地靶机做成 `local_bench`，用**假 LLM 跑流水线回归**
   （不花钱验证工具/流程），只在最终确认时用少量真模型 token。
3. ROPgadget 接入 pwnkit 的 gadget 搜索路径（当前 pwnrop 自己扫，可换成 ROPgadget 更全）。

## 6. 来源

- verialabs/ctf-agent（BSidesSF 2026 第一，52/52）：<https://github.com/verialabs/ctf-agent>
- EnIGMA：<https://arxiv.org/abs/2409.16165>｜<https://enigma-agent.com/>
- EnIGMA pwn/rev 配置：<https://github.com/SWE-agent/SWE-agent/blob/v0.7/config/ctf/ctf_pwn.yaml>
- T3MP3ST（cybench 工具镜像）：<https://atomgit.com/gh_mirrors/t3/T3MP3ST>
- CTF-Dojo（可复现离线基准）：<https://arxiv.org/html/2508.18370>
- Retrieval-Augmented Reasoning for Offensive Security：<https://ieeexplore.ieee.org/document/11391064>