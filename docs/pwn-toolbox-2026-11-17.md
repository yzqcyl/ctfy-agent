# PWN 工具箱补缺（2026-11-17）：pwndbg-lite / one_gadget / FSOP·堆模板 / 导航工具面

承接 [`docs/open-source-ctf-agents-2026-11-17.md`](open-source-ctf-agents-2026-11-17.md)：整篇移植 EnIGMA 的
pwn/rev ACI 之后，还差四类工具。这里按「镜像依赖 / 纯脚本 / 知识模板 / 真接不了」分类给方案，
并说明本轮已落地哪一部分。

## 0. 结论表

| 缺口 | 现状 | 补法 | 本轮 |
|---|---|---|---|
| **pwndbg**（gdb 增强：vmmap/telescope/heap bins/cyclic） | 镜像只有 gdb 批处理，无 pwndbg（pip 第三方 + 体积） | 写**纯 gdb Python 扩展** `tools/linux_bootstrap/gdb_pwn.py`（pwndbg-lite）：`pwnrecon`/`pwnmaps`/`pwnoff`/`pwnstack`/`pwnchunks`/`pwnio`/`pwnbins`；`gdb -batch -q -x gdb_pwn.py -ex run -ex pwnrecon ./poc < payload.bin` | ✅ 已落地 + 离线校验 cyclic 偏移反查 |
| **one_gadget**（libc 里 execve("/bin/sh") 的现成 gadget） | 没有 ruby/gem，也不该在镜像里装 one_gadget | 两条路：①`pwnkit(op=ret2libc)` 已给 system/puts//bin/sh + gadget，覆盖 95% 栈题；②`pwnrop` 搜 `pop rdi; ret` 组合；③真需要 one_gadget 时**按 libc 版本查表**（离线字典放 `tools/linux_bootstrap/one_gadget_db.json`，第一版只放常用 2.27/2.31/2.35，标注来源与约束） | ⏳ 方案定，字典未建（需按版本核对，避免编错） |
| **FSOP / 堆模板** | `pwnkit` 只有 libc/ret2libc | 模板是「知识 + 偏移表」：house of apple 2（`_IO_wfile_overflow`）、`_IO_str_overflow`、tcache poisoning、`__free_hook`（<2.34）、`_IO_list_all` 链。写成 `docs/pwn-heap-fsop-recipes.md`（含 glibc 2.31/2.35 的 vtable 偏移表）+ `pwngen` 只负责拼字节 | ⏳ 方案定，文档与偏移表待建 |
| **re-mcp 式导航**（xref / 调用图） | xref 已补（2026-11-17） | `elfpeek view=xrefs, target=地址/符号`：线性扫描可执行段收集 call/jmp/lea/mov 命中 + 数据区 8/4 字节指针；capstone 缺失给降级提示 | ✅ 已实现（`tests/smoke_rev.py case_xrefs`）；调用图 view=calls 仍未做 |
| 反编译器 | **已有**（Ghidra headless `ghidra_decompile`，见 `docs/ghidra-rev-2026-11-17.md`） | 不需要补 | ✅ 已有 |

## 1. pwndbg-lite：`tools/linux_bootstrap/gdb_pwn.py`

为什么自己写而不装 pwndbg：镜像删了 pip、无网络；pwndbg 依赖 capstone/pygments 等第三方包，
体积与维护成本都不划算。`gdb_pwn.py` 是**纯 gdb Python**（`import gdb` 之外零依赖），
覆盖崩溃现场最常用的 7 个动作：

```bash
# 崩溃后一条命令出「映射 + 寄存器 + 栈顶」
gdb -batch -q -x tools/linux_bootstrap/gdb_pwn.py -ex run -ex pwnrecon ./poc < payload.bin
# 反查偏移（pattern 覆盖 RIP/RSP/栈上返回地址）
gdb -batch -q -x tools/linux_bootstrap/gdb_pwn.py -ex run -ex pwnoff ./poc < payload.bin
# 堆现场 / FSOP 目标
gdb -batch -q -x tools/linux_bootstrap/gdb_pwn.py -ex run -ex pwnchunks -ex pwnio ./poc
```

| 命令 | 作用 |
|---|---|
| `pwnrecon` | 映射（末 2.5KB）+ 关键寄存器 + 栈顶 12 个 qword |
| `pwnmaps [pat]` | 过滤映射；给出 libc 基址提示 |
| `pwnoff [reg...]` | pwntools 兼容 cyclic 反查：寄存器值/栈上 pattern → 偏移 |
| `pwnstack [n]` | 栈顶 n 个 qword |
| `pwnchunks [n]` | 从 `[heap]` 起点走 chunk：size/flags/fd/bk |
| `pwnio` | `_IO_2_1_stdout_` / `_IO_list_all` / vtable 符号地址；无符号时给 2.31/2.35 偏移提示 |
| `pwnbins` | 有 libc 符号时打 `main_arena` fastbins；strip 时提示退化方案 |

离线校验（不依赖 gdb）：`runs/t_gdbpwn.py` 用同一套 cyclic 算法验证 `aaaabaaacaaadaaa` 前缀、
`off+8=8`、`off+16=16`、非 pattern 返回 -1。

## 2. one_gadget：为什么先不硬编码

one_gadget 的价值是「libc 版本已知时省一条 ROP 链」，风险是**版本对不上直接打空**。
方案：`tools/linux_bootstrap/one_gadget_db.json` 按 build-id/版本号存 `{version: [ {offset, constraints} ]}`，
第一版只收 2.27/2.31/2.35 三个常见版本（来源写成注释：one_gadget 仓库 + 本地 libc 校验），
`pwnkit(op=one_gadget, path=libc, build_id=...)` 查表并明确标注「未校验版本」。
在只有 ret2libc 可用的题里，`pwnkit(op=ret2libc)` 已经覆盖，不要为 one_gadget 引入 ruby。

## 3. FSOP / 堆模板：写成可核对的中文配方

`docs/pwn-heap-fsop-recipes.md`（待建）按「触发条件 → 需要的原语 → 具体偏移/结构体 → 失败模式」组织：

- **tcache poisoning**：`free(a); free(a)` → 改 `a->fd` → `malloc`×2 → 任意地址写；2.32+ 有 safe-linking（`fd = target ^ (chunk>>12)`）。
- **`__free_hook`**（< 2.34）：`free` 前写 `system`，再 `free("/bin/sh")`。
- **`_IO_list_all` / house of apple 2**（2.35+）：`_IO_wfile_overflow` + `_wide_data` 链，需 `_IO_wfile_jumps` 偏移。
- **`_IO_str_overflow`**（2.27~2.31）：`_IO_str_jumps`，`fp->_allocate_buffer` = system。
- 每个配方附「怎么用 gdb_pwn 的 `pwnchunks`/`pwnio` 验证」和「libc 版本差异导致失败的排查点」。

不直接改 `pwnkit` 的原因：模板本质是「payload 布局 + 偏移表」，用 `pwngen` 拼 hex 即可；
塞进 `pwnkit` 会让这个纯 stdlib 工具依赖 libc 版本表，维护成本高于收益。

## 4. re-mcp 式导航：`elfpeek view=xrefs`（2026-11-17 已实现）

re-mcp 的核心不是「反编译」，而是**按引用关系导航**（`decompile_function` + `xrefs` + 调用图）。
我们已有 `ghidra_decompile --function`，缺的是「谁调用了这个函数/哪引用了这个字符串」。
已实现：`elfpeek view=xrefs, target=0x401234`（或符号名）——线性扫描可执行段（失败前进 1 字节，上限 1MB / 20 万条指令）收集 `call/jmp/lea/mov` 的立即数与内存位移命中，同时扫数据区 8/4 字节指针；capstone 缺失时给中文降级提示。`view=calls`（调用图）仍未做。收益：rev 大二进制不必全文读汇编，pwn 也能快速找
`read/strcpy` 的调用点——这正是 EnIGMA/PwnAI 强调的「定点读」。

## 5. 优先级建议

1. **`elfpeek view=xrefs`**（rev/pwn 都受益，纯 Python，可在宿主机测）——下一步最值得做；
2. `docs/pwn-heap-fsop-recipes.md` + `one_gadget_db.json`（纯知识/数据，零运行风险）；
3. `gdb_pwn.py` 已在镜像打包清单里，需在真实容器里跑一次冒烟（本轮宿主机无 gdb，只做了离线校验）。
