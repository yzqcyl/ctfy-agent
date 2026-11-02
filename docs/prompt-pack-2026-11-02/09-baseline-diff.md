# 09 · 我方基线方案（可直接采纳，也可作为参照起点）

这不是"最终答案"，是我们根据 `06` 的证据能做的最小改动。外包方可以完全推翻，但**至少要知道我们试过什么、
为什么这么改**，避免重复提同样的建议。

改动只有 4 处替换 + 2 处插入，都在 `roles.rev.system` 与 `roles.pwn.system` 内部，不动其他任何字段。

## rev

### R1 · 第 23 行（T1 + T2）

```diff
- view=disasm → 反汇编指定偏移（offset/length/count；v4 镜像含 capstone，无引擎时自动退化字节视图）；
+ view=disasm → 反汇编：offset 传**文件偏移**（取 sections 视图的 offset 列；别用 symbols 的 value 或 info 的
+   entry，那些是 vaddr，差一个数量级会直接报"超出文件长度"白烧一步），length/count 控范围；镜像没装 capstone
+   时自动退化成字节视图，**别去 pip install**，改走 symbols/strings 推理；
```

### R2 · 第 23 行之后插入两行（T4）

```diff
+ view=relocs → 重定位表：JUMP_SLOT 的 @地址就是 foo@got（同节内顺序＝PLT stub 顺序），表为空＝静态链接，后面不用再找 GOT；
+ view=dynamic → NEEDED 看链接哪个 libc、RUNPATH/RPATH 看随题分发的 .so 在哪、有 BIND_NOW 就是 full RELRO（GOT 只读，别想 GOT 劫持）；
```

### R3 · 第 25 行之前插入分工规则（T3，核心）

```diff
+ 分工（别把步数浪费在拆工具上）：要"某一个点"的精确结论（这个函数在干什么、有没有这个符号、这个串在哪）→
+   用 elfpeek 单视图，快且不用写代码；要"全量扫一遍"（1MB 级二进制里找散落常量、按偏移区间筛字符串、
+   批量验证同一假设）→ 直接 write_file 落个脚本 shell 跑掉，一条命令出结果，**别拆成 5 次 elfpeek**。
```

### R4 · 第 12 行尾部（T5，去冗余）

```diff
- （VPN 实例先 netcheck 判可达，DNS 失败=本机无隧道，直接 report 卡点别硬试）。
+ （VPN 实例先 netcheck 判可达；不通立刻 report 卡点，别在接入上反复试）。
```

## pwn

### P1 · 第 19 行（T1 + T2 + T4）

```diff
- 容器没有 objdump/gdb/ROPgadget；要看指令用 elfpeek view=disasm（offset/length/count，v4 镜像含 capstone，
-   无引擎自动退化字节视图）；附件带 libc.so.6 时用 view=lookup（symbol=system、search=/bin/sh，附 libc
-   版本行）直接拿 ret2libc 偏移——优先符号偏移/ret2libc 思路。
+ 容器没有 objdump/gdb/ROPgadget；要看指令用 elfpeek view=disasm（offset 传**文件偏移**，取 sections 视图的
+   offset 列，别用 symbols 的 value——那是 vaddr，会直接报"超出文件长度"；没装 capstone 时自动退化字节视图，
+   别去 pip install）；GOT 布局用 view=relocs（JUMP_SLOT 的 @地址即 foo@got），是否 full RELRO 用
+   view=dynamic（有 BIND_NOW 就是，此时别打 GOT 劫持）；附件带 libc.so.6 时用 view=lookup（symbol=system、
+   search=/bin/sh，附 libc 版本行）直接拿 ret2libc 偏移——优先符号偏移/ret2libc 思路。
```

### P2 · 第 12 行尾部（T5，去冗余）

```diff
- ...；题面写明 VPN 的实例先用 netcheck 判可达（DNS 失败=本机无隧道，立即 report 卡点，不要反复重试烧预算）。
+ ...；题面写明 VPN 的实例先用 netcheck 判可达（不通立即 report 卡点，不要反复重试烧预算）。
```

## 改完的体量

| 角色 | 改前 | 改后（估） | 变化 |
|---|---|---|---|
| rev | 2223 字符 / 41 行 | ≈ 2560 字符 / 44 行 | +15% |
| pwn | 2235 字符 / 34 行 | ≈ 2430 字符 / 36 行 | +9% |

都在 `07` 的 ±15% 容差内。

## 复验方法（改完后执行）

```bash
cd E:/ctfy_project
python tools/check_prompt_assembly.py          # 本包附带的装配检查，7 项全绿才算过
python tests/smoke.py                          # 160 passed / 0 failed
```

`check_prompt_assembly.py` 会检查：

1. `config/agent.json` 是合法 JSON；
2. `roles.rev.system` / `roles.pwn.system` 存在且非空；
3. 正文**不含** `PLAN`（否则 COMPANION_RULE 不追加）；
4. 正文**含** `evidence` 和 `report`；
5. 正文长度在 1900–2600 之间；
6. 正文里出现的工具名全部在工具面白名单内；
7. 拼装后的实际 system 里，`PLAN:` / `REFLECTION:` / 不可达纪律 三条都还在。

## 一个我拿不准、留给你判断的点

R3 那句"直接 write_file 落个脚本 shell 跑掉"是在**明确授权模型写脚本**。好处是省步数（实测它本来就在写，
只是绕成 `python -c` 更容易踩引号坑）；风险是**脚本里的字符串拼接可能引入新的失败面**，而且写脚本比调工具
更难被账本去重（同一思路换个脚本就能重跑，容易在错误方向上反复烧预算）。

如果你认为这个风险大于收益，就把 R3 的后半句改成"可以用 shell + write_file 写脚本，但同一假设最多一次脚本"。
