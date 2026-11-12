# 05 · 工具真实输出（模型看到的就是这些）

改提示词时最容易犯的错，是建议模型去要一个工具根本给不出的信息。本文件把 13 种视图的
真实返回照抄下来，**未经改写、未经美化**，包括出错时长什么样。

看的时候注意三件事：

1. **工具输出是给人看的格式化文本**，不是 JSON。模型要拿它做下一步计算，得自己再解析一遍
   —— 这是它宁可写脚本的原因之一（见 `06` 第二节）。
2. **每个视图都有 `limit`**（默认 60 条）。大二进制里"找散落常量"这类需求，工具给不全。
3. **v4 镜像里没有 capstone**，`disasm` / `gadgets` 走的是 §8 的退化路径。§7 是装了 capstone 的样子。

---

## 7. 各视图的真实输出（照抄）

样本：`misc-bank/picture__lsb-2016HCTF/files/1`（8509 字节，ELF64，动态链接，30 节，ET_EXEC 非 PIE）

> 本机装有 capstone 5.0.7，所以 disasm / gadgets 是真实反汇编。**v4 镜像里没有 capstone**，
> 那时看到的是 §8 的退化视图——这个差别是本次改动的核心背景。

### view=info

```
ELF 64 LE，machine=0x3e，entry=0x400440，节数=30，字节数=8509
类型：ET_EXEC（PIE=False）
缓解措施：NX=True  canary=False  RELRO=True
入口段：PT_LOAD vaddr 0x400000..0x400724（文件 0x0..0x724，可执行）
节：.interp, .note.ABI-tag, .note.gnu.build-id, .gnu.hash, .dynsym, .dynstr, .gnu.version, .gnu.version_r, .rela.dyn, .rela.plt, .init, .plt, .text, .fini, .rodata, .eh_frame_hdr, .eh_frame, .init_array, .fini_array, .jcr, .dynamic, .got, .got.plt, .data
符号（前 30，非 _ 开头）：printf, crtstuff.c, deregister_tm_clones, register_tm_clones, completed.6972, frame_dummy, 1.c, crtstuff.c, data_start, printf@@GLIBC_2.2.5, main
```

### view=sections

```
name                       addr     offset       size
.interp                0x400238      0x238         28
.note.ABI-tag          0x400254      0x254         32
.note.gnu.build-id     0x400274      0x274         36
.gnu.hash              0x400298      0x298         28
.dynsym                0x4002b8      0x2b8         96
.dynstr                0x400318      0x318         63
.gnu.version           0x400358      0x358          8
.gnu.version_r         0x400360      0x360         32
.rela.dyn              0x400380      0x380         24
.rela.plt              0x400398      0x398         72
.init                  0x4003e0      0x3e0         26
.plt                   0x400400      0x400         64
.text                  0x400440      0x440        386
.fini                  0x4005c4      0x5c4          9
.rodata                0x4005d0      0x5d0         41
.eh_frame_hdr          0x4005fc      0x5fc         52
.eh_frame              0x400630      0x630        244
.init_array            0x600e10      0xe10          8
.fini_array            0x600e18      0xe18          8
.jcr                   0x600e20      0xe20          8
.dynamic               0x600e28      0xe28        464
.got                   0x600ff8      0xff8          8
.got.plt               0x601000     0x1000         48
.data                  0x601030     0x1030         16
.bss                   0x601040     0x1040          8
.comment                    0x0     0x1040         36
.shstrtab                   0x0     0x1064        264
.symtab                     0x0     0x18f0       1560
.strtab                     0x0     0x1f08        565
```

### view=symbols（limit=40）

```
name                                          value type
printf                                          0x0 OBJECT
__libc_start_main                               0x0 OBJECT
__gmon_start__                                  0x0 FUNC
crtstuff.c                                      0x0 NOTYPE
__JCR_LIST__                               0x600e20 NOTYPE
deregister_tm_clones                       0x400470 NOTYPE
register_tm_clones                         0x4004a0 NOTYPE
__do_global_dtors_aux                      0x4004e0 NOTYPE
completed.6972                             0x601040 NOTYPE
__do_global_dtors_aux_fini_array_entry     0x600e18 NOTYPE
frame_dummy                                0x400500 NOTYPE
__frame_dummy_init_array_entry             0x600e10 NOTYPE
1.c                                             0x0 NOTYPE
crtstuff.c                                      0x0 NOTYPE
__FRAME_END__                              0x400720 NOTYPE
__JCR_END__                                0x600e20 NOTYPE
__init_array_end                           0x600e18 NOTYPE
_DYNAMIC                                   0x600e28 NOTYPE
__init_array_start                         0x600e10 NOTYPE
_GLOBAL_OFFSET_TABLE_                      0x601000 NOTYPE
__libc_csu_fini                            0x4005c0 OBJECT
_ITM_deregisterTMCloneTable                     0x0 FUNC
data_start                                 0x601030 FUNC
_edata                                     0x601040 OBJECT
_fini                                      0x4005c4 OBJECT
printf@@GLIBC_2.2.5                             0x0 OBJECT
__libc_start_main@@GLIBC_2.2.5                  0x0 OBJECT
__data_start                               0x601030 OBJECT
__gmon_start__                                  0x0 FUNC
…（共 41 行，此处截断）
```

### view=strings（min_len=6, limit=40）

```
字符串 40 条（优先含 flag/pass/key 等）：
__do_global_dtors_aux_fini_array_entry
GCC: (Ubuntu 4.8.2-19ubuntu1) 4.8.2
hctf{dd0gf4c3tok3yb0ard4g41n~~~}
__frame_dummy_init_array_entry
__libc_start_main@@GLIBC_2.2.5
/lib64/ld-linux-x86-64.so.2
_ITM_deregisterTMCloneTable
_ITM_registerTMCloneTable
__do_global_dtors_aux
_GLOBAL_OFFSET_TABLE_
deregister_tm_clones
printf@@GLIBC_2.2.5
_Jv_RegisterClasses
.note.gnu.build-id
register_tm_clones
__init_array_start
__libc_start_main
__init_array_end
__libc_csu_fini
__libc_csu_init
__gmon_start__
.gnu.version_r
completed.6972
__gmon_start__
_IO_stdin_used
.note.ABI-tag
.eh_frame_hdr
__FRAME_END__
.gnu.version
…（共 41 行，此处截断）
```

### view=relocs

```
重定位 4 条，分 2 节（JUMP_SLOT 的 @地址就是 foo@got；同一节内顺序 = PLT stub 顺序）：
[.rela.plt] 3 条
  printf                       JUMP_SLOT   @0x601018
  __libc_start_main            JUMP_SLOT   @0x601020
  __gmon_start__               JUMP_SLOT   @0x601028
[.rela.dyn] 1 条
  __gmon_start__               GLOB_DAT    @0x600ff8
RELATIVE 的 addend = 修正前的文件内 vaddr（重定位后 got=载入基址+addend）；GLOB_DAT 多为数据导入（stdin/stdout/环境指针）；无符号 RELATIVE 常在 .init_array，可反推构造函数。
```

### view=dynamic

```
动态段 29 条，NEEDED=1（libc.so.6）
NEEDED      libc.so.6
INIT        0x4003e0
FINI        0x4005c4
INIT_ARRAY  0x600e10
INIT_ARRAYSZ 0x8
FINI_ARRAY  0x600e18
FINI_ARRAYSZ 0x8
GNU_HASH    0x400298
STRTAB      0x400318
SYMTAB      0x4002b8
STRSZ       0x3f
SYMENT      0x18
DEBUG       0x0
PLTGOT      0x601000
PLTRELSZ    0x48
PLTREL      0x7
JMPREL      0x400398
RELA        0x400380
RELASZ      0x18
RELAENT     0x18
VERNEED     0x400360
VERNEEDNUM  0x1
VERSYM      0x400358
```

### view=gadgets（filter=binsh）

```
gadget 枚举（capstone 真实指令边界，深度≤5，只扫可执行段；filter 可换关键字）
/bin/sh                                  未找到
```

### view=disasm（offset=0x440 文件偏移, length=64, count=16）

```
反汇编 文件偏移 0x440，64 字节，vaddr 0x400440
00400440  31ed                 xor      ebp, ebp  ; <_start+0x0>
00400442  4989d1               mov      r9, rdx  ; <_start+0x2>
00400445  5e                   pop      rsi  ; <_start+0x5>
00400446  4889e2               mov      rdx, rsp  ; <_start+0x6>
00400449  4883e4f0             and      rsp, 0xfffffffffffffff0  ; <_start+0x9>
0040044d  50                   push     rax  ; <_start+0xd>
0040044e  54                   push     rsp  ; <_start+0xe>
0040044f  49c7c0c0054000       mov      r8, 0x4005c0  ; <_start+0xf>
00400456  48c7c150054000       mov      rcx, 0x400550  ; <_start+0x16>
0040045d  48c7c72d054000       mov      rdi, 0x40052d  ; <_start+0x1d>
00400464  e8b7ffffff           call     0x400420  ; <_start+0x24>
00400469  f4                   hlt        ; <_start+0x29>
0040046a  660f1f440000         nop      word ptr [rax + rax]  ; <_start+0x2a>
00400470  b847106000           mov      eax, 0x601047  ; <deregister_tm_clones+0x0>
00400475  55                   push     rbp  ; <deregister_tm_clones+0x5>
00400476  482d40106000         sub      rax, 0x601040  ; <deregister_tm_clones+0x6>
```

### view=disasm（offset=0x400440 误传 vaddr）← 这就是坑

```
[异常] DomainToolError: offset=0x400440 超出文件长度 0x213d（先 sections 视图看范围）
```

### view=lookup（symbol=printf）

```
符号 printf               vaddr 0x0          （不在文件段内：可能是导入符号，运行期解析）
```

### view=bytecode（.pyc 无 extract）

```
.pyc（头部 16 字节；与本解释器 magic 一致）
对象：<module>  变量：[]
co_names：SECRET, check
常量池（前 30，关键词优先）：FLAG{demo_constant}
dis（前 60 行）：
  0           RESUME                   0

  1           LOAD_CONST               0 ('FLAG{demo_constant}')
              STORE_NAME               0 (SECRET)

  3           LOAD_CONST               1 (<code object check at 0x0000000004255A70, file "C:\Users\Lenovo\AppData\Local\Temp\elfdemo_ardb5l67\demo_mod.py", line 3>)
              MAKE_FUNCTION
              STORE_NAME               1 (check)
              RETURN_CONST             2 (None)
```

### view=bytecode（func=check）

```
.pyc（头部 16 字节；与本解释器 magic 一致）
对象：check  变量：['x']
co_names：(空)
常量池（前 30，关键词优先）：int:4919
dis（前 60 行）：
  3           RESUME                   0

  4           LOAD_FAST                0 (x)
              LOAD_CONST               1 (4919)
              COMPARE_OP              72 (==)
              RETURN_VALUE
```

### hexpeek（offset=0, length=128）

```
1  大小 8509 B  窗口 [0, 128)
00000000  7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00  |.ELF............|
00000010  02 00 3e 00 01 00 00 00 40 04 40 00 00 00 00 00  |..>.....@.@.....|
00000020  40 00 00 00 00 00 00 00 70 11 00 00 00 00 00 00  |@.......p.......|
00000030  00 00 00 00 40 00 38 00 09 00 40 00 1e 00 1b 00  |....@.8...@.....|
00000040  06 00 00 00 05 00 00 00 40 00 00 00 00 00 00 00  |........@.......|
00000050  40 00 40 00 00 00 00 00 40 00 40 00 00 00 00 00  |@.@.....@.@.....|
00000060  f8 01 00 00 00 00 00 00 f8 01 00 00 00 00 00 00  |................|
00000070  08 00 00 00 00 00 00 00 03 00 00 00 04 00 00 00  |................|
... 还有 8381 B 未显示，可用 offset=128 继续
```

## 8. 退化形态（v4 镜像的真实观感）

> 把 capstone 拿掉后的输出，与 v4 镜像里模型看到的一致。

### view=disasm（无 capstone，offset=0x440）

```
本环境无 capstone（v5 起镜像内置；v4 及以前没装）——退化为字节视图；优先走 symbols/strings 推理，或用 python3 按已知指令编码人工核对：
文件偏移 0x440 起 64 字节（vaddr 0x400440）：31ed4989d15e4889e24883e4f0505449c7c0c005400048c7c15005400048c7c72d054000e8b7fffffff4660f1f440000b84710600055482d401060004883f80e
```

### view=gadgets（无 capstone，filter=binsh）

```
gadget/常量扫描（无 capstone 或非 x86，退化为固定模式首现扫描）：
pop rdi; ret     0x5b3（vaddr 0x4005b3）
pop rsi; ret     未找到
pop rdx; ret     未找到
pop rax; ret     未找到
syscall; ret     未找到
/bin/sh          未找到
```

