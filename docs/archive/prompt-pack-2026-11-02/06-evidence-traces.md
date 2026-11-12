# 06 · 实跑证据：模型实际怎么用这两个角色

数据来源：`runs/*/transcripts/*.jsonl`（538 份完整对话转录，含工具调用与原始回执），
筛出 `role in {rev, pwn}` 的全部会话逐条统计。**这些数字不是估计，是可复算的。**

> ⚠️ 两点口径说明，读之前先知道：
> 1. 这些是**本地 Windows 上的开发跑**，不是容器内跑。容器里没有 capstone（v4），所以
>    "无 capstone"的真实比例只会比下面更高，不会更低。
> 2. pwn 的 12 次会话里有 9 次死在"实例需 VPN / `*.internal` 不可达"上，**不能当能力样本**。
>    pwn 的结论只能从"离线二进制"那一类里看，样本量个位数。

## 一、总览

| 指标 | rev | pwn |
|---|---|---|
| 会话数 | **63** | 12 |
| 步数分布 | 12 步 21 次、14 步 19 次、8 步 5 次、其余零星 | 12 步 6 次、14 步 2 次 |
| 步数用满（≥12）| **40 / 63（63%）** | 9 / 12 |
| 用过 `elfpeek` 的会话 | **24 / 63（38%）** | **1 / 12** |
| 用过 `shell` 的会话 | 61 / 63 | 11 / 12 |
| 用过 `write_file` 的会话 | 25 / 63 | 4 / 12 |
| 撞上"本环境无 capstone" | **16 / 63 会话，共 33 条消息** | 0 |

## 二、最刺眼的一条：工具被冷落

rev 角色 63 次会话的工具调用合计：

```
shell                619   ← 压倒性
write_file           208
elfpeek              106   ← 提示词里"静态四步"的主角，只占 shell 的 1/6
start_instance        58
report                38
...
hexpeek               13
```

`elfpeek` 被调用时的视图分布：

```
info 33 | disasm 25 | strings 21 | symbols 12 | sections 11 | lookup 4
```

**结论**：提示词第 2 节写着「静态：info → symbols → strings → gadgets → disasm → bytecode」，
但模型**只有 38% 的会话愿意用它**，其余全靠 `shell` 里手写 python 硬解。

### 实证：模型在手写 ELF 解析器（等于重造 `view=sections`）

CYBENCH-014 的 rev 专家第一步就是：

```python
python -c "open('s1.py','w').write('''
import struct,re
d=open("nettools.bin","rb").read()
shoff=struct.unpack_from("<Q",d,0x28)[0]
shentsize=struct.unpack_from("<H",d,0x3a)[0]
...''')"
```

`shoff` 就是 e_shoff、`shentsize` 就是 e_shentsize —— 这是在**用 20 行代码重新实现 `view=sections`**。
同样的会话里 `elfpeek` 一次都没调。

为什么？三个可验证的原因：

1. **要一次扫全量**。`view=strings` 默认 `limit=60`、`min_len=4`，返回的是"前 60 条"；
   而题目要的是"在 1 MB 二进制里找散落常量"，模型需要的是**自己控制过滤条件**。
   工具的 `limit` 上不去（视图内部会裁剪），模型就用 `re.findall` 自己扫。
2. **要拿到可编程的中间结果**。工具输出是给人看的格式化文本；模型要拿它做下一步计算时，
   得先自己解析回来。不如直接 `print(hex(off), s)` 拿结构化结果。
3. **步数成本**。`elfpeek` 一次只能看一个视图；一个 1 MB 二进制要走
   info → sections → strings → symbols → disasm 就是 5 步，占 14 步预算的 36%。
   而一条 `python3 scan.py` 一次把所有视图都算完，只花 1 步。

> **这是提示词层面最该修的一件事**：不是"再强调一遍用 elfpeek"（第 2 节已经强调了），
> 而是要**明确分工**——"单点、小文件、要精确结论时用 elfpeek；全量扫描、批量过滤、要可编程结果时
> 就写脚本，别硬拆成 5 次 elfpeek"。现在的提示词把所有场景都推给 elfpeek，模型权衡后选择不听话。

## 三、capstone：一个已被证实的"承诺与实现不符"

16 个会话、33 条消息里模型看到：

```
本环境无 capstone（v4 镜像起内置）——退化为字节视图；优先走 symbols/strings 推理，或用 python3 按已知指令编码人工核对：
文件偏移 0xe0a0 起 320 字节（vaddr 0xe0a0）：504889f2488d05cd8405...
```

注意这句话本身自相矛盾：**"v4 镜像起内置"** 但又说 **"本环境无 capstone"**。模型读到的是
"这东西本该有，现在是坏的"——于是它开始修环境。

CYBENCH-004 的 rev 专家，第 1、2、4 步全花在这上面：

```
step 1  python -m pip install capstone 2>&1 | tail -20
step 2  python -m pip install capstone
step 4  python -m pip install capstone 2>&1
        → ERROR: Could not install packages due to an OSError: [Errno 13] Permission denied
```

**3 步 / 14 步，21% 的预算，花在一个不该存在的环境问题上。**

当前处置（已完成，未提交）：`Dockerfile` 已加 capstone 5.0.3（sha256 钉死 + 构建期自检），
三处"v4 起内置"的错误表述已改为"v5 起内置；v4 及以前没装"。**提示词里这两句仍是旧的**，正是本包要改的内容。

## 四、write_file 的规定与实际行为背离

`write_file` 的工具描述写着：

> 写脚本/载荷/字典文件一律用它，**不要用 shell 的 `echo`/heredoc/`python -c` 拼** ——
> Windows 与 Linux 的引号规则不同，拼接长脚本极易写坏。

实际：rev 会话里 `python -c` 出现 **130 次**，`write_file` 只有 25 个会话用过。
而且大量 `python -c` 的内容就是 `open('x.py','w').write(...)` —— **用禁用的方式写文件**。

原因很直白：`write_file`（1 步）+ `shell` 跑（1 步）= 2 步；`python -c "open(...).write(...)"` = 1 步。
14 步预算下，模型必然选省一步的那个，**哪怕它更容易被引号坑**。

> 顺带一个真实代价：这些会话里出现过 `[工具被拒绝] Windows cmd 不认单引号，用双引号`、
> `[工具被拒绝] Windows shell 无 tail`。省下的那一步，有时又赔回去。

## 五、pwn：样本被环境问题污染

12 次会话的工具调用：

```
shell 102 | netcheck 33 | start_instance 18 | tcp 18 | ... | elfpeek 1
```

`netcheck` 33 次、`elfpeek` 1 次 —— 这不是"pwn 不会静态分析"，是**大部分题根本连不上**。
11-03 报告的口径确认：pwn 池 4 道，其中 3 道实例是 `*.internal`（需 VPN），只有 1 道离线可跑。

所以对 pwn 提示词的改动，**只能做"结构性"改进（分工、步数分配、vaddr/file-offset 混淆），
不能做"针对失败模式"的调优**，因为失败模式数据不存在。

## 六、一个 v5 上线才会爆的坑（现在还没数据，但可预测）

`view=disasm` 的 `offset` 参数是**文件偏移**，不是 vaddr。代码里有段吸附逻辑：
不在可执行段内会吸附到最近的可执行段并加一句提示；但**超出文件长度会直接报错**：

```
offset=0x400440 超出文件长度 0x213d（先 sections 视图看范围）
```

而 `view=symbols` 输出的 `value` 列是 **vaddr**、`view=info` 报的 `entry` 也是 **vaddr**。
ET_EXEC 的二进制里，vaddr 0x400440 对应的文件偏移是 0x440 —— 差一个数量级。

**v4 镜像里没有 capstone，退化路径不做范围校验，所以这个坑现在不发作。**
一旦 v5 装上 capstone，模型拿着 symbols 给的 0x400440 直接喂 `offset`，就会开始撞这个错，
而错误信息只会说"超出文件长度"，不会说"你传的是 vaddr，我要的是文件偏移"。

> 这是**必须在 v5 上线前写进提示词**的一条：`disasm` 的 `offset` 是文件偏移，
> 从 `sections` 视图的 `offset` 列取，不要拿 `symbols` 的 `value` 或 `info` 的 `entry`。

## 七、复算方法

```bash
python - <<'PY'
import glob, json, io, collections
files = sorted(glob.glob('runs/*/transcripts/*.jsonl'))
for role in ('rev','pwn'):
    n=0; tools=collections.Counter()
    for f in files:
        mine=[json.loads(l) for l in io.open(f,encoding='utf-8') if l.strip()]
        mine=[d for d in mine if d.get('role')==role]
        if not mine: continue
        n+=1
        for r in mine:
            for tc in (r.get('tool_calls') or []): tools[tc.get('name')]+=1
    print(role, n, dict(tools.most_common(6)))
PY
```
