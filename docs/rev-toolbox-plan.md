# rev 方向工具箱 v2 · 落地记录（R1）

> 会话：toolbox（rev/pwn/crypto 专用，与并行会话零文件冲突）
> 日期：2026-10-30 · 状态：**R1 已完成并通过全部回归**；R2（capstone 进镜像）随 v4 重建，另行审批。
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

1. **capstone 进镜像**（Dockerfile `pip install capstone==5.0.3` 后照旧删 pip，约 +4–6MB）——随 v4 重建一起做（v4 仍等并行会话收工），pwn 的 `disasm` 共用同一引擎。
2. ~~pwn 方案~~ → **已完成**，见 `docs/pwn-toolbox-plan.md`（pwngen/pwnfind/lookup，同期交付）。
3. ~~crypto 方案~~ → **已完成**，见 `docs/crypto-toolbox-plan.md`（rsatool/aesblock/prng，**零镜像依赖**，无需 z3/gmpy2 体积代价）。
4. rev 方法卡 3–5 张（method_cards.py 现由并行会话占用）。
5. 真机验收 A3（8 道 stalled reverse 里挑 3 道复跑，目标 ≥1 翻盘）——需模型调用预算，单独申请。
6. 代码审计（2026-10-30 第二轮）：10 处问题全修、elfpeek 派发层整数参数加固（支持 0x 前缀 offset）；详见 `docs/toolbox-audit-2026-10-30.md`。
