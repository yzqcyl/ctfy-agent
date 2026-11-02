# rev 方向工具箱 v2 · 落地记录（R1）

> 会话：toolbox（rev/pwn/crypto 专用，与并行会话零文件冲突）
> 日期：2026-10-30（R3 补记 2026-11-02）· 状态：**R1 已完成并通过全部回归**；
> **R2（capstone 进镜像）2026-11-02 已写入 Dockerfile**（v5 起生效，v4 漏做——见 §6.1）；
> **R3（relocs / dynamic 两视图）2026-11-02 已落地**，见 §6.2。
> 关联方案：本文件是聊天记录里「rev 完善方案」的实施记录与验收凭据。

## 1. 改动清单与标识

所有新增代码用 `[rev-toolbox 2026-10-30]` / `>>> … <<<` 成对注释包裹（JSON 除外，见 §3）。

| 文件 | 改动 | 标识方式 |
|---|---|---|
| `src/ctfy_agent/tools_domain_ext.py` | ① imports +4（marshal/types/zlib 纯 stdlib + 行内标记）；② `elfpeek` 签名扩 9 参、bytecode/utf16 前置分流、info 补入口段行；③ gadgets 视图改为委托 `_view_gadgets`；④ 尾部新增 disasm 分支 + view 枚举更新；⑤ 大段新增实现（`_phdrs/_off_to_vaddr/_entry_line/_capstone_for/_nearest_symbol/_view_disasm/_view_gadgets/_strings_utf16/_pyi_parse/_pyz_parse/_view_pyinstaller/_view_pyc/_view_bytecode`）；⑥ `ELFPEEK_SPEC` 重写；⑦ `call_domain` elfpeek 派发透传新参 | `>>> [rev-toolbox 2026-10-30]` 段标记 + 行内 `[rev-toolbox]` |
| `tests/smoke_rev.py` | 新文件（独立 runner，15 用例，夹具全内造：最小 ELF64×2 / .pyc / 伪 PyInstaller 含 PYZ） | 文件头 `[rev-toolbox 2026-10-30]` |
| `config/agent.json` | rev 角色块 5 处（见 §3） | JSON 不能注释，以本文件 §3 为准 |
| `docs/rev-toolbox-plan.md` | 本文件 | — |

**未触碰**：crew.py / solver.py / tools_local.py / tools_misc_ext.py / method_cards.py / tests/smoke.py / roles/* / Dockerfile（R2 才动）。

## 2. 新能力（全部挂在既有 elfpeek 工具名下，零注册改动）

| view | 能力 | 引擎 |
|---|---|---|
| `disasm` | 反汇编（offset/length/count/arch；vaddr 标注 + 最近符号注释 + PIE 提示；偏移自动吸附可执行段；超界报错） | capstone（v4 镜像）；缺失退化字节视图，不报错 |
| `bytecode` | .pyc：magic 校验 + marshal + dis + 常量池（关键词优先）；PyInstaller：CArchive 目录/提取（含 ELF bootloader 变体）、PYZ 模块提取（`pyz:名`）、`func=` 只看某函数；b/x 条目解压落盘 | 纯 stdlib（marshal/dis/zlib） |
| `gadgets` v2 | 真实指令边界枚举（深度≤5，ret 终点校验）、`filter=rdi\|rsi\|rdx\|syscall\|binsh`、/bin/sh 全部偏移、PIE 提示、4s 超时保护 | capstone；缺失保留旧 5 模式盲扫（行为不变） |
| `strings` | `utf16=true` 扫 UTF-16 LE/BE 宽字符串（任意文件，不要求 ELF） | 纯 stdlib |
| `info` | 补"入口段"行：入口所在 PT_LOAD 与可执行性（UPX/壳的快速线索） | 纯 stdlib |
| `relocs` | **R3 新增**：重定位表按节分组，JUMP_SLOT 的 `@地址` 即 `foo@got`（节内顺序=PLT stub 顺序）、GLOB_DAT 数据导入、RELATIVE 的 addend=文件内 vaddr；无重定位表时明示"静态链接" | 纯 stdlib |
| `dynamic` | **R3 新增**：NEEDED 依赖库、RUNPATH/RPATH、PLTGOT/INIT/FINI/INIT_ARRAY、BIND_NOW（→full RELRO）、DT_FLAGS/FLAGS_1 位解码（含 PIE 位） | 纯 stdlib |

capstone 通过 `importlib.import_module` 动态加载（`_capstone_for` 内有说明）：`case_stdlib_only` 静态扫描禁止第三方 import（v3 镜像无 pip），动态加载缺失时返回 None 走退化路径，永不 ImportError，契约不破。

## 3. config/agent.json 的 rev 块精确 diff（5 处）

```jsonc
// rev.max_steps: 12 → 14
// rev.platform: ["read","instance"] → ["read","instance","oracle"]   // verify_answer 白试
// rev.local:    追加 "hexpeek"（提示词【1.判型】本来就引用 hexpeek）
// rev.system 开头: "elfpeek（ELF 解析）" → "elfpeek（ELF 解析 + disasm 反汇编 + bytecode 处理 .pyc/PyInstaller）、hexpeek（十六进制切片）"
// rev.system 步数行: "只有 12 步：1 取料/1 判型/4 静态/2 推演/1 验证/1 收口" → "只有 14 步：1/1/5/3/1/1"
// rev.system 静态段: 删"**没有反汇编器**…人工核对"整段 → 改为 view=disasm / view=bytecode / gadgets filter 用法 + "先 symbols/strings 定位再 disasm 局部"
// rev.system 判型行: ".pyc/脚本先反编译或读字节码" → ".pyc/PyInstaller 直接 elfpeek view=bytecode"
```

## 4. 验收记录（2026-10-30）

| 检查 | 结果 |
|---|---|
| `python -m compileall src tests` | exit 0 |
| `python tests/smoke_rev.py`（无 capstone，退化路径） | **15/15 PASS** |
| `python tests/smoke_rev.py`（PYTHONPATH 挂 capstone 5.0.3 wheel，真实引擎） | **15/15 PASS** |
| `python tests/smoke.py` 全量回归 | **144 passed / 0 failed**（基线随并行会话扩到 144，无回归） |
| config JSON 合法性 + rev 块断言（14 步/oracle/hexpeek/disasm/bytecode/pyz 提示词就位、"没有反汇编器"清零） | 全部通过 |

本机真实引擎验证方式：wheel 解压到 `runs/_tmp_capstone/pkg`（不入库、不进镜像），`$env:PYTHONPATH="src;runs\_tmp_capstone\pkg"`。

## 5. 未做 / 待批（R2+）

1. **capstone 进镜像**（Dockerfile `pip install capstone==5.0.3` 后照旧删 pip，约 +4–6MB）——
   **2026-11-02 已写入 Dockerfile（R2 完成，v5 生效）**；v4 当年漏做，见 §6.1。
2. ~~pwn 方案~~ → **已完成**，见 `docs/pwn-toolbox-plan.md`（pwngen/pwnfind/lookup，同期交付）。
3. ~~crypto 方案~~ → **已完成**，见 `docs/crypto-toolbox-plan.md`（rsatool/aesblock/prng，**零镜像依赖**，无需 z3/gmpy2 体积代价）。
4. rev 方法卡 3–5 张（method_cards.py 现由并行会话占用）。
5. 真机验收 A3（8 道 stalled reverse 里挑 3 道复跑，目标 ≥1 翻盘）——需模型调用预算，单独申请。
6. 代码审计（2026-10-30 第二轮）：10 处问题全修、elfpeek 派发层整数参数加固（支持 0x 前缀 offset）；详见 `docs/toolbox-audit-2026-10-30.md`。

## 6. 补记（2026-11-02）：R2 补做 + R3 两视图

触发：评估 `ctf-forge`（HI Cyber Con 2026 单类别 ELF 逆向工作台）时发现，它的 rev 之所以"强"，
第一层原因不是工具多（它只有 9 个工具、capstone 只解 x86 前 128 条指令），而是**镜像里真的装了它
宣传的东西**：pyelftools + capstone + objdump + gdb + strings。对照之下我们这边有个更难看的问题。

### 6.1 R2 漏做（v4 没有 capstone）——承诺与实现不一致

| 位置 | 当时写的 | 实际 |
|---|---|---|
| `Dockerfile` | 只有 ffuf / SecLists / sqlmap 三段 | **无任何 capstone 安装步骤**，基础镜像 `python:3.12-slim` 也不带 |
| `tools_domain_ext.py:369`（注释） | "capstone 为可选引擎（镜像 v4 起内置）" | 假 |
| `_view_disasm` 退化提示 | "本环境无 capstone（v4 镜像起内置）" | 假 |
| `_capstone_for` docstring | "capstone 到 v4 镜像才内置" | 假 |
| `config/agent.json` rev/pwn 提示词 | "v4 镜像含 capstone" | 假 |
| `HANDOFF` 缺口 #12 | "capstone 未进镜像" | **真**——只有这一处说了实话 |

后果不是"少个功能"：rev 专家按提示词调 `view=disasm`，拿到的是字节视图 + 一句"本环境无 capstone"，
14 步预算里白烧 1–2 步，而且会误判成"工具坏了"。**这比根本没有该功能更糟**——提示词在骗模型。

修法（已落地）：Dockerfile 在"删 pip"那段**之前**加 capstone 安装，版本 + sha256 双钉死，
`--require-hashes` 强制校验，构建期跑真实反汇编自检（`push rbp; mov rbp,rsp` 两条指令）。
三处注释/提示语改成"v5 起内置；v4 及以前没装"。许可义务记入 `THIRD_PARTY_NOTICES.md §3.5`（BSD-3-Clause）。

### 6.2 R3：relocs / dynamic 两个视图（纯 stdlib）

借鉴点来自 ctf-forge 的 `inspect_elf`（pyelftools 返回结构化 JSON，含 `relocations` 与 `dynamic`），
**实现独立自研**（它 AGPL-3.0，只取字段语义，未复制源码）。

| 视图 | 解决的问题 |
|---|---|
| `relocs` | 之前完全没有重定位解析：判断"静态还是动态"、"哪个 import 对应哪次调用"、"GOT 在哪"只能靠 `shell` 手写 python。现在 `.rela.plt` 直接给出 `printf → @0x601018` 这种 `foo@got` 映射，节内顺序就是 PLT stub 顺序 |
| `dynamic` | 之前没有 `.dynamic`：NEEDED（链接哪个 libc）、RUNPATH/RPATH（随题分发的 .so 在哪）、BIND_NOW（full RELRO ⇒ GOT 只读，别想 GOT 劫持）、FLAGS_1 的 PIE 位，全部要手解 |

顺带修掉一个 32 位老 bug：`security()` 的 32 位分支把 `Elf32_Phdr` 解成 (vaddr, paddr, filesz)
却按 (vaddr, filesz, flags) 赋值 → **32 位 ELF 的 NX 恒判 True**（可执行栈也报 NX=True）。
`PT_GNU_STACK` 的 flags 从未被读到。已改为按完整 8 字布局解包，并加回归用例 `case_elf32_nx_flags`。

### 6.3 验收（2026-11-02 实测）

| 检查 | 结果 |
|---|---|
| `python tests/smoke_rev.py` | **19/19**（原 15 条 + 新增 4 条：relocs / dynamic / 静态链接提示 / 32 位 NX） |
| `python tests/smoke_pwn.py` | 24/24 |
| `python tests/smoke.py` 总入口 | 160/0（视图是纯增量，未触碰既有路径） |
| 真实 ELF 冒烟（`misc-bank/picture__lsb-2016HCTF/files/1`，30 节动态链接） | 正确列出 3 条 JUMP_SLOT（`printf@0x601018` / `__libc_start_main@0x601020` / `__gmon_start__@0x601028`）、1 条 GLOB_DAT、NEEDED=libc.so.6、INIT/FINI/INIT_ARRAY |

### 6.4 仍未做（需要模型预算或另开决策）

1. **rev/pwn 角色提示词补两行**（把 relocs/dynamic 写进"静态四步"流程）——工具 schema 已可见，
   但不写进提示词的话模型不会主动想到用；按 H.8 提示词属资产，**等批准**。
2. gdb 批处理工具（ctf-forge 的 `-nx -nh -batch -iex "set auto-load off"` 防坑参数集）——
   gdb 进镜像约 +30MB，与静态可解的收益不成比例，暂不采纳。
3. A3 真机验收（8 道 stalled reverse 挑 3 道复跑，目标 ≥1 翻盘）——需模型调用预算。
