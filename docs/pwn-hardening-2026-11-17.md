# PWN 硬化（2026-11-17）：确定性计算工具 + 团队资产入包

> 背景：PWN 是当前最大短板——真实外部题 0/4、BUUCTF 120 题里 3/31、L2 隔离彩排 5 轮 ≈440 万 token
> 仍未出 flag。复盘（`docs/solving-record-2026-11-17.md`）里卡点不是「不会写 exploit」，而是三层机械工作
> 每轮重造：**libc 偏移手算、泄漏后基址推导、ASan shadow 解封**；再加上团队资产只躺在 gitignored 的
> `runs/_tpctf/l2_assets/` 里（一次空间清理就会消失，也进不了镜像）。

## 1. 这轮做了什么

| # | 改动 | 文件 | 为什么 |
|---|---|---|---|
| 1 | 新增 `pwnkit` 工具（纯 stdlib，op 式） | `src/ctfy_agent/tools_domain_ext.py`、`src/ctfy_agent/pwn_kit.py` | 三个机械计算全部确定性化，模型不用在 800k 预算里现写解析脚本 |
| 2 | 团队 pwn 资产入包 | `src/ctfy_agent/pwn_assets/`（`leak_helpers.py` / `asan_shadow.py` / `pwn_stack_probe.py` + README） | 原来在 `runs/_tpctf/l2_assets/`（gitignored）；现在随 `COPY src/` 进镜像，不会丢 |
| 3 | pwn 角色接线 + 提示词 | `config/agent.json`（`roles.pwn.local += pwnkit`、system 补一段）、`crew.DEFAULT_ROLES["pwn"]` 兜底 | 工具不接线=不存在；兜底也要有，防 agent.json 丢失退化 |
| 4 | 方法卡更新 | `src/ctfy_agent/method_cards.py`（栈溢出卡 + ASan 泄漏链卡） | 题面命中 pwn tag 时直接把「用 pwnkit 算，不要手算」推到模型眼前 |
| 5 | 回归 | `tests/smoke_pwn_kit.py`（48 项） | 数学、真实 libc 解析、错误路径、接线、资产一致性全部钉住 |

## 2. `pwnkit` 能力与边界

| op | 输入 | 输出 | 典型用法 |
|---|---|---|---|
| `libc` | 沙盒里的 `libc.so.6` | 版本串、build-id、14 个关键符号偏移（vaddr）、`/bin/sh` 的 vaddr/file_off、缺哪些符号 | 附件带 libc 时第一步 |
| `ret2libc` | libc + 泄漏符号名 + 泄漏值 | libc 基址、system、`/bin/sh`、pop_rdi/pop_rsi/pop_rdx/syscall 等 gadget（**已加基址**） | 泄露出 puts/read 之后一步出链 |
| `asan` | 目标地址 + 窗口长度 | shadow 区间、每个 shadow 字节要写的值、恢复提示 | 写真实栈（ASan 题最后一步） |
| `assets` | — | 团队资产目录 + 文件清单 + 用法 | 找 `pwn_stack_probe.py` 等 |

ASan 语义细节（本轮修过一版）：`shadow(addr) = (addr>>3)+0x7fff8000`；块首对齐时低 3 位=可访问前缀长度；
**起始地址不齐时首块整块放开（写 0）**——否则 ASan 仍按「前缀可用 N 字节」把目标写拦掉（多放开无害，少放会失败）。

边界（明确不做）：不自动连远端、不生成完整 exploit、不含 libc 数据库（`libc` 需要一个真实 libc 文件，
匹配 libc-database 的「只给两个偏移找版本」不在本轮）；`one_gadget`、堆风水模板仍未做。

## 3. 验证

```bash
python tests/smoke_pwn_kit.py      # 48 项：ASan 数学 / 真 libc 解析 / ret2libc / 资产纯 stdlib / 接线
python tests/smoke_pwn.py          # 24/24（既有 pwngen/pwnfind/elfpeek 回归）
python tests/smoke_libs.py         # 33 项通过（pwnrop/z3/pycrypt 接线，2 组因无 ELF 编译环境跳过）
python tests/smoke_domain_cards.py # 方向卡（同步把计数 8→9：udp 卡是 11-13 加的，测试之前过期）
python tools/sync_roles.py --check # 三源零漂移
python tests/smoke_strategy.py     # 策略回归：新增 6 项断言（本机 DSH 沙盒禁写系统 TEMP，
                                   # 本次用内联断言复核过；CI/容器里应正常）
python tests/smoke.py              # 全量 180 passed / 0 failed（含 crew/solver 改动回归）
```

真 libc 解析用仓库里的跑批残留样本 `runs/tpctf2026/attachments/AUTHORED-31FB487C/libc.so.6`
（Debian GLIBC 2.41），验证到：版本串、40 位 build-id、system/puts/read 偏移、`/bin/sh` vaddr 全部可复算；
`ret2libc` 用构造泄漏值反推基址，与「base+偏移」逐项对齐。**没有样本时该组自动跳过**（`runs/` 被 gitignore）。

## 4. ctf2 真实平台实测（2026-11-17 晚）

> 用户指定「去 ctf2 找一题中等难度测试」，实际跑了同一题两轮。

**选题**：`ctf2` 练习场 `BUUCTF` 的 `easyROPtocol`（PWN / Medium / 85 队已解；附件 `pwn` 14,968 B +
`libc-2.31.so` 2,029,224 B；实例 `nc_ssl=true` 即 TLS）。平台档案切换后 whoami / 练习场列表 / 题单过滤
（category=pwn + difficulty=Medium）/ 附件下载 / 起实例全部可用。

**pwnkit 在真 libc 上算对了**（离线、零配额）：`libc-2.31.so` 解析出
`Ubuntu GLIBC 2.31-0ubuntu9.2`、build-id `099b9225…`、`system=0x55410`、`puts=0x875a0`、`/bin/sh=0x1b75aa`
——与公开 libc 数据库的 glibc 2.31 偏移逐项一致（上一轮只验过 Debian 2.41）。

**实测第 1 轮**（自压 40 万 token / 24 步）：实例起成功、TLS banner 与菜单抓到，96 次模型调用、
400,343 token，`stalled` 无 flag。复盘转录（`runs/ctf2-easyrop/transcripts/…jsonl`）暴露三条打法问题：

1. **专家拿不到实例端点**：协调者简报里有 `access_url`，专家简报没有 → pwn 专家花 20+ 步
   shell 翻目录找 host:port（还去读 `state.json` 被沙盒边界拒绝），最后靠协调者转述才连上；
2. **把宿主机当 Linux 用**：在 Windows 沙盒里查 `wsl/docker/gdb`、写反汇编脚本时被 `dis.py`
   遮蔽标准库坑住，反复换正则解析 PLT stub（同一假设加了 5 个扫描脚本）；
3. `pwnkit` 只被调用 1 次（没走到「泄漏后算链」阶段），偏离「先算再打」的设计意图。

**针对性的打法修复（随本提交）**：

- `solver._role_context` 新增「实例接入」块：把 `host/port`、`nc_ssl→tls=true`、允许连的实例、
  「本机无 Linux 运行时（无 wsl/docker/gdb）」确定性注入每个专家简报（`_instance_hint`，无实例时不注入）；
- `config/agent.json roles.pwn.system` 补「实测事故补丁」：菜单/协议题先单字节探路、`tls=true`、
  别找 state.json、反汇编一个假设最多 1 个脚本、每 3 步无新观测换假设；
- `crew.COORDINATOR_PROMPT` 加硬规则：派 pwn/rev 任务**不要**要求「本地 gdb/wsl 验证偏移」；
- `tests/smoke_strategy.py` 加 6 项覆盖（实例接入块 / host:port / tls 提示 / 无实例不注入）。

**实测第 2 轮（80 万预算）没跑起来**：起实例与附件正常，第 3 步 `reason=llm_error`——LLM 端点
（DashScope 兼容接口）返回 `403 AccessDenied.Unpurchased` / `400 Access denied, account is in good
standing`，逐个试 `deepseek-v4.1-flash` / `v4.1-flash-1017` / `qwen` / `glm` 均被拒。**这是账户/额度问题，
不是代码问题**；需要用户处理 key/余额后才能继续验证打法修复的真实收益。消费记录：第 1 轮 400,343
token / 96 calls；第 2 轮 121,758 token / 38 calls（均未提交 flag，提交配额未动）。

两轮实例均已在收尾时按 `challenge_id` 回收（`delete_environment` 回 `NOT_FOUND`，平台侧无残留环境）。

## 5. 还没验证的部分（不要吹）

- 离线单测全绿 + 真 libc 解析验证过，但**真实赛题上 pwnkit 带来的收益还没被证明**（第 2 轮因账户
  问题中断；第 1 轮受 40 万上限与上述打法问题影响）；
- 资产脚本已改成纯 stdlib + 包内 `pwn_kit`：原 `leak_helpers` 用 pyelftools/readelf （镜像里都没有，沙盒一跑就挂），现在 `leak_helpers`/`pwn_stack_probe` 都能 `python3 -m` 自检（实测 exit 0）；`tests/smoke.py::stdlib_only` 恢复绿；
- 真实收益要等下次 pwn 批次对比：预期减少「算偏移/推导基址」类步数，但**不会**自动解决 ASan/堆题；
- 下一个该补的短板（按收益排序）：libc 版本匹配（只给偏移找 libc）、one_gadget/FSOP 模板、
  堆（tcache/fastbin）脚手架、seccomp 落地后的 ORW 链模板。

## 6. 追加：开源 CTF Agent 借鉴（同日，见 `docs/open-source-ctf-agents-2026-11-17.md`）

第 1 轮复盘里「pwn 专家找不到实例端点 / 想找本地 gdb / 反复写正则扫 PLT」这三个问题，
除已做的简报注入 + 提示词补丁外，又按 EnIGMA（SWE-agent 0.7）的 Interactive Agent Tools 补了
**`pwnsession`**：一次调用跑完「连接 → banner → 多步 send/expect（≤40 步）」，`send_hex` 发二进制，
每步回 hex+ascii；菜单/协议题不必每步重连。pwn 提示词同时加了「12 步示范流程」，
rev 提示词加了「静态优先 + 差分」顺序。

- 该工具已接线到 `config/agent.json roles.pwn.local` 与 `crew.DEFAULT_ROLES` 兜底，白名单复用
  `LocalTools._call_domain_tool`（未登记实例仍被拒）；
- 离线回归 `python tests/smoke_pwnsession.py`（本地菜单服务，不依赖平台）；
- **实测（轮 3/4，限额 80 万→50 万）**：`pwnsession` 首次调用即打通 TLS 握手并拿到 banner 243 B + 菜单
  （轮 1 曾花 20+ 步找端点）；轮 4 交付「main 菜单分发 + submit 溢出精确偏移 + gadget 集」与 exploit 蓝图，
  但仍未拿 flag，且 pwn 仍大量用 shell 重写 TLS 客户端（轮 3 72 次/轮 4 81 次）——已补「工具优先级」
  与协调者「同一 objective 连派 ≤2 次」两条纪律。剩余卡点：headless 宿主机跑不了 pwntools exploit 脚本，
  多轮交互式利用需要 `pwnsession` 支持 step 内变量/计算（见调研文档 §6）。
- 两轮用量：轮 3 billed ~437k（人工停跑）；轮 4 billed 358,623 / raw 1,989,289，10 个协调步、
  `coordinator_done` 主动收口；平台实例两次收尾均 `NOT_FOUND`（无残留、提交配额未动）。

### 6.1 二轮：整篇移植 EnIGMA 的 pwn/rev ACI

在用户要求「别再坚守、基本全抄」之后，放弃了只补几条纪律的做法：
config/agent.json 的 
oles.pwn.system / 
oles.rev.system 按 EnIGMA（SWE-agent 0.7 CTF）的 system_template
结构整篇重写（SETTING → 环境 → 工具映射 → RESPONSE FORMAT → IMPORTANT TIPS → 方向打法顺序 → 提交纪律），
工具名映射到本地：open/goto/scroll/search/edit → 新工具 **fileview**，disassemble/decompile → elfpeek 多视图，
debug_* → shell 里的 gdb -batch（仅容器内），connect/send_* → pwnsession/	cp，submit → 
eport。
ileview 同时接进 pwn.local / 
ev.local 与 DEFAULT_ROLES 兜底；回归见 	ests/smoke_pwnsession.py（含 fileview 用例）。
仍缺：反编译器、pwndbg、one_gadget/FSOP 模板、堆脚手架——这些是镜像依赖，不是提示词能补的（详见调研文档 §5.5）。
