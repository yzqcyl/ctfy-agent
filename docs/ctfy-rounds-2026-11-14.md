# 续测收尾报告：模型/思考模式对照 + 提交额度致命缺陷 + round3 结果（2026-11-14）

一句话：**继续测试期间最大的发现不是「哪个模型更强」，而是「提交额度会因未验证候选被永久烧掉」**——
平台按「用户+题」全局计 5 次、跨 run 不重置，而我们是 per-run 计数，**已经因此永久报废 4 道题**（本文 §2）。
修复（跨 run 提交账本 + 协调者验证纪律）已进代码、有回归、进了镜像快照；round3 在新配置下 12 题解出 1 题
（`repeater`，615s，`CTF2{1e268785-...}`）。

---

## 1. 平台真值（收尾时）

| 口径 | 数值 |
|---|---|
| 平台已解 | 47 / 3244 |
| 120 题清单 | **39 / 122 已解，83 未解**（本轮新增 `repeater`） |
| 提交账本覆盖 | 323 题有提交记录，其中 **4 题额度已打空（attempt≥5）**、5 题 attempt=4 |

## 2. 致命缺陷：提交额度被未验证候选永久烧掉（P17/P18 的由来）

平台回执里的 `attempt` 是**「用户+题」全局计数，跨 run 不重置**（上限 5），而本地 `state.json` 是 per-run 的——
每换批次/快照计数就清零，于是同一道题被反复「重新开始交」。收割 1370 份转录 + 全部 state.json 后的实况：

| 题 | 平台已交 | 判错值 | 状态 |
|---|---|---|---|
| `[XNUCA2018]Code_Interpreter` | 6/5 | 6 | 永久报废 |
| `[MRCTF2020]vigenere` | 6/5 | 2 | 永久报废 |
| `cuteRE` | 6/5 | 6 | 永久报废（probe4 那条 `DST{...}` 是第 6 次） |
| `[网鼎杯2020]bang` | 5/5 | 2 | 永久报废 |
| `菜刀流量` | 4/5 | 2 | 只剩 1 次 |

直接原因有两层，都有转录原文：
1. **协调者交未验证候选**：`菜刀流量` 的 misc 专家写「DAT/NPF 候选均为噪声」，协调者仍提交 `NPF_{728EDF3A-...}`；
   `cuteRE` 的 rev 专家自己标注「尾部 `}` 是补齐推断」，协调者仍提交 `DST{Wo7Xj5Ad8Nx8}`。
2. **判错值没有跨 run 记忆**：同一个 `NPF_{...}` 在下一个 run 又原样交了一次。

### 修复
| 修复 | 内容 | 回归 |
|---|---|---|
| **P17** `d4dc3a6` | 新增 `src/ctfy_agent/submit_ledger.py`：跨 run JSON 账本（attempt 单调 max + 判错值集合 + 已通过值），提交前拦「额度满 / 该值已判错」，提交后记账；`runs/_big/harvest_submit_ledger.py` 从历史 state+转录收割（323 题）；批量跑批用 `CTFY_SUBMIT_LEDGER` 让所有快照共享 | `tests/smoke_submit_ledger.py` 全通过；`smoke.py` 180/0 |
| **P18** `5b6cc30` | 协调者硬性纪律：提交必须有「生成该值的命令+原样输出行」；专家标注噪声/诱饵/未验证/尾部推断的值**禁止提交**；拿不准先派「验证候选」的活 | `smoke.py` 180/0、`smoke_gate` 全通过 |

## 3. 模型 / 思考模式对照（同一批 3 题：菜刀流量、ezRSA、cuteRE，25min/题）

| 组合 | 解出 | 思考烧穿 | 提交 | 观察到的真实进度 |
|---|---|---|---|---|
| flash + 思考开（旧本地口径） | 0/3 | — | 8（含诱饵） | cuteRE 15min 没找到算法 |
| `qwen3.8-max-0902` + **思考开**（probe3） | 0/3 | **3 次** | 0 | 定位 base64+RC4 但 CoT 烧穿打断专家 |
| `qwen3.8-max-0902` + **思考关**（probe4） | 0/3 | **0 次** | 8 | 菜刀流量解出口令 `SuperHer0Masters`；ezRSA 正确转向 Coppersmith 已知高位分解；cuteRE 解出 ROT32 base64 → `DST{Wo7Xj5Ad8Nx8` |

**结论：本地 `.env` 把 `LLM_ENABLE_THINKING = 0` 注释掉＝端点默认开思考，而镜像 `Dockerfile` 是 `ENV LLM_ENABLE_THINKING=0`。**
本地开思考产生 3 次「思考烧穿」，关掉后同模型同预算 0 次烧穿且进度更深——**本地测量口径一开始就与上传版本不一致**，
这条本身就是「怀疑解题体系」的直接答案：此后一律按镜像口径（关思考）跑。

## 4. 能力补进镜像（都有功能级验证）

| 修复 | 依据 | 验证 |
|---|---|---|
| **P19** shell 守卫补 `cut/tr/uniq/xargs/tee/basename/dirname/env` + 提示改 Python | round3 实测 `... \| cut -c1-200` 直接 `'cut' is not recognized` | `smoke_local_tools` 新增用例全通过 |
| **P19** 镜像 apt 增加 `upx-ucl`（+1MB，构建期校验可执行，`check_capabilities` 增加「压缩→解压→逐字节一致」往返探针） | round3 的 `b8c599c2` 专家敲 `upx --version` 本机/镜像都缺；REVERSE 加壳题刚需 | `smoke.py` 180/0；容器内探针由 CI 构建跑 |
| **P20** 本地缺失工具（objdump/readelf/od/gdb/strings…）守卫改道 `elfpeek`/`hexpeek` | round3 实测 `od -An -c attachment.md` 直接失败，白丢专家步 | `smoke_local_tools` 新增 od/objdump 两条用例 |
| **未采纳：angr** | 已装到本地 `tools/pylibs` 但依赖（protobuf 等）与现行 pin 冲突、且无法在本地做镜像级验证 | 按「补的功能必须能进镜像并反复测过」的要求，**不写进 Dockerfile**，留作下一轮（先把依赖锁验证好再上） |

## 5. round3（新配置首跑）逐题结果

配置：`qwen3.8-max-0902` + `LLM_ENABLE_THINKING=0` + 25min/题 + 跨 run 账本 + 并发 3；
选题：12 道**从未提交过**（attempt=0）、解出队伍多的相对容易题。

| 题 | 分类 | 耗时 | 结果 |
|---|---|---|---|
| `repeater` | PWN | 615s | **解出** `CTF2{1e268785-81cb-43aa-bcc3-1c8fcd5c101d}` |
| `掩耳盗铃` | MISC | 1171s | budget_exhausted |
| `二师兄呢？` | MISC | 774s | budget_exhausted |
| `[VNCTF2022公开赛]仔细找找` | MISC | 946s | budget_exhausted |
| `[UTCTF2020]Curveball` | CRYPTO | 1008s | budget_exhausted |
| `easyROPtocol` | PWN | 998s | budget_exhausted |
| `edocnEesaB` / `EZsignin` / `crypto_crossword` | CRYPTO | 805–1187s | coordinator_done |
| `ez_arithmetic` / `starctf_2019_babyshell` / `clockin` | REV/PWN/REV | 587–1065s | coordinator_done |

shell 结果分类（round3 6 份转录）：ok 443 / cwd_not_found 13 / cmd_not_found 6 / timeout 1——`cmd_not_found` 已由 P19/P20 覆盖。

## 6. 未解原因分类（来自转录，不是猜测）

1. **提交纪律**（已修）：有候选但不敢交 / 交了未验证值；现在「有证据才交 + 跨 run 拦判错值」。
2. **预算分配**：`budget_exhausted` 5 题全部是「长分析链 + 反复侦察」，25min 墙钟没到但 token/调用预算先打满。
   下一步该做的是「先产出可执行验证脚本、再深挖」，而不是继续加墙钟。
3. **能力缺口**（部分已补）：UPX 脱壳（已进镜像）、GF(2^k) 运算、大整数分解工具链、angr 符号执行（未验证，未进镜像）。
4. **模型侧**：思考模式必须与镜像一致（关）；强模型在「数学类 crypto」上明显更强（Coppersmith/HNP 路线正确），
   但**没有验证纪律时强模型只会更自信地交错值**——P18 是这次最重要的护栏。

## 7. 复现命令

```bash
# 120 题真值与清单
python runs/_big/fetch_unsolved.py ctf2
# 跨 run 提交账本（收割历史 + 查询某题剩余额度）
python runs/_big/harvest_submit_ledger.py
# 跑一批（注意：并发≤3；账本必须指向全局文件，否则跨 run 保护失效）
$env:DEEPSEEK_MODEL='qwen3.8-max-0902'; $env:LLM_ENABLE_THINKING='0'
$env:BIG_PROFILE='ctf2'; $env:BIG_STATIC_MINUTES='25'; $env:BIG_ENV_MINUTES='25'
$env:CTFY_SUBMIT_LEDGER='E:\ctfy_project\runs\_big\submit_ledger.json'
python runs/_big/run_big.py <batch> <manifest.json> <unix_stop> 3
# 测试门禁
python tests/smoke.py            # 180/0
python tests/smoke_submit_ledger.py
python tests/smoke_local_tools.py
```

## 8. 本轮 git 提交（路径限定，均已跑测试）

- `d4dc3a6` fix(submit): 跨 run 平台提交账本（P17）
- `5b6cc30` fix(prompt): 协调者提交前必须有可复现执行证据（P18）
- `1091e0c` fix(toolbox): shell 守卫补 cut/tr/env；镜像补 upx（P19）
- `2748a68` fix(toolbox): 本地缺失工具导向 elfpeek/hexpeek（P20）

## 9. 教训（写给自己）

- **「有候选就交」在只有 5 次永久额度的平台上是负收益**：4 道题已经因为这条报废，任何「再试一次」都必须先问「这个值有执行证据吗」。
- **本地口径 ≠ 镜像口径**（思考模式、工具链、沙箱）时，本地结论不能当结论；先对齐口径再比较模型。
- **失败题的第一手材料是转录，不是猜测**：`cut`/`od`/`upx` 这些缺口全都是转录里一眼可查的，不是「模型不聪明」。