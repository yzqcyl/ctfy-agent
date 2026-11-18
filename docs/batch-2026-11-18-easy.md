# 真实批量测试 Easy 批次复盘（2026-11-18 10:54–11:17）

> 平台：ctf2 / BUUCTF（practice_ground `b9bbb32f-f186-458f-b90b-12440c0f6aea`）
> 跑法：`python tools/run_batch.py --manifest runs/batch-easy/manifest.json --run-id batch-easy-2026-11-18 --profile ctf2`
> 复盘：`python tools/analyze_batch.py runs/batch-easy-2026-11-18`
> 原始数据：`runs/batch-easy-2026-11-18/{driver.log,events.jsonl,analysis.md}` + 每题 `transcripts/<id>.jsonl`

## 1. 结果

| 题 | 类别 | 结果 | 用时 | token | 记录/PLAN | 说明 |
|---|---|---|---|---|---|---|
| ciscn_2019_n_1 | PWN | ✅ 解出 `CTF2{16145d46-…}` | 2.3 min | 84k | 87 / 20 | 静态分析直接命中：无 PIE/无 canary + `gets` 溢出覆写 `v2` 判等 → 一发入魂 |
| get_started_3dsctf_2016 | PWN | ❌ stalled | 8.1 min | **151k（撞 150k 上限）** | 101 / 8 | 32 位静态 ret2text；`pwnsession` 试了 14 次，始终「无回显/EOF」，无法区分崩溃 vs 输出被吞 |
| reverse1 | REVERSE | ✅ 解出 `flag{hell0_w0rld}` | 4.7 min | **154k（≈上限）** | 99 / 42 | 差分执行定位到 o→0 替换（`{hello_world}` vs 程序接受的 `{hell0_w0rld}`），压哨提交 |
| 新年快乐 | REVERSE | ❌ stalled | 8.0 min | **156k（撞上限）** | 98 / 39 | 39 次 PLAN、9 次上下文压缩，预算耗尽仍未收口 |

**汇总：2/4 解出（50%）；4 题里 3 题撞到/贴近单题 token 上限。**

## 2. 监视到的思考过程（亮点）

- **PWN ciscn_2019_n_1**：`elfpeek` 看架构/保护 → 反汇编 `main` 发现 `gets` → 写 exp 打远端，**不绕路**。
  PLAN 摘要：`PLAN: 先确认架构与保护，再反汇编 main 找溢出偏移…` → `REFLECTION: 无 PIE、无 canary，直接覆盖 v2 判等`
- **REVERSE reverse1**：先用 `hexpeek`/`fileview` 在 PE 里定位三个提示串 → 找到 `.data` 的 `{hello_world}` →
  **用本机执行 exe 做差分实验**（`{hell0_w0rld}` → "this is the right flag!"）→ 回头用汇编确认 `cmp eax,0x6f` 的 o→0 替换。
  这是「动态验证优先于死读汇编」的正确打法，但花了 154k。
- 两个失败题的最后一个 PLAN 都是「预算已近耗尽，收口期」——**思路没断，是预算先断**。

## 3. 暴露的问题（按影响排序）

| # | 问题 | 证据 | 建议 |
|---|---|---|---|
| 1 | **单题预算偏小**：Easy 150k token / 10 min | 3/4 题撞上限；reverse1 压哨 154k 解出；两题 stalled 时 token 151k/156k | Easy 提到 250k/15min；Medium 400k；Hard 600k。改 `config/agent.json` 或批次参数 |
| 2 | **收口太晚**：撞预算时还没提交候选 | get_started/新年快乐 stop 时 `attempts=0`，一个候选都没交 | 加「预算 80% 强制提交最优候选」的 hard rule（coordinator 层），别等 100% |
| 3 | **Windows/Linux 习惯错配** | shell 非零退出 14 次：`tar` rc=9009、路径引号错误、`python3` 27 次提及、`tee` 8 次 | ①提示词写明「本机是 Windows：用 python，不要 tee/tar/&」；②工具层把 `python3` 别名到 `python`；③或提供 POSIX shim（WSL 可用则优先） |
| 4 | **缺本地确定性验证**（PWN 32 位静态） | `pwnsession` 14 次全无回显，无法区分「崩溃/无输出/偏移错」 | 给 pwn 一条本地跑 ELF 的路（WSL/qemu-user 或容器）；短期在 `pwnsession` 回执里标注「等首字节超时=服务端没输出」 |
| 5 | **上下文压缩频繁** | reverse1 5 次、新年快乐 9 次压缩提示；PLAN 39–42 次 | 压缩本身是设计好的，但压缩后重复读同一文件（`fileview` 12–13 次）浪费预算；可在压缩时保留「已确认事实」摘要 |
| 6 | **TLS 误用** | get_started 先试 `nc_ssl=true`，被合规拦截 1 次 | 实例回执已给 `access_type: tcp`，提示词强调「按 access_type 来，不要猜 TLS」 |
| 7 | **驱动脚本健壮性**（本次顺手修掉） | 首版 `run_batch.py` 被 `▶` 字符 GBK 编码、`batch_note` 字符串 payload、`task_done` 字段名连崩 3 次 | 已修：`_safe_console()`、`_field()` 容错、ASCII 日志前缀；`task_done` 现在记 `ok/flag/note` |

## 4. 下一步

1. **提预算后重跑这 2 道未解**（get_started、新年快乐），验证「预算才是瓶颈」：预计各 3–5 分钟、250k 内可解。
2. **再上 medium/hard**（题单已备好：`runs/batch-medium-hard/manifest.json`，20 题：
   4 Hard WEB + PWN/REV/CRYPTO/MISC 各 2 Medium + 2 Hard）。Medium 建议 400k/20min，Hard 600k/30min。
3. 预算与额度会真实消耗平台实例配额（每题 start ≤5 次、实例全局 1 个），开跑前确认。