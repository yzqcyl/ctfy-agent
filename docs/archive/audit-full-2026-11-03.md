# 全项目审计报告（2026-11-03）

> 范围：`src/ctfy_agent` 全部 31 模块（约 17.5k 行）、`tools/`、`tests/`、`config/`、`Dockerfile`、仓库根目录卫生。
> 方法：静态通读 + 交叉核验 + 只读复现（临时目录构造样例直接调 `call_domain` 等，未改任何源码）。
> 口径：【实测复现】=当场构造输入触发；【读码确认】=逐行读到代码属实；【待确认】=逻辑成立但未复现。
> 基线：HEAD `f50f092`（v3 线）；总入口单测 **160 passed / 0 failed**（本机原工作区口径）。

---

## 修复状态（2026-11-03 下午，第一批已落地）

| 条目 | 状态 | 改动 |
|---|---|---|
| B-1 工具层裸异常穿透 | ✅ 已修 | `tools_domain_ext.py`：`call_domain` 增 `except (ValueError, TypeError, struct.error, OverflowError) → _fail`；`_Elf.__init__` 按 is64 区分 64/52 字节最小头长；`prng` mt_seed 用 `_int_of` 结果格式化；`_int_of` 十进制优先（前导 0 不再误判十六进制）；补 `import re` |
| B-2 start 重试文案 + 逐次记账 | ✅ 已修 | `solver.py`：删硬编码 `（timeout）`；`_start_with_backoff` 增 `start_name` 参数、每次真实 start 前 `record_call`；`_ensure_instance`/`_recover_instance_slot` 移除前置重复记账；`tests/smoke.py` 两个桩签名适配 |
| A-1 `_unique_dir` 沙盒逃逸 | ⚠️ 澄清为误报 | `LocalTools.unpack_archive` 已对 `out_dir` 做 `_resolve_path` 锚定（`tools_local.py:990`），`is_relative_to(sandbox)` 越界即拒；`_unique_dir` 只在沙盒内加后缀，不逃逸。未改代码 |
| B-3 部分解状态机 | ✅ 已修 | `mark_submitted` 增 `fully_solved` 参数（False 不置 submitted/submitted_ok）；`_submit` 增 `_fully_solved_from` 解析回执；`precheck_fn` + 集群-B 预检改为只认 `done`/`submitted_ok`。新增单测 `case_partial_solve_state` |
| B-4 QA 提交通道 | ✅ 已修 | `_submit` 的 `competition_id` 改走 `self._competition_id(challenge_id)` 主动反查；`upsert` 白名单补 `is_qa_only`（bool 单独处理） |
| B-5 并发写竞态 | ⏳ 未修（第二批） | 集群-A/多进程开启时触发，默认关，暂缓 |
| A-2 凭据明文落盘 | ⏳ 未修（第二批） | 需脱敏 transcript/ledger/traffic/checkpoint |

---

## 0. 总评

| 维度 | 结论 |
|---|---|
| 整体质量 | **中上**。护栏设计意识很强（白名单/幂等/限读/有界收割/重定向护栏），密码学实现（AES/RSA/MT19937）对照标准向量全部正确，无 shell 命令注入、无 Zip Slip、无密钥硬编码 |
| 最薄弱处 | ① 畸形输入的裸异常穿透（工具层只捕自家异常类型）；② 多线程/多进程并发写 state/checkpoint/traffic 的竞态（集群-A 开启时引爆）；③ 提交状态机对"部分解"的处理自相矛盾 |
| 安全红线 | 无可直接远程利用的 RCE/注入；主要风险是**凭据明文落盘**（transcript/ledger/traffic/checkpoint 四处）与**沙盒外写目录旁路**（misc 解包 1 处） |
| 冗杂 | 仓库根 ~8 MB 调试残留（含一对完全相同的文件）；5 份复制粘贴的 `_clip/_fail/_tool_error`；4 处确凿死代码；docs 75 篇中约 1/3 主题重叠 |

---

## A. 安全漏洞

### A-1【已澄清为误报】`unpack_archive` 的 `_unique_dir` 可把解包目录建到沙盒外

~~`tools_misc_ext.py:368-374`：`out_dir` 经 `LocalTools._resolve_path` 解析后传给 `unpack_archive`，但 `_unique_dir(base)` 在 `base` 已存在时返回 `base.parent / f"{base.name}-1"`——派生出的兄弟目录没有再过 `is_relative_to(sandbox)` 校验。~~

**复核结论（误报）**：`LocalTools.unpack_archive`（`tools_local.py:984-992`）对 `path` 与 `out_dir` **都**先经 `_resolve_path` 锚定——`_resolve_path`（`tools_local.py:931-938`）对绝对路径做 `is_relative_to(sandbox)` 越界即抛 `ToolError`，相对路径拼到沙盒根。因此传入 `tools_misc_ext.unpack_archive` 的 `out_dir` 已是沙盒内绝对路径，`_unique_dir` 的 `base.parent` 仍在沙盒内，不会逃逸。实际调用路径全部经 `tools_local` 包装，无旁路。

### A-2【读码确认】工具结果与账本标签把凭据明文落盘/回灌（P1，信息泄漏）

- `solver.py:1180/1494`：`args` 原样进 transcript（`http_request` 的 `headers` 含 Authorization/Cookie，`form/body` 含账密）；
- `ledger.py:239-260`：`attempt_label` 把 `body={...}`、URL query（`?token=…`）原样写进 label，经 `digest()` 进模型上下文与 checkpoint；
- `traffic.py:205-209`：请求头与完整响应体（含 flag）明文入 sqlite，目录权限随 umask；
- `checkpoint.py:38-49`：`Tried.raw` 全量明文落盘。

`mcp_client.mask_token` 已有掩码意识但这四处是缺口。修法：落盘/回灌前对 `authorization/cookie/token/password/secret` 类键统一掩码；runs 目录权限收紧到 0700。

### A-3【读码确认】角色工具面门禁只挡"看不见"，不挡"调得动"（P1，权限模型名不副实）

- `solver.py:694-696`：`_execute` 对本地工具的判定是 `name in local.names()`，而 `LocalTools.specs()`（`tools_local.py:904-917`）**无条件**包含 `fuzz/sqli/authdiff/jsrecon/misc_ext` 等全部规格——角色 `local` 名单只影响 schema 可见性。模型（或被注入的题面）点名 `sqli`/`fuzz` 即可让一个 `local:["http_request"]`、`allow_shell=false` 的角色跑起 sqlmap/ffuf 子进程。
- `crew.py:523-529`：`_role_platform_names` 与 `platform.tool_names()`（全量 100+）取交集，**未再与 `MODEL_TOOLS` 白名单取交集**——`roles.<role>.platform` 写错一个名字就把非白名单平台工具授给专家。
- `solver.py:1301`：`run_role(platform_names=None)` 默认回退"全平台白名单"，默认值不安全（当前调用方都传了，属陷阱）。

修法：把角色 `local` 名单传入 `LocalTools` 并在 `call()` 内做名字白名单；`_role_platform_names` 追加 `& MODEL_TOOLS`；`platform_names` 默认改空集。

### A-4【读码确认】`.env` 本地明文存有真实令牌（运维项）

`.env` 含 `CTFY_TOKEN=pf_…` 与 `DEEPSEEK_API_KEY=sk-…`（已 gitignore/dockerignore，未入库）。但 HANDOFF §F.2 列的**轮换与 PAT 吊销尚未执行**。打包/交接前必须：吊销旧 GitHub PAT、轮换两把 key、从交付物剔除 `.env`。

### A-5【读码确认】`instance_guard.reap` 保护判定过宽（P2，误杀风险）

`instance_guard.py:135-153`：只按 `challenge_id` 保护，不检查平台实例的 `team_id/lease/status`；`challenge_id` 为空的实例、擂台 match 实例都可能被尝试 stop（当前依赖服务端拒绝）。修法：`lease=="match"` 或 `cid` 为空一律跳过。

### A-6【读码确认】其余核查为阴性（不必再查）

- ffuf/sqlmap 全程 argv 列表、无 `shell=True` 参数拼接 → **无命令注入**；
- `words/wordlist` 走白名单键 → **无路径穿越**；
- `tools_websql.py:85-91` 的 `sqlmap_py` 可指向任意 .py 并被 `sys.executable` 执行——当前生产路径恒为 None 不可达，属纵深防御缺口（建议加 `path.name=="sqlmap.py"` 校验）；
- AES-128/192/256（FIPS-197 向量）、MT19937 种子/反 temper、RSA PKCS#1 v1.5 均实测正确；
- `SameOriginRedirect/GuardedRedirect` 防 `Authorization` 外送、https→http 降级拦截、MCP/LLM 响应 8MB 限读、`run_id` 字符白名单——均已到位。

---

## B. Bug

### B-1.【实测复现】工具层畸形输入裸异常穿透（P0，一步白烧）

`call_domain`（`tools_domain_ext.py:2258-2345`）只捕 `DomainToolError`，以下输入实测抛裸异常（好在 solver:1169 有兜底会回灌给模型，但整步作废、工具不可用）：

| 触发 | 异常 | 位置 |
|---|---|---|
| 60 字节的 64 位 ELF（`len(data)<52` 只覆盖 32 位头，64 位分支在 offset 54 解 `HHHHH` 需 ≥64 字节） | `struct.error` | `tools_domain_ext.py:296,305` |
| `tcp port="80abc"`、`read_timeout="x"` 等参数未规整 | `ValueError` | `tools_domain_ext.py:2262,2273` |
| `prng op=mt_seed seed="123"`（`_to_int` 算对了整数但回显用原值 `%d`） | `TypeError` | `tools_domain_ext.py:1907` |
| `jwt_forge` 传入 header/claims 非 JSON 对象的 token | `TypeError/AttributeError` | `tools_misc_ext.py:650-666` |
| `rsatool op=pollard_pm1 B<=0` / `crt ns=["0"]` | `IndexError/ZeroDivisionError` | `tools_domain_ext.py:1355,1400-1417` |
| `_int_of("0123")` 返回 **291**（`int(text,0)` 失败后回落 16 进制，十进制被静默误读——比崩溃更危险） | 错误结果 | `tools_domain_ext.py:1321-1334` |

修法：`call_domain` 统一包 `except (ValueError, TypeError, struct.error, IndexError, ZeroDivisionError) → _fail(...)`；`_int_of` 先试 10 进制再试 16 进制。

### B-2.【读码确认】`_start_with_backoff` 把一切异常当瞬时错误重试，且本地配额只记 1 次（P0，可打穿平台 5 次硬上限）

`solver.py:508-509`：异常文案硬拼 `（timeout）` → `transient` 恒真 → 最多 5 次真实 `start_instance`；而 `_ensure_instance`（L586）与 `_recover_instance_slot`（L568）各只 `record_call` 一次。一次自动起实例最坏消耗平台 ~11 次 start，`instance_guard.start_remaining` 却以为只用了 1-2 次——正是该模块文档要防的"每题 5 次上限烧光、当天作废"。
修法：删掉文案里的 `（timeout）`，用 `_is_transient(str(exc))` 判定；每次真正发出 start 前都 `record_call`。

### B-3.【读码确认】部分解（partial_solve）状态机自相矛盾（P0，多问题题漏分）

- `solver.py:792-795`：第一题答对就 `mark_submitted` → `status="submitted"`、`submitted_ok=True`；
- `crew.py:1007-1044`：随后从回执读 `challenge_fully_solved=false` 把内存 `submitted` 改回 False，**但 store 已写成 submitted**；
- `crew.py:1297`：`precheck_fn` 见 `status=="submitted"` 直接短路 `already_solved`——**剩余题号永远不会被提交**。
另外 `crew.py:1035-1039` 的 `current` 无题号时回落 `"flag"` 而非真实题号，`rest` 可能包含刚答对的题，下一条默认提交打回同一题。

修法：`mark_submitted` 增加 `fully_solved` 参数（False 时只记 flag/题号不改 status）；`precheck_fn` 改判 `submitted_ok and fully_solved`；`current` 优先取回执里的 `question_id`。

### B-4.【读码确认】QA 提交通道两处断点（P1，条件触发回归）

- `solver.py:733`：`_submit` 的 `competition_id` 只读 `store.meta`，而 meta 只在 `_competition_id()`（L330，仅实例路径触发）写入；纯 QA 题从未走实例路径 → 缺参 400。修法：改 `self._competition_id(challenge_id) or ""`。
- `state.py:267-286`：`Store.upsert` 的字段白名单**不含 `is_qa_only`**（cli.py:88 归一化了但落地即丢），`solver.py:710` 的 `getattr(challenge,"is_qa_only")` 恒 False，只能靠 tags 兜底。修法：upsert 白名单补该字段。

### B-5.【读码确认】并发写竞态三连（P1，集群-A/多进程开启时引爆；默认关=暂不触发）

| 位置 | 问题 |
|---|---|
| `state.py:252` | `save()` 临时文件名只含 pid，同进程多线程（集群-A 各 lane）互相截断 → 坏 state.json；且无 fsync，kill 后可能 0 字节 |
| `checkpoint.py:124` | tmp 名固定 `*.tmp`，且 checkpoint 路径只由 challenge_id 决定（`crew.py:811`）→ 集群-A 各 lane 共用同一 checkpoint，后起路 adopt 兄弟路上下文 |
| `state.py:303-322` | `_refresh_flags` 只合并 `flags_tried/attempts/submitted_ok` 三字段，`save()` 随后全量覆盖 → 并发路写下的 `tool_counts/oracle_calls/tokens/rejected_answers/notes` 被回滚 |

修法：tmp 名加 `threading.get_ident()+uuid`；集群-A 每路 checkpoint 加 lane 后缀或禁用；`save()` 改锁内 merge-then-write。另：Windows 下 `_lock_exclusive`（state.py:37-42）先 `os.write` 使 fd 偏移=1，后开的进程锁第 1 字节、先开的锁第 2 字节——**首锁不互斥**，加锁前应 `os.lseek(fd,0,0)`；`LK_LOCK` 10 秒即抛也应改为自旋。

### B-6.【读码确认】`budget` 墙钟刹车不跨进程继承（P1）

`budget.py:111-130`：`deadline_minutes` 每次进程启动重新计时；`snapshot()` 写了 `deadline_at`（L148）但 `load()` 不恢复——断点续跑/崩溃重启一次，墙钟预算刷新一次。修法：`load()` 恢复 `deadline_at` 取更早者。

### B-7.【读码确认】traffic blobs 目录跨题共享，GC 会删别的题的正文（P1）

`traffic.py:108,235-257`：`blobs_dir = run_dir/traffic/blobs` 全 run 共享，`_gc_orphans` 的引用集只查本题 `.sqlite` → A 题超 50MB 时把 B 题仍在引用的 blob 当孤儿删除，B 题 `get/diff` 从此返回空 body 且无报错。修法：blobs 按题隔离（`traffic/<cid>/blobs`）或 GC 前汇总全 run 引用集。

### B-8.【读码确认】提交判定对字符串布尔永真（P1，潜在假阳性）

`solver.py:246-255`：`{"success": "false"}` → `bool("false")=True`；`{"status":"ok"}`（仅接口成功）也判"答案正确"。当前真实平台回 `correct: bool` 不误判，换平台即错杀。修法：显式类型判定，`status/result` 不接受裸 `"ok"`。

### B-9.【读码确认】stop 后不清 instance_id，与 start 幂等门互锁（P1）

`solver.py:621-629`：stop 分支不改 state；`_ensure_instance`（L578-580）见 `challenge.instance` 非空直接复用。模型 stop 后无法重启；实例实际已被平台回收时，所有实例工具报 `instance_not_found` 而本地拒绝重开，死锁。修法：stop 成功或 start 报 `instance_not_found` 时清空 `instance_id/instance`；复用前用 `get_instance_status` 校验存活。

### B-10.【读码确认】`retry.NEAR_ACTIONS` 用语义名，运行期是真实工具名（P2）

`retry.py:38`：`{"…","submit_flag"}`，而 `tail_action` 恒为 `submit_answer` → 提交类 near_miss 永不命中，集群-B 少一条早停信号。修法：补 `"submit_answer"` 或按 `try_resolve` 生成。

### B-11.【读码确认】`quotable_flag` 返回最早而非最近（P2）

`ctxcompress.py:315-325`：docstring 写"最近一次"，实现按插入序返回**第一条**——模型被引导引用早已证伪的旧候选。修法：`reversed(list(self._facts.values()))`。

### B-12.【读码确认】`ALLOWED` 允许 `submitted→probing`（P2）

`state.py:71`：与"提交过的绝不重复提交"目标冲突，回退后 `attempts<limit` 仍可再提交。修法：删该迁移或加显式开关。

### B-13.【读码确认】其余确认但降级的问题（P2 摘录）

- `tools_domain_ext.py:129`：`_recv_until` 实际墙钟 = 2×read_timeout（deadline 判定多加了一次 read_timeout）；
- `tools_domain_ext.py:1122`：`k**n` 先算后比，n 大时触发 4300 位整数转换限制或 OOM；
- `tools_domain_ext.py:374-386`：`strings` 的 `min_len=0` 每字节产出一个空串，先 OOM 后截断；`cur += bytes([b])` 是 O(n²)；
- `tools_misc_ext.py:485`：`write_file` 的 `append` 未布尔化，字符串 `"false"` 真值为追加；
- `tools_webscan.py:411 / tools_websec.py:426 / tools_websql.py:242`：先裁列表再附加 note，最终 `_clip` 仍可能切出**非法 JSON**；
- `progress.py:156-167`：`stable_text` 只剥顶层时变键，`headers.date` 剥不掉 → 重复检测形同虚设（`still_rounds` 刹车永不累计）；
- `ledger.py:663-685`：失败标记被 digest 覆盖 → 失败尝试在模型眼里和成功一样；`FAILURE_MARKERS` 裸子串 `"error"/"401"` 会误判正常输出；
- `artifacts.py:130-140`：base64 先全量解码后判大小，30MB 附件先吃内存；
- `llm.py:147`：`int(usage.get(...))` 遇网关返回 `"12.5"` 会 `ValueError` 击穿 `chat()`。

### B-14.【误报澄清】子代理初判 `solver.py:1215 ok=accepted or None` 把正常读取记成失败——**核实为不是 bug**：`False or None` 求值为 `None`，走 `looks_failed` 启发式，语义正确。无需修。

---

## C. 冗杂内容与文件（清理清单）

### C-1 仓库根目录调试残留（~8 MB，全部未跟踪，HANDOFF §B.6 已登记）

| 路径 | 体积 | 说明 |
|---|---|---|
| `新建文件夹 (2)/` | 6.7 MB | 他人 tarball 解包残留 |
| `agent-artifacts-m55-m56.zip` | 856 KB | 历史构建产物 |
| `bundle.js` + `idx.js` | 64 KB×2 | **md5 完全相同**（`0ee1f8d4…`），纯重复 |
| `assets_index.js` / `js/` / `home.html` / `crush`(ELF) / `message.txt.cz` / `recruitment.eml` | ~135 KB | web/misc 题附件落错位置 |
| `%TEMP%/`（字面量目录名）/ `x/` / `work/capture.pcap` / `_extbank/` | ~200 KB | 测试副作用 |

处置：确认无独有数据后整批删除；并把 `*.js`、`*.eml`、`%TEMP%/` 等模式补进 `.gitignore`，根治"本地跑题污染仓库根"（缺口 #5）。

### C-2 `dist/`（62 MB，gitignored）

除两个正式交付包外，含大量一次性实验目录（`_brake_dbg*`、`_brief_probe`、`_ai_*_standalone` 等十几个）。交付包之外的实验目录可清（约可回收 60 MB）。

### C-3 代码内重复实现（读码确认）

| 内容 | 位置 |
|---|---|
| `_tool_error/_fail/_clip` 复制 **5 份**，且 `_clip` 有 3 种不同截断口径（domain 版不减尾长会超限） | domain:36-57 / misc:54-74 / webscan:96-113 / websec:99-116 / websql:37-53 |
| `_TIMEVARY_KEYS + _stable_output` | solver.py:77-94（**死代码**，progress.py:148-165 有在用的同口径副本） |
| `caps_fn` 公式逐字重复 | solver.py:848-856 与 crew.py:1279-1293 |
| `_initial_prompt` 与 `_brief` 同模板两份且已漂移（notes[-5:] vs [-8:]） | solver.py:1683-1705 与 crew.py:1607-1639 |
| `DEFAULT_ROLES` 与 `config/agent.json` 双份角色定义 | crew.py:149-167 |
| 节头解析 64/32 位分支写了三遍 | tools_domain_ext.py:313-437 |
| 消毒逻辑两份（行为已漂移） | traffic.py:74-76 与 state.py:432 |

### C-4 死代码（grep/AST 确认零调用）

- `solver.py:15` `import hashlib`、`retry.py` `import sys`（AST 扫描未使用导入）；
- `solver.py:75-94` `_TIMEVARY_KEYS/_stable_output`（见上）；
- `crew.py:513` `self._specialists` 赋值后从未读取；
- `budget.py:88-94` `tokens_left/cost_left` 属性全仓无调用方；
- `retry.py:58` `HOPELESS_TAGS` 分支：两个 `AttemptOutcome` 构造点都不传 `tags` → 永不成立；
- `retry.py:196` `raw is False` 在 `raw == 0` 之后，永假；
- `tools_domain_ext.py:33` `MAX_READ = 65536` 定义后从未使用（恰好是 elfpeek 整文件读入该用的上限）；
- `tools_websec.py:275-277` `AUTHDIFF_MAX_ROWS` 裁剪：plan 恒 ≤13 行，永不触发。

### C-5 文档冗余（75 篇）

审计/复盘类至少 10 篇主题高度重叠（`audit-2026-10-25 / audit-agent / audit-behavior / audit-fixes / security-audit / security-fixes / toolbox-audit / audit-post-misc-code / audit-a3-method-cards / audit-budget-exhausted`）。建议归档进 `docs/archive/`，根目录只留 `docs/archive/HANDOFF-2026-11-08-对外交接版.md`、`plan-v1.md`、`security-fixes.md` 与最新基准报告。README 的 6 处失真（HANDOFF 缺口 #4）仍未修。

---

## D. 可优化代码

| 位置 | 问题 | 建议 |
|---|---|---|
| `solver.py:48-50,1063` + `crew.py:892` | 每步对全历史 `json.dumps` 估算 token，O(n²) | 增量维护估算值 |
| `solver.py:1084` | 每步 `store.save()` 序列化**整个 run 全部题目** | 只写当前题或 dirty 标记 |
| `solver.py:292-335` | `_competition_id` 反查失败不缓存，每次全比赛 `list_challenges`（实测 16 场网络请求） | 失败写短期负缓存 |
| `crew.py:574` | `json.loads(json.dumps(COORDINATOR_TOOLS))` 每轮深拷贝 | 模块级预生成 |
| `tools_domain_ext.py:374-381` | 逐字节字符串拼接 O(n²) | `re.finditer` 或 bytearray |
| `traffic.py:283-303` | 每 100 次写触发全目录 stat + 全表扫的同步 GC | 后台化或计数器触发 |
| `ledger.py:606-616` | `verify_evidence` 每轮对最近 60 条重做 3 层 `json.loads` | 预归一化缓存 |
| `tools_domain_ext.py:313-437` | `info/security` 视图重复调 `symbols()/sections()` 两三次 | 解析结果实例级缓存 |

---

## E. 修复优先级路线

**第一批（改一行到几行，收益最大）**
1. B-2 start 重试的 `（timeout）` 文案 + 逐次 record_call（保住每题 5 次实例配额）；
2. B-3 partial_solve 的 `mark_submitted(fully_solved=…)` + precheck 口径（多问题题漏分）；
3. B-4 `_submit` 改用 `_competition_id()` + `upsert` 补 `is_qa_only`（QA 链路）；
4. B-1 `call_domain` 统一异常包装 + `_int_of` 十进制优先（工具不再裸崩）；
5. A-1 `_unique_dir` 沙盒复验（一行）。

**第二批（并发与预算可信度）**
6. B-5 state/checkpoint tmp 唯一化 + `save()` 锁内合并 + Windows 锁偏移修正；
7. B-6 budget 恢复 `deadline_at`；
8. B-7 traffic blobs 按题隔离。

**第三批（安全加固与清理）**
9. A-2 四处凭据脱敏 + 目录权限；
10. A-3 工具面执行层白名单（local 名单 + MODEL_TOOLS 交集）；
11. C-1/C-4 根目录清理 + .gitignore 补模式 + 死代码删除；
12. C-3 抽公共 `_common.py`（`_fail/_clip/_to_int`），统一截断口径。

---

*本报告由只读审计 + 定点复现产生，未修改任何源码；修复后请以 `python tests/smoke.py`（当前 160/0）+ 25 个免凭据套件全绿为准。*
