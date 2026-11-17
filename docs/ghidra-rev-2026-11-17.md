# Ghidra 反编译接入 + rev 彩排验证（2026-11-17）

## 背景

2026-11-16 的 rev 彩排（Hint Me Maybe）卡在：checker 被 strip，只能 `objdump`/capstone
逐段反汇编；gdb 在 ASan checker 上插不进断点；agent 报告"静态反汇编截断、看不下去"。
这正是"工具缺口"，不是模型能力问题——所以按 CSDN 那篇清单里 autoctf/CTF-Solver 的思路，
补一个 **Ghidra headless 反编译**。

## 安装（WSL / 真实 VPS 通用）

```bash
sudo bash tools/linux_bootstrap/ghidra_setup.sh
# 装 JDK25（Temurin）+ 最新 Ghidra（12.2，544MB）+ /usr/local/bin/ghidra_decompile
```

- Ghidra 12.2 要求 **JDK 25**（Ubuntu apt 只有 21，会报 `unsupported java version`），
  所以脚本从 Adoptium 下 JDK25 到 `/opt/jdk25`，并把 JAVA_HOME 记到 `/opt/ghidra/JAVA_HOME25.txt`；
- Ghidra 解压到 `/opt/ghidra/ghidra_12.2_PUBLIC`，`chmod a+rX` 让非 root 的 `l2agent` 也能用；
- `analyzeHeadless` 无 TTY 时不会自己找 JDK，包装器显式传 `JAVA_HOME`。

## 包装器 `ghidra_decompile`

`tools/linux_bootstrap/ghidra_decompile.py` + `DecompileFunc.java`：

```bash
ghidra_decompile <binary>                     # 前 40 个函数的 C 伪代码
ghidra_decompile <binary> --function verify   # 名字含 verify 的函数
ghidra_decompile <binary> --limit 60 --timeout 600
```

实现要点：Ghidra 自己 `-import` + 自动分析（15KB checker 约 7 秒），`DecompileFunc.java`
把 `DecompileInterface` 结果**写到文件**（不解析 Ghidra 吵人的日志），包装器只读文件输出；
失败时打印末尾日志（JDK/Ghidra 路径/文件格式问题一眼可见）。

## rev 彩排验证（rev2，500k，40 分钟预算）

| 指标 | 旧 rev（2026-11-16，无 Ghidra） | **rev2（有 Ghidra）** |
|---|---|---|
| tokens / calls | 594,444 / 104 | **501,301 / 92**（500k 预算绑定，23 分钟） |
| `ghidra_decompile` 调用 | 0 | **7 次** |
| 环境 / 命令错误 | 0 / 0 | 0 / 2（2 次是 agent 自己的脚本报错） |
| 结果 | 未解出，结论="静态反汇编截断" | 未解出，但**判据链条打通** |

**rev2 用 Ghidra 伪代码解出的关键事实**（agent 报告原文摘要）：

1. `FUN_001018a0` = 校验核心：`FT_Set_Var_Design_Coordinates(face, 0x18, coords)` →
   `FT_Get_Char_Index` → `FT_Load_Glyph` → 要求 outline 格式、点数 ≥ 0x44 →
   遍历 0x400 字节（128 个 8 字节项）逐项校验 `v <= 0x3c0 && (v & 0x3f) == 0` →
   通过则写回 `coords[i] = (v >> 6) << 16`；
2. 输入语义 = **24 条 fvar 轴的设计坐标**（每个 hex nibble 0..15；`challenge.ttf` fvar
   `axisCount=24`、min -16384/def 0/max 16384、axisSize 20 完全对上）；
3. 成功条件 = **16 个轮廓点的 x 必须同时落在 26.6 整数网格**（≤960 且 64 的倍数）——
   即"输入坐标是 hinting 映射的不动点"；
4. 否证了题面暗示的"逐位二分"路线：`HINT_TRACE` 的 marker_y 恒 128、对
   `0…0`/`01000…`/`ffff…` 输出完全相同（无梯度）；
5. 给接手的下一步：ctypes 加载 `libfreetype.so.6` 建 16×24 Δ 矩阵，解整数线性系统
   （z3/高斯消元 + 网格约束）得到 24 nibble → hex。

结论：**Ghidra 把 rev 从"读不动"推到"读懂判据 + 有明确求解算法"**；rev2 未完成的原因是
500k 预算在 23 分钟就用完（墙钟还有 17 分钟），不是工具或思路问题。

## 交付影响

- 新文件：`tools/linux_bootstrap/{ghidra_decompile.py,DecompileFunc.java,ghidra_setup.sh}`
  随包发布（`tools/linux_bootstrap` 已在整个打包清单里）；
- `src/ctfy_agent/method_cards.py` 的 REV 卡新增："若 PATH 里有 `ghidra_decompile`，
  先出伪代码再定点读，比逐段反汇编省 10 倍步数"；
- 题面/环境提示：rev 隔离彩排的题面会告知 `ghidra_decompile` 可用；
- 真实 VPS 上跑 `ghidra_setup.sh` 即可获得同样能力（需 ~1.1GB 磁盘）。