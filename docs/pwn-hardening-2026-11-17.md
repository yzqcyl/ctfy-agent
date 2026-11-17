# PWN 硬化（2026-11-17）：确定性计算工具 + 团队资产入包

> 背景：PWN 是当前最大短板——真实外部题 0/4、BUUCTF 120 题里 3/31、L2 隔离彩排 5 轮 ≈440 万 token
> 仍未出 flag。复盘（`docs/solving-record-2026-11-17.md`）里卡点不是「不会写 exploit」，而是三层机械工作
> 每轮重造：**libc 偏移手算、泄漏后基址推导、ASan shadow 解封**；再加上团队资产只躺在 gitignored 的
> `runs/_tpctf/l2_assets/` 里（一次空间清理就会消失，也进不了镜像）。

## 1. 这轮做了什么

| # | 改动 | 文件 | 为什么 |
|---|---|---|---|
| 1 | 新增 `pwnkit` 工具（纯 stdlib，op 式） | `src/ctfy_agent/tools_domain_ext.py`、`src/ctfy_agent/pwn_kit.py` | 三个机械计算全部确定性化，模型不用在 800k 预算里现写解析脚本 |
| 2 | 团队 pwn 资产入包 | `src/ctfy_agent/pwn_assets/`（`leak_helpers.py` / `asan_shadow.py` / `pwn_stack_probe.py` + README） | 原来在 `runs/_tpctf/l2_assets/`（gitignored）；现在随 `COPY src/` 进镜像，不会丢 |
| 3 | pwn 角色接线 + 提示词 | `config/agent.json`（`roles.pwn.local += pwnkit`、system 补一段）、`crew.DEFAULT_ROLES["pwn"]` 兜底 | 工具不接线=不存在；兜底也要有，防 agent.json 丢失退化 |
| 4 | 方法卡更新 | `src/ctfy_agent/method_cards.py`（栈溢出卡 + ASan 泄漏链卡） | 题面命中 pwn tag 时直接把「用 pwnkit 算，不要手算」推到模型眼前 |
| 5 | 回归 | `tests/smoke_pwn_kit.py`（34 项） | 数学、真实 libc 解析、错误路径、接线、资产一致性全部钉住 |

## 2. `pwnkit` 能力与边界

| op | 输入 | 输出 | 典型用法 |
|---|---|---|---|
| `libc` | 沙盒里的 `libc.so.6` | 版本串、build-id、14 个关键符号偏移（vaddr）、`/bin/sh` 的 vaddr/file_off、缺哪些符号 | 附件带 libc 时第一步 |
| `ret2libc` | libc + 泄漏符号名 + 泄漏值 | libc 基址、system、`/bin/sh`、pop_rdi/pop_rsi/pop_rdx/syscall 等 gadget（**已加基址**） | 泄露出 puts/read 之后一步出链 |
| `asan` | 目标地址 + 窗口长度 | shadow 区间、每个 shadow 字节要写的值、恢复提示 | 写真实栈（ASan 题最后一步） |
| `assets` | — | 团队资产目录 + 文件清单 + 用法 | 找 `pwn_stack_probe.py` 等 |

ASan 语义细节（本轮修过一版）：`shadow(addr) = (addr>>3)+0x7fff8000`；块首对齐时低 3 位=可访问前缀长度；
**起始地址不齐时首块整块放开（写 0）**——否则 ASan 仍按「前缀可用 N 字节」把目标写拦掉（多放开无害，少放会失败）。

边界（明确不做）：不自动连远端、不生成完整 exploit、不含 libc 数据库（`libc` 需要一个真实 libc 文件，
匹配 libc-database 的「只给两个偏移找版本」不在本轮）；`one_gadget`、堆风水模板仍未做。

## 3. 验证

```bash
python tests/smoke_pwn_kit.py      # 34 项：ASan 数学 / 真 libc 解析 / ret2libc / 资产 / 接线
python tests/smoke_pwn.py          # 24/24（既有 pwngen/pwnfind/elfpeek 回归）
python tests/smoke_libs.py         # 33 项通过（pwnrop/z3/pycrypt 接线，2 组因无 ELF 编译环境跳过）
python tests/smoke_domain_cards.py # 方向卡（同步把计数 8→9：udp 卡是 11-13 加的，测试之前过期）
python tools/sync_roles.py --check # 三源零漂移
```

真 libc 解析用仓库里的跑批残留样本 `runs/tpctf2026/attachments/AUTHORED-31FB487C/libc.so.6`
（Debian GLIBC 2.41），验证到：版本串、40 位 build-id、system/puts/read 偏移、`/bin/sh` vaddr 全部可复算；
`ret2libc` 用构造泄漏值反推基址，与「base+偏移」逐项对齐。**没有样本时该组自动跳过**（`runs/` 被 gitignore）。

## 4. 还没验证的部分（不要吹）

- 全部是离线单测：**没有在真实 PWN 题上跑过 pwnkit**（需要用户批配额/起靶，见 TODO）；
- 真实收益要等下次 pwn 批次对比：预期减少「算偏移/推导基址」类步数，但**不会**自动解决 ASan/堆题；
- 下一个该补的短板（按收益排序）：libc 版本匹配（只给偏移找 libc）、one_gadget/FSOP 模板、
  堆（tcache/fastbin）脚手架、seccomp 落地后的 ORW 链模板。