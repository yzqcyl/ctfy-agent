# pwn 团队资产：ASan shadow 数学 + 解封/写栈模板（2026-11-17）

## 为什么做

clean-room 五轮（约 440 万 token）已经拿到「可控 8 字节任意写 + 任意读 + PIE/libc 基址」，
但卡在最后一步：**ASan 把目标地址（真实栈）的 shadow 标记为 poison，直接写会被拦**。
这是 agent 每轮都要重新摸索、且 800k 预算内做不完的环节——所以做成团队资产。

## 资产 1：`runs/_tpctf/l2_assets/asan_shadow.py`

- 公式：`shadow(addr) = (addr >> 3) + 0x7fff8000`（x86_64 64-bit ASan）；
- `shadow_addr` / `shadow_span` / `classify` / `accessible`：给地址→shadow 地址/字节区间，
  并把 shadow 字节翻译成 `accessible / partial(k/8) / stack-use-after-return / heap-use-after-free …`；
- `unpoison_plan(addr, size, read8=None)`：生成解封写计划
  - 给了 `read8` 就**读-改-写**：只清本区间覆盖的 shadow 字节，保留同组其它对象的 poison 状态；
  - 没给就整组写 0（把 64 字节地址范围内的 shadow 全清，通常也能跑）；
  - 返回 `UnpoisonPlan(writes=[(shadow_addr, value)], restore=[...])`，可直接喂给题目的任意写原语；
- 自检：`python3 asan_shadow.py` → 公式/对齐/部分区间/分类共 10+ 断言，PASS。

## 资产 2：`runs/_tpctf/l2_assets/pwn_stack_probe.py`

把「查 shadow → 解封 → 复核 → 试写并读回」固化成 `StackProbe`：

```python
from pwn_stack_probe import StackProbe
from exploit_a import Svc          # 上一轮留下的原语脚本

svc = Svc(host, port)
probe = StackProbe(read8=svc.read8, write8=svc.write8)
probe.explain()
report = probe.probe(stack_addr, size=64)
# report: {"before": ..., "unpoison": {"writes": [...]}, "verified": True, "wrote": True}
```

- `probe()` 会打印每一步：当前 poison 组、每组解封写、解封复核、`write8` 后读回是否一致；
- 用模拟内存做自检（poison 对齐地址、非对齐跨 64 字节边界两种场景），PASS。

## 实测校验（不只是单元测试）

启动真实 clean-room challenge 进程，读 `/proc/<pid>/maps`：

```
7fff7000-8fff7000 rw-p 00000000 00:00 0     # ASan shadow 映射
OK: 0x7fff8000 落在 ASan shadow 映射 0x7fff7000-0x8fff7000
```

→ 确认该二进制的 shadow offset 就是 `0x7fff8000`，公式与真实进程一致。

## 下一轮 pwn 的建议用法（C 段）

1. 用 `exploit_a.py` 的原语构造 `read8/write8`，接 `StackProbe`；
2. 对目标栈地址（从 environ/栈泄漏得到）跑 `probe()`，拿到 `verified=True, wrote=True`
   即证明"shadow 已解封 + 真实栈可写"（这一步跑通就等于拿到 RCE 钥匙）；
3. 再写 ROP/调用链（seccomp 只放行 openat/read/write，所以目标是让程序自己读 `/answers/flag`）；
4. 打远程拿 flag，报告里贴 `probe()` 输出与本地/远程证据。

## 交付

- 两个资产随包发布（`runs/_tpctf/l2_assets` 整个目录入包，隔离沙盒自动拷入）；
- 隔离 pwn 续跑题面已加入这两个资产的用法提示（见 `l2_sim_iso.py` 的继续说明）。
