# L2 隔离彩排环境（2026-11-16）

## 为什么必须隔离

2026-11-16 的 800k 复跑发现：agent 跑 pwn 时**没有走漏洞链**，而是直接读了宿主机
`/answers/flag`（0644），把原文当"候选 flag"提交。原因是我们第一版 L2 彩排让
**agent 和靶共享同一个 root 文件系统**——这既不是真实比赛形态（真 flag 在靶容器内，
agent 的 shell 在 VPS 宿主），也让"是否解出"的数据完全失效。

因此重做了隔离版彩排环境，目标：**agent 只能通过网络打靶，读不到靶的 flag**。

## 隔离设计

| 角色 | 权限 | 职责 |
|---|---|---|
| **root wrapper** `runs/_tpctf/l2_isolated_run.sh` | root | 每次运行**随机生成** flag；写 `/answers/flag`（`/answers` 700、flag 600）；起 socat（pwn 真 binary）与 `rev_target.py`（真 checker 服务）；只把「端口 + flag 的 sha256」写入 `/tmp/l2_ports.json`（0644） |
| **靶进程** | root | pwn 进程读 `/answers/flag`；rev 的 flag 只在 rev_target 进程内存/环境里 |
| **agent** `l2_sim_iso.py` | **非 root 用户 `l2agent`** | 以真 Crew + 真 LLM 跑题；sandbox 归它；提交/校验只拿到 sha256，用 hash 比对 |
| **工具箱** | `/opt/l2toolkit`（只读副本，a+rX） | `l2agent` 可用 pwntools/gdb/gcc/socat/ROPgadget/z3 |

其他约束：
- 仓库脚本里**不含明文 flag**（rev_target 从 `L2_REV_FLAG` 环境变量取；l2_sim_iso 只收 hash）；
- agent 读不到 `/root`、`/answers`、root 进程内存（ptrace 跨 UID 被内核拒绝）；
- 超时/预算取消沿用 2026-11-16 的修复（`cancel_event` 传入专家轮内每步检查）。

## 实测证据（2026-11-16 18:27）

- 安全验证（`l2agent` 身份）：
  - `cat /answers/flag` → **Permission denied**
  - `ls /root` → Permission denied
  - pwn 专家自己在报告里写：「沙盒内 `ls -l /answers` 与 `cat /answers/flag` 均 Permission denied
    （agent 非 root，符合题面）。必须走二进制利用。」
- 隔离版 pwn 800k 复跑（真 LLM `deepseek-v4.1-flash`，35 分钟墙钟）：
  - **687,240 tokens / 145 calls，未解出**；
  - 环境类工具错误 1（就是那次被拒绝的 `cat /answers/flag`，**不是工具链问题**）、命令类错误 0；
  - agent 已确认：checksec（Full RELRO/Canary/NX）、delete 后 `selected` 悬垂 UAF、
    seccomp 允许 `openat(O_RDONLY)+read(fd>=3)+write(1)`，并写了 pwntools exploit 但没打通；
  - 到达 35 分钟墙钟后被 `[已取消，停止]` 正常切断（取消修复生效）。
- 结论：**数据现在有效**——没有读到 flag 的捷径；pwn 在 800k/35min 内是"有正确方向但没做完"。

## 运行时坑：WSL 里 dashscope 走 IPv6 会卡死

本次运行前发现 LLM 调用卡在 `SYN-SENT`（IPv6 路由 `2001:da8...` deprecated）。
处理：WSL 里 `sysctl -w net.ipv6.conf.all.disable_ipv6=1`（wrapper 每次兜底），之后
`curl -4` 与 Python LLM 调用均正常（实测一次 56 token 的 chat 返回 `OK`）。GPU/网络类
环境问题应记入环境错误，不要误判成模型能力。

## 怎么跑

```bash
# WSL root（wrapper 内部会 su 到 l2agent 跑 agent）
wsl.exe -d Ubuntu-24.04 -- bash /mnt/e/ctfy_project/runs/_tpctf/l2_isolated_run.sh \
    --mode pwn --budget 800000 --steps 80 --wall-minutes 35 --stats-interval 120
# 只跑 rev
wsl.exe -d Ubuntu-24.04 -- bash .../l2_isolated_run.sh --mode rev --budget 200000
```

产物：`runs/_l2_sim_iso/sandbox/<cid>/`（agent 沙盒）、`runs/_tpctf/l2_out/summary_iso.json`、
`events_iso.jsonl`（事件流）、`runs/_tmp/l2_isolated.log`（实时日志）。

## 剩余限制

- flag 是**每次随机**的，无法跨轮复用；这是有意的（防作弊）；
- 靶与 agent 同 VM 但不同 UID，属于"网络隔离"级别；生产环境要更严格可换容器/网络命名空间；
- rev 的 checker 服务目前是自写 TCP 包装，与真平台暴露方式可能不同（但 checker 本身是真的）。

## 800k + 分段目标复跑（2026-11-16 18:36–19:24，有效数据）

参数：`--budget 800000 --steps 120 --wall-minutes 50 --staged-plan`，单次补全上限提到 16k
（减少"思考烧穿"）。结果：

| 指标 | 值 |
|---|---|
| tokens / calls | **796,057 / 151**（预算 800k 绑定，不是墙钟） |
| 墙钟 | 2863s（约 48 分钟） |
| 结果 | 未解出，无提交 |
| 真实环境错误 / 命令错误 | **0 / 0**（复核：之前的 3 条是报告文本复述，口径已修正） |
| 被拒绝的 /answers 访问 | **2 次**（隔离生效证据） |
| 工具调用 | shell 165、elfpeek 8、write_file 7、download 3、get_challenge 1、list attachments 1 |

**agent 实际推进到的位置**（沙盒留存 `exploit_a.py` / `probe.py` / `scan_stack.py` / `dump2.py`）：

- 分段目标 A **完成**：利用 delete 后 `selected` 悬垂指针 + edit 写路径，本地跑通
  「把指定 8 字节写到指定地址」的可控写原语，且可一键重跑复核；
- **任意读**：KIND_BLOB 的 view 路径本地打通并逐字节验证；
- **PIE 泄漏**：从记录结构固定字段泄漏到一枚 PIE 数据段指针，可据此算基址；
- **卡点**：libc 基址没算出来（预算在"静态判定后就耗尽"），没走到 openat("/answers/flag")
  + read + write 的收口；团队 3 次"思考烧穿"（16k 仍打满）。

结论：隔离后数据可信；**800k/50min 不足以让当前 agent 解出 clean-room**，但它已从
"只会分析"推进到"有可控写原语 + 任意读 + PIE 基址线索"的 near-miss 状态。

## 二轮续跑（20:36–21:24，同一沙盒、再 800k，有效数据）

- 机制：新增 `--continue-from <上一轮 run-id>`，把上一轮 sandbox 里 agent 自己产出的
  15 个文件（`exploit_a.py`、日志、dump 脚本等）拷进新一轮；题面追加「续跑说明：A 段已完成，
  直接从 B 段开始，优先算 libc 基址」；预算、隔离、墙钟与上一轮相同。
- 结果：

| 指标 | 值 |
|---|---|
| tokens / calls | **792,078 / 150**（预算 800k 绑定） |
| 结果 | 未解出，无提交 |
| 真实环境错误 / 命令错误 | **0 / 0** |
| 被拒绝的 /answers 访问 | **2 次**（隔离继续生效） |
| delegates / 工具调用 | 8 次派活；shell 154、elfpeek 20、write_file 16、pwnrop 1 |

- **推进**：A 段写原语复核通过；B 段第一步 **libc 基址算出并可复现**
  （`calc_base_final.py`）；写了 `exploit_b.py` 并对真实靶跑了一次，卡在
  "泄漏读取不足 8 字节"，未完成 openat/read/write 收口。
- **两轮合计**：约 **158.8 万 tokens**（687k + 792k + 中间 staged 跑 796k——实际单题累计
  三轮 ≈227.5 万），仍未解出 clean-room。
- 实现 bug：本轮结束写 summary 时 `args` 变量被循环覆盖导致崩溃（数据本身完整，已由
  `state.json`+transcripts 重建为 `summary_iso_pwn3.json`），代码已修（改名 `tool_args`）。

**结论**：clean-room 属于"要跨过 libc/收口这一步"的硬题；两轮 800k 把 agent 推到
"A 段 + 任意读 + PIE 基址 + libc 基址"但仍未出 flag。继续加预算的边际收益在下降，
建议按时间预算决定是否第三轮，或转向 rev/crypto/web 的隔离彩排。

## 第三轮（23:00–23:50）：补了"泄漏链能力"后仍然无 flag

**本轮改动**（针对二轮卡点"libc 基址算不出/泄漏短读"）：
1. 新增团队资产 `runs/_tpctf/l2_assets/leak_helpers.py`（拷进沙盒供 import）：
   - `ElfOffsets` / `LibcOffsets`：pyelftools/readelf 双路解析符号、GOT 槽、629 条
     `R_X86_64_RELATIVE` 槽位与 addend → 泄漏一个已知位置指针即可反推 PIE 基址；
   - `read8()`：任意读统一收口，**短读直接抛错并打印长度/地址**（正是二轮 exploit_b 的坑）；
   - `MenuClient`：clean-room 行菜单协议客户端（expect/sendline/recv 定长）；
2. `method_cards.py` 新增 PWN 泄漏链卡（tags: heap/sanitizer/asan/uaf/arbitrary_read）：
   "① 读记录结构固定字段拿 heap/PIE 指针 → ② ELF 偏移换算 PIE/libc base → ③ 每次读 8 字节
   断言长度 → ④ 再构造 openat/read/write 或 ret2libc"；
3. 题面 tags 注入（隔离平台 get_challenge/list_challenges 带 tags），确保方法卡真的被送进专家上下文。

**结果**：

| 指标 | 值 |
|---|---|
| tokens / calls | **800,079 / 169**（800k 预算 100% 用尽） |
| 结果 | 未解出、无提交 |
| 真实环境错误 / 命令错误 | **0 / 0** |
| 被拒绝的 /answers 访问 | 1 次（隔离生效） |
| 工具 | shell 158、write_file 16、elfpeek 3、get_challenge 1 |

**推进（泄漏链能力生效）**：
- B 段 **libc 基址判定完成并交叉验证**：PIE base = `0x5d47c99a5000`（由 blob `label` 指针 −
  `LABEL_VADDR 0x1343a0` 得出，与 `/proc/pid/maps` 完全一致 PIE_OK=True），
  `__libc_start_main` 泄漏 → libc base 也能对上；
- 但 C 段（远程 ROP/收口）**没来得及执行**：预算 99% 时只剩收口调用，agent 按纪律交卡点，
  没有对远程发起交互、也没有提交猜测值。

**四轮累计**：687k + 796k + 792k + 800k ≈ **3.08M tokens**，仍未解出 clean-room。
结论：该题在当前 agent + 800k/题预算下属于"能力边界外"；泄漏链资产把 B 段从"算不出"变成
"可验证"，但完整利用链（栈写 + ROP 收口）需要更多轮次/预算。**建议停止在该题烧预算**，
把隔离彩排转向 rev/crypto/web，等有真实比赛窗口再验证。
