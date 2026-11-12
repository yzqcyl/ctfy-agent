# pwn 方向工具箱 v1 · 落地记录（R1）

> 会话：toolbox（rev/pwn/crypto 专用，与并行会话零文件冲突）
> 日期：2026-10-30 · 状态：**R1 已完成并通过全部回归**；R2（capstone 进镜像）随 v4 重建，另行审批。
> 姊妹文档：`docs/archive/rev-toolbox-plan.md`（rev 工具箱 v2，同期完成）。

## 1. 为什么做（实测依据）

| 事实 | 来源 |
|---|---|
| pwn 历史 **14 试 0 解**（reverse 62%、crypto 21%） | `runs/**/state.json` 全量扫描 |
| 偏移探测靠模型在 shell 里手搓 python 拼串 → **撞 shell 引号/注入守卫**（SAMPLE-013 因此整题烧光 1.5M） | 10-29 实验记录 `docs/archive/experiment-budget-steps-2026-10-29.md` |
| 容器无 objdump/gdb/ROPgadget，提示词只能让模型"按已知指令编码人工核对" | config 旧 pwn 提示词 |
| ret2libc 需要 system 偏移 + /bin/sh 偏移 + libc 版本，此前只能靠 `symbols/strings` 翻页碰运气 | 工具面盘点 |

## 2. 交付物与标识

| 文件 | 改动 | 标识 |
|---|---|---|
| `src/ctfy_agent/tools_domain_ext.py` | ① `_cyclic_seq`（De Bruijn B(k,n)，pwntools 同款算法/字母表，带规模守卫）；② **pwngen**（op=cyclic 模式串 / op=build parts 拼装：hex/raw/cyclic/pad/int 小端/落盘）；③ **pwnfind**（3~8 字节反查偏移，文本/hex/倒序）；④ `_vaddr_to_off` + **elfpeek `view=lookup`**（符号精确偏移/相近建议、字符串全部出现位置、libc 版本行识别、ret2libc 要素提示）；⑤ PWNGEN_SPEC/PWNFIND_SPEC + DOMAIN_SPECS 注册 + call_domain 派发 + ELFPEEK_SPEC 扩参 | `>>> [pwn-toolbox 2026-10-30]` / `# [pwn-toolbox]` |
| `tests/smoke_pwn.py`（新） | 16 用例独立 runner：cyclic 确定性、pwnfind 往返/hex/长针/错误提示、n 规模守卫、build 组合/落盘/错误路径、lookup 符号/相近/字符串/版本、**自动注册契约**、派发 | 文件头 `[pwn-toolbox]` |
| `config/agent.json` | pwn 块 5 处（见 §3） | JSON 不能注释，以 §3 为准 |
| `docs/archive/pwn-toolbox-plan.md` | 本文件 | — |

**注册机制（零改动接线）**：`tools_domain_ext.DOMAIN_SPECS` 被 `tools_misc_ext.MISC_EXT_SPECS` 聚合 → `LocalTools.specs()` 暴露 → `crew._role_specs` 按角色 `local` 名单过滤；`LocalTools.call` 用 `_DOMAIN_NAMES` 集合动态路由、`path` 参数自动锚定沙盒。**未改 crew.py / solver.py / tools_local.py / tools_misc_ext.py**。

## 3. config/agent.json 的 pwn 块精确 diff（5 处）

```jsonc
// pwn.max_steps: 12 → 14
// pwn.platform: ["read","instance"] → ["read","instance","oracle"]   // verify_answer 白试
// pwn.local: 追加 "hexpeek" / "pwngen" / "pwnfind"
// pwn.system 开头: 工具清单加 elfpeek(disasm/lookup)、hexpeek、pwngen、pwnfind
// pwn.system 步数行: "只有 12 步：1 取料/3 静态/2 偏移/2 打通/1 取旗/1 收口" → "只有 14 步：1/3/2/3/1/1"
// pwn.system 静态段结尾: 删"用 python3 读字节人工核对" → 改为 disasm + lookup(symbol=/search=/libc 版本) 指引
// pwn.system 【2.定位偏移】: 手搓 python 二分 → pwngen cyclic → pwnfind 反查
// pwn.system 【3.打通】: 新增"payload 用 pwngen op=build 组装（绕开 shell 引号守卫）"
```

## 4. 验收记录（2026-10-30）

| 检查 | 结果 |
|---|---|
| `python -m compileall src tests` | exit 0 |
| `python tests/smoke_pwn.py` | **16/16 PASS** |
| `python tests/smoke_rev.py`（同期 rev 回归） | 15/15 PASS（双引擎） |
| `python tests/smoke.py` 全量回归 | **144 passed / 0 failed** |
| 手工验证 | `pwngen op=build` 拼装 24B 正确（cyclic8 + pad4 + p64(0x401000) + deadbeef）；`pwnfind` 自洽偏移命中 |

## 5. 未做 / 待批（R2+）

1. **capstone 进 v4 镜像**（`pip install capstone==5.0.3` 后照旧删 pip，约 +4–6MB）——rev 的 `disasm` 与 pwn 的链条分析共用，随 v4 重建一起做。
2. 真机验收：从 14 道 stalled pwn 里挑 2–3 道复跑（1.2M/14 步），目标 ≥1 翻盘；需模型预算，单独申请。
3. 动态调试（gdb/qemu）明确超范围——重、非 stdlib；用 disasm + 算法复现 + 差分对比替代。
4. crypto 方案 → **已完成**，见 `docs/archive/crypto-toolbox-plan.md`（rsatool/aesblock/prng，纯 stdlib 零镜像依赖）。
5. **代码审计（2026-10-30 第二轮）已完成**：10 处问题全修（含 1 个高危缓存串号 bug），测试 16→24；详见 `docs/archive/toolbox-audit-2026-10-30.md`。