# 开源 CTF Agent 调研与借鉴（pwn / rev 方向，2026-11-17）

> 触发：ctf2 实测 `easyROPtocol` 时，pwn 专家把预算烧在「找不到实例端点 / 想找本地 gdb / 反复写正则解析 PLT」上。
> 用户要求：联网看别的开源 CTF agent 怎么做 pwn/rev，能借鉴就借鉴，必要时直接替换。
> 本文记录调研结论、本轮已落地的改动、以及**明确不采用**的方案及理由。

## 1. 调研对象

| 项目 | 这是什么 | 与 pwn/rev 相关的关键点 |
|---|---|---|
| **EnIGMA**（ICML 2025，基于 SWE-agent 0.7） | 目前公开成绩最好的 CTF LM agent 之一（NYU CTF 13.5%、InterCode-CTF 72%、CyBench 20%） | 提出 **Interactive Agent Tools (IATs)**：给 agent 可嵌套的 REPL——`gdb` 调试器（start/stop/step/breakpoint/任意 gdb 命令）与 **专用 server-connection 工具**（connect + send line/bytes）；并在 `config/ctf/ctf_pwn.yaml` / `ctf_rev.yaml` 里给出分方向提示词与调试纪律 |
| **PwnAI** | 多 agent 二进制利用框架 | 角色拆分为 Reversing（静态）/ Debugging（动态）/ Exploitation（利用）/ Writeup 四个 agent；工具接 **gdb+pwndbg / Radare2 / pwntools**；支持 `--remote host:port` |
| **D-CIPHER / NYU CTF Bench** | NYU CTF 基准的多 agent 基线 | planner + executor + auto-prompter 的三段式；强调「执行器只做小步、planner 换假设」 |
| **re-mcp** | 面向 LLM 的**无头**逆向工具 MCP server（IDA Pro / Ghidra 后端） | 把逆向动作做成细粒度工具面：`decompile_function` / `disassemble` / 函数列表 / **xrefs/调用图** / strings+引用 / imports/exports / 批量 `batch` / 元工具 `search_tools`、`get_schema`、`call`；还有引导式 prompt（`survey_binary`、`analyze_function`） |
| **NextCTF-Agent** | 国产多方向 CTF agent | 多智能体协作（Coordinator + 4 种策略）、`skills/` 技能包按题加载、本地 RAG 知识库、MCP 插件、平台适配（CTFd/GZCTF） |
| **awesome-llm-reverse-engineering** | LLM×逆向的资源清单 | 汇总了反编译/符号恢复/二进制分析类项目（选型参考） |

来源见文末链接。

## 2. 直接可借鉴的结论

1. **pwn 的核心交互不是「一条命令」，而是「一段会话」**。EnIGMA 的消融实验把这一点作为主要贡献：普通 shell-only agent 不会嵌套 REPL（nc、gdb、radare2 都要交互），
   所以 EnIGMA 专门做了 server-connection 与 debugger 两个交互工具；`ctf_pwn.yaml` 里反复强调「**优先用交互工具**、不要直接 nc / 不要开交互式 vim/python」。
   我们的 `tcp` 是单次往返，菜单题每步都要重连、每步一次模型调用，既慢又贵——这是本轮最该补的。
2. **分方向提示词要给「纪律 + 示范流程」，不是知识点堆砌**。EnIGMA 的 CTF 配置里，pwn/rev 共用的「IMPORTANT TIPS」是行为约束：
   优先用现有工具、不要 `python -c` 写长代码（落文件）、二进制输出先 hexdump/xxd、避免暴力爆破、一次只发一条命令；
   pwn 额外有「DEBUG SESSION TIPS」：先 disassemble/decompile 找点再下断点、用输入文件喂 stdin、用 `debug_exec` 读寄存器/栈。
3. **rev 的顺序是「静态定位 → 动态验证 → 算法复现」**，不是一上来全文反汇编。re-mcp 的工具面（decompile/xrefs/调用图）说明逆向 agent 真正需要的是**按函数/引用关系导航**，而不是整段 dump。
4. **角色拆分（Reversing / Debugging / Exploitation）有价值**，但我们的 crew 已有 pwn 专家 + 协调者，拆三个子角色会显著增加调用成本；本轮只把「静态定位 vs 远端验证」的纪律写进 pwn 提示词，不新增角色。
5. **工具面越细，模型越不容易自己造轮子**。re-mcp 的 `search_tools`/`get_schema`/`call` 元工具是解决「工具太多、模型不知道用哪个」的思路；
   我们已有 `elfpeek` 多视图 + `pwnkit` 确定性计算，本轮补 `pwnsession` 后，pwn 的「会话/计算/静态」三块齐了。

## 3. 本轮已落地（代码 + 提示词 + 回归）

| # | 改动 | 文件 | 借鉴自 |
|---|---|---|---|
| 1 | 新增 `pwnsession` 工具：持久 TCP/TLS 会话，一次调用跑完「banner + 多步 send/expect（≤40 步）」，`send_hex` 发二进制、每步返回 hex+ascii 回显；沿用实例白名单门禁 | `src/ctfy_agent/tools_domain_ext.py`、`tests/smoke_pwnsession.py`（离线菜单服务回归） | EnIGMA IAT 的 server-connection 工具 |
| 2 | pwn 提示词新增「交互式会话」+「示范流程（PWN 中等题 12 步）」：pwnkit 算偏移 → relocs 找调用点 → pwnsession 探菜单 → cyclic/pwnfind 定偏移 → ret2libc 出链 → 一把打 | `config/agent.json roles.pwn.system` | EnIGMA `ctf_pwn.yaml`（DEBUG SESSION TIPS / 优先交互工具）+ PwnAI 的「静态→动态→利用」顺序 |
| 3 | rev 提示词新增「静态优先 + 差分」：strings/symbols → 单函数 disasm → 多组输入差分 → 算法复现；禁止整段反汇编、禁止为跑本地 ELF 找 gdb/wsl | `config/agent.json roles.rev.system` | EnIGMA `ctf_rev.yaml` + re-mcp 的按函数/引用导航 |
| 4 | 协调者提示词加硬规则：派 pwn/rev 任务不得要求「本地 gdb/wsl 验证」（宿主机无 Linux 运行时） | `src/ctfy_agent/crew.py` | EnIGMA 的 IAT 分工（调试必须在受控环境内，而不是宿主随手 gdb） |

接线：`pwnsession` 经 `DOMAIN_SPECS`（16 个域工具）自动并入 `MISC_EXT_SPECS`，`config` 的 `roles.pwn.local` 与
`crew.DEFAULT_ROLES` 兜底同步；白名单/路径锚定复用 `LocalTools._call_domain_tool`，无需改 crew 门禁。

回归：`python tests/smoke_pwnsession.py` → 12 项 PASS（规格接线 / banner+多步收发 / hex payload / 参数校验 / 白名单）；
`python tests/smoke_pwn.py` 24/24；`tests/smoke_pwn_kit.py` 48 项；`tools/sync_roles.py --check` 零漂移；
全量 `tests/smoke.py`（见提交说明）。

## 4. 明确不采用（本轮）

| 方案 | 为什么不用 |
|---|---|
| 集成 gdb / pwndbg / radare2 / Ghidra / IDA | 提交镜像是**纯 stdlib Python（无第三方二进制、删了 pip）**；gdb/radare2 是 ELF 原生工具，Ghidra/IDA 有许可与体积问题。而且本机（Windows）没有 Linux 运行时，装了也跑不了目标 ELF。EnIGMA 的 IAT 价值在「交互语义」，我们用 `pwnsession` 把这块补上，不引入重依赖 |
| GhidraMCP / re-mcp / IDA-MCP 全套工具面 | 依赖 IDA/Ghidra 本机安装（Windows 可装，但镜像里不可用），且工具面过大（几十个工具）会挤压上下文；如果要给「本机开发增强」用，建议单开 profile，不并入提交镜像 |
| PwnAI 的四 agent 拆分 | 多一层 agent = 多一层 token 与协作开销；我们的协调者已经把「静态/动态/利用」按轮次分工，先看 `pwnsession` + 提示词改动的收益再说 |
| NextCTF-Agent 的本地 RAG 知识库 | 需要 embedding 模型与向量库（非 stdlib），收益不确定；我们的 `method_cards` + 方向提示词已覆盖「按题型推打法」 |
| 反编译器（decompile） | 纯 stdlib 没有可用反编译器；`elfpeek view=disasm` + 调用点/引用导航是目前性价比最高的替代 |

## 5. 实测（轮 3 / 轮 4，easyROPtocol，LLM 403 已解除后）

> 说明：轮 3 之前两轮分别因为「专家简报缺实例端点」和「LLM 403」没跑起来；轮 3/4 是第一次带着
> `pwnsession` + 新提示词真打。两轮都**没拿到 flag**，但把卡点从「环境」推到了「利用本身」。

| 轮次 | 配置 | 结果 | 关键观测 |
|---|---|---|---|
| #3 | 32 步 / 80 万 token | 中途人工停跑（已花 ~437k billed，55%） | `pwnsession` 首次调用即拿到 **TLS banner 243 B + 菜单**（协议握手打通）；但 pwn 仍用 `shell` 写 ssl/socket 脚本反复跑（72 次 shell 调用），协调者把「摸协议」原样连派十几次，烧到 40 万 token 仍在同一假设上打转 |
| #4 | 24 步 / 50 万 token | `coordinator_done`（协调者主动收口），无 flag | 打补丁后又跑：pwn 用 `pwnsession` 3 次 + `elfpeek` 16 次 + `pwnkit`/`pwnrop`，交付「main 菜单分发 + submit 溢出精确偏移 + 唯一可用 gadget 集」+ 可续做 exploit 蓝图；billed 358,623 / raw 1,989,289，10 个协调步 |

补丁（轮 3 之后加，已随代码提交）：
- pwn 提示词加【工具优先级】：与实例的交互一律走 `pwnsession`/`tcp`（走白名单），**不要**用 shell 写 TLS 客户端；
  shell 只做本地静态计算；静态解析用 elfpeek 多视图，别拼 grep/sed。
- 协调者加规则 5：同一角色、同一 objective 最多连派 2 次，第 3 次必须换假设/换角色/收口。

结论：
1. **交互式工具确实有效**：轮 3 第一次远程交互就拿到 banner 与菜单，此前轮 1 花 20+ 步找地址、轮 2 没跑起来；
2. 剩余卡点变成「本地跑不了 pwntools exploit 脚本」（headless Windows 无 pwn 模块）+「多轮交互式利用」——
   `pwnsession` 目前是「发死 payload」，还不能在会话里跑计算；下一步优先给 `pwnsession` 加
   「send 模板 + 计算变量」（或允许容器内跑脚本），而不是继续加提示词。

## 6. 下一步

- 若交互工具方向继续投入：给 `pwnsession` 加 step 级变量/计算（如 `{"send_hex": "{pack(overflow)}"}`），
  让 offset→payload→发送在**一次会话内**完成，避免「本地算不出/发不出」的断点；
- 若不再投入：当前状态下这题的天花板就是「静态事实完整、利用未打通」，需要在容器内（有 pwntools）跑脚本才能闭环；
- 可把 `pwnsession` 泛化给 misc/crypto 的 nc 式题（门禁不变）。

## 7. 来源

- EnIGMA 论文：<https://arxiv.org/abs/2409.16165>｜项目页（含 Benchmark 结果与 IAT 说明）：<https://enigma-agent.com/>
- SWE-agent 0.7（EnIGMA 实现所在仓库）：<https://github.com/SWE-agent/SWE-agent/tree/v0.7>
- Interactive Agent Tools 文档：<https://swe-agent.com/0.7/background/iat/>
- EnIGMA 的 pwn 配置（提示词/调试纪律/demonstration 机制）：<https://github.com/SWE-agent/SWE-agent/blob/v0.7/config/ctf/ctf_pwn.yaml>
- EnIGMA 的 rev 配置：<https://github.com/SWE-agent/SWE-agent/blob/v0.7/config/ctf/ctf_rev.yaml>
- PwnAI（多 agent 二进制利用）：<https://github.com/hal3002/pwnai>
- D-CIPHER / NYU CTF 基线 agents：<https://github.com/NYU-LLM-CTF/nyuctf_agents>
- re-mcp（IDA Pro / Ghidra 的无头 MCP 逆向工具面）：<https://github.com/stl3/re-mcp>
- NextCTF-Agent（多智能体 + skills + RAG）：<https://github.com/Alexander17-yang/NextCTF-Agent>
- awesome-llm-reverse-engineering（资源清单）：<https://github.com/ram-elgov/awesome-llm-reverse-engineering>
