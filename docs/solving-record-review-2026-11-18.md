# 解题记录复盘与思路调整（2026-11-18）

> 数据来源：`runs/_ssh/linux*/`（5 轮、14 道次真实解题转录，全部在比赛机容器内跑）
> 目的：不是继续调预算数字，而是**读记录找共性、改打法**。

## 1. 记录里读出来的三个根因

### 1.1 agent 从不本地运行目标二进制，也从不真用 gdb

统计 5 轮全部 shell 调用（warmup 68 次、CrackMe、brichfire 等）：

| 指标 | 实测 |
|---|---|
| 写远程 exploit 脚本 + 打实例 | 全部（`python3 exp*.py` 连 `*.tcp-ctf2.dasctf.com`） |
| 本地运行附件本身（`./target` / `process()`） | **0 次** |
| 真正调用 `gdb`（`gdb -batch ...`） | **0 次**（只有 PLAN 里提到 21 次「gdb」） |
| 调用 `pwntools.process()` 本地复现 | **0 次** |

后果：偏移、SROP frame、ret2syscall 寄存器设置这些必须在本地验证的东西，只能靠远端
「`dumped core` / 无回显」猜——warmup 因此在同一族 gadget 上反复失败并烧到 15 分钟墙钟。

### 1.2 prompt 承诺的本地工具，镜像里根本不存在

镜像 `ghcr.io/yzqcyl/ctfy-agent-pub:v7.3` 的 `/ctfy` 只有 **`config/` 与 `src/`**，没有 `tools/`：

- prompt 里写的 `tools/linux_bootstrap/gdb_pwn.py`（pwndbg-lite）→ **不存在**
- 同理 `ghidra_decompile` / `tools/linux_bootstrap/*` 在镜像里也不在（v7.3 构建时这些还没进仓库）
- 但 gdb 本体、pwntools、z3、capstone、unicorn 都在（实测 `/usr/bin/gdb`、`pwn` 可 import）

也就是说：**模型被告知有本地复现能力，实际路径是坏的，于是退化成纯远端盲试**。

### 1.3 上下文被重复注入反复挤压

同一题转录里「## 题目背景（确定性信息）」重复出现 **9~14 次**（每次 delegate 重开专家上下文
都重灌一遍背景），叠加 354–558 条记录，压缩频繁、真正用于推理的窗口被挤掉。

## 2. 思路调整（已实施）

### 2.1 把「本地验证」做成工具，而不是靠模型记得敲 shell + gdb

新增 `pwnlocal`（`src/ctfy_agent/tools_local.py`，纯 stdlib）：

```
pwnlocal(path, stdin_hex/stdin_text, argv, timeout, gdb, gdb_cmds)
```

- 本地原生运行 ELF（含 32 位静态），可喂 stdin、传 argv、限时
- `gdb=true` → `gdb -batch -q -nx -ex 'run < infile'` + 寄存器/崩溃指令/bt/内存映射
- 轨迹 stdin 文件用完即删；参数越界/文件不存在/Windows 开发机都有明确报错文案
- 注册到 **pwn 与 rev 两个角色的工具面**（`crew` 默认 + `config/agent.json`）

### 2.2 写进硬规则

`config/agent.json` 的 pwn/rev system 追加：

> 你现在就在 Linux 容器里，容器内有 gdb 与原生 ELF 执行能力。拿到附件后先用 `pwnlocal`
> 本地跑（喂输入 / `gdb=true` 抓寄存器与崩溃点）验证偏移与利用，**本地跑不通就不要去打远端**；
> 远端只做最后验证。镜像里没有 `tools/` 目录，不要引用 `tools/linux_bootstrap` 下的脚本路径。

### 2.3 验证

`tests/smoke_pwnlocal.py`（spec/参数/缺文件/Windows 拒绝 + Linux 容器内真跑 echo/gdb）：

- Windows 开发机：`smoke_pwnlocal 全部通过`（实跑用例跳过）
- **比赛机容器内实跑：全部通过**（本地运行 exit=0、gdb 模式给出确定运行结论）
- `tests/smoke.py`：180 passed / 0 failed

## 3. 关键约束：要影响正式赛必须重建镜像

本次改动都在源码/配置里，**只对「本地检查 + 练习容器」立即生效**（练习是把仓库挂进容器跑）。
正式赛平台跑的是已提交的 `v7.3` 镜像（digest `2700685e…`），要带上 `pwnlocal` 与新 prompt，必须：

1. `python tools/publish_image.py --tag v8`（GitHub Actions 云端构建，需要 `GH_TOKEN`）
2. `python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v8`（匿名拉取 + 契约）
3. 平台页面「配置本次测评」→ 镜像地址改 `:v8` → 重新检查 → 提交审核（浏览器人工步骤，API 无绕法）

> 若比赛即将开始，这一步有风险；需要用户决定：是赶在赛前重建 v8，还是用 v7.3 原样参赛
> （v7.3 的 gdb/pwntools 仍在，只是没有 pwnlocal 这个「一键本地验证」入口）。

## 4. 后续可做（按性价比）

| 优先级 | 动作 |
|---|---|
| P0 | 重建 v8 并重新提交（否则 pwnlocal 进不了正式赛） |
| P1 | 把 `tools/linux_bootstrap/gdb_pwn.py` 等脚本**真的 COPY 进镜像**（或在 prompt 里彻底改用 pwnlocal，避免路径谎言） |
| P1 | 背景注入去重：同一 run 内「题目背景」只在首轮注入，后续用摘要引用（省钱省上下文） |
| P2 | 给 rev 增加「差分验证」专用入口（多组输入批量跑本地二进制比对输出），当前需模型自己写循环 |