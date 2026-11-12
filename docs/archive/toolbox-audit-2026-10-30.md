# rev / pwn / crypto 工具箱 · 代码审计记录（2026-10-30 第二轮）

> 范围：`tools_domain_ext.py` 三段新增代码（rev v2 / pwn v1 / crypto v1）+ `config/agent.json` 三角色块 + 三个独立测试文件 + 接线层（`tools_local.call` / `_call_domain_tool` / `call_domain`）。
> 方法：逐行通读 + 已知向量仲裁（FIPS-197 / NIST SP800-38A / MT19937 / .NET AES 对照）+ 边界输入探测 + 集成路径实测（LocalTools 沙盒锚定）。
> 结论：**发现 10 处产品代码问题（1 高 / 6 中 / 3 低），全部已修**（`[toolbox-fix 2026-10-30]` 标识）；另修 4 处测试自身缺陷。

## 1. 发现并已修复（产品代码）

| # | 级别 | 位置 | 问题 | 修复 |
|---|---|---|---|---|
| F1 | **高·正确性** | `_cyclic_seq` | 缓存键 `(k, n)` 不含字母表 → 同长度不同自定义字母表**串号**，pwngen 生成串与 pwnfind 查找串不一致 → 偏移错 | 键改为 `(alph, n)`；回归：双字母表互查 |
| F2 | 中·正确性 | `pwnfind` | `max(n, 4)` 与 pwngen 的 `n` 不一致 → pwngen 用 n=3 时 pwnfind 强制 n=4 的序列 → **永远查不到** | 与 pwngen 完全同 n；回归：n=3 往返 |
| F3 | 中·稳定性 | pwnfind `value` / pwngen `raw` part / lookup `search` / bitflip `known/target` | 多字节字符（模型传 CJK）→ `encode("latin-1")` UnicodeEncodeError 或 bytearray 赋值 ValueError **裸崩**（`call_domain` 只捕 DomainToolError） | `_latin1` helper 统一入口 + bitflip 单字节守卫；alphabet 自动过滤回退默认表 |
| F4 | 中·稳定性 | aesblock `iv`/`nonce`/bitflip iv | 坏 hex → ValueError 裸崩（key/data 有包装，这三处没有） | `_hexbytes` 统一入口 |
| F5 | 中·稳定性 | pwngen parts | `count`/`cyclic`/`width` 非整数 → ValueError/TypeError 裸崩 | try/except → DomainToolError（带 parts[i] 上下文） |
| F6 | 中·稳定性 | `call_domain` 派发 | `size/n/counter/count/B/limit/seed/m` 垃圾值 → int() ValueError 裸崩；**elfpeek 的 offset/length 等同样暴露**（模型传 "0x40" 直接崩） | `_to_int` helper（None/空→默认值；垃圾→ToolError；**加成：支持 "0x40" 十六进制字符串**，原来会崩） |
| F7 | 中·上下文预算 | prng mt_seed/mt_predict | count 上限 2000 且无 `_clip` → 最多 ~20KB 文本**直灌模型上下文**，绕过平台 6000 字符契约 | `_clip` 包装；回归：count=2000 输出带截断标记 |
| F8 | 低·边界 | aesblock ctr | 计数器越过 2^128 → OverflowError 裸崩 | 128 位掩码 |
| F9 | 低·边界 | `_cyclic_seq` | n<3 时 3 字节针无唯一性 → pwnfind 可能给错偏移 | n≥3 守卫 |
| F10 | 低·可发现性 | ELFPEEK_SPEC description | 枚举了全部视图却漏了 `lookup`（enum 里有）→ 影响 rev 角色发现该能力 | 描述补 lookup 一句 |

## 2. 审计确认无问题（重点核过的疑点）

| 疑点 | 结论 |
|---|---|
| pwngen `path` 写文件是否越出沙盒 | ✅ `_call_domain_tool` 对 `path` 统一 `_resolve_path`：相对路径锚定沙盒根、绝对路径必须 `is_relative_to(base)`；**LocalTools 集成实测**：`sub/x.bin` 落盘沙盒内、`../esc.bin` 被 ToolError 拒绝 |
| `_Elf.symbols()` 是否只扫 .symtab（strip 的 libc 会失明） | ✅ 同时覆盖 SHT_SYMTAB(2) + SHT_DYNSYM(11)，lookup 对真实 libc 有效 |
| ELF32 分支（i386 pwn 目标） | ✅ `_phdrs`/`sections`/`symbols` 的 32 位 unpack 正确；新增 i386 夹具实测 lookup：符号 0x400054 + /bin/sh 偏移 |
| AES-192/256 无 KAT 覆盖 | ✅ 补 FIPS-197 C.2/C.3；**C.2 期望值用 .NET `System.Security.Cryptography.Aes`（ECB/NoPadding）仲裁**——本地实现正确，最初抄录的向量尾部有误（`…ec0d7191`，非 `…8ec0a7d9`） |
| Wiener 连分数递推 | ✅（前一轮已修 h/k 初值错位；本轮构造 d=5 向量复验） |
| `call_domain` 异常语义 | 边界把 DomainToolError `_fail()` 转译为 ToolError 抛出（设计如此）——测试需 catch 两类（已改） |
| config 接线 | ✅ json.load 有效；rev/pwn/crypto 三角色 local 名单全部解析到真实工具、无 unknown |
| stdlib 纯度 | ✅ `tests/smoke.py` 144/0（含 `case_stdlib_only` AST 扫描） |
| 并行会话隔离 | ✅ git status：本轮只动 `tools_domain_ext.py` / `config/agent.json` / `docs/archive/HANDOFF-2026-11-08-对外交接版.md`（附录）/ 三个 toolbox 文档与测试；并行会话文件零触碰 |

## 3. 测试侧修复（非产品缺陷）

1. 审计用例 catch `(DomainToolError, ToolError)`（call_domain 边界转译）。
2. `case_rsa_fermat` 断言 `and/or` 优先级混乱 → 清理为两因子分别断言。
3. AES-192 KAT 期望值纠错（.NET 仲裁）。
4. ELF32 夹具符号数与预留槽位差 16 字节 → 补 main 条目对齐。

## 4. 验收（全绿）

| 套件 | 审计前 | 审计后 |
|---|---|---|
| `tests/smoke.py`（全量） | 144/0 | **144/0** |
| `tests/smoke_rev.py` | 15/15 | **15/15** |
| `tests/smoke_pwn.py` | 16/16 | **24/24**（+8：字母表缓存、n=3 一致性、n 守卫、latin1、parts 坏值、派发坏整数、沙盒锚定集成、ELF32 lookup） |
| `tests/smoke_crypto.py` | 21/21 | **27/27**（+6：AES-192/256 KAT、坏 hex、多字节 bitflip、CTR 溢出、prng clip、派发坏整数） |
| compileall src+tests | 0 | **0** |

## 5. 残余记录在案（低优先，暂不修）

1. `tcp`/`tcp_batch`/`netcheck` 派发的 int/float 转换仍是裸 int()（属**既有基线代码**，非本轮新增；统一加固需动 tcp 通用路径，建议与并行会话协调后一轮处理）。
2. `mt_predict` 成功消息写"（N 个输出）"但实际只取前 624 个（语义提示小瑕疵，不影响正确性）。
3. `rsatool factor` 对 n≤1 退化输入返回平凡因子（不崩溃、无实际危害）。
4. `_int_of` 对 bool 输入按 int 处理（True→1，可接受）。