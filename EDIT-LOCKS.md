# EDIT-LOCKS —— 跨会话文件编辑锁（防两个对话同时改一个文件）

> **约定（2026-10-30 起生效，两个会话都遵守）：**
> 1. **改任何「非本轮新建」的文件之前**，先在下表登记一行；**会跨轮继续改的文件必须登记**。
> 2. 看到文件已有别人的**活动锁** → 不要改；等对方释放，或让用户协调。
> 3. 文件稳定（写完/已提交/不再动）就**删掉该行**（可挪到「已释放」留痕）；悬置超 24h 的锁视为过期，交用户裁决。
> 4. 活动锁的文件，**文件头加一行横幅**：`<!-- WIP: <会话>, <起时>, 见 EDIT-LOCKS.md -->`（代码文件放注释/文档字符串首行），完成时一并删除。
> 5. `runs/` 下对方的 `_*` 脚本与批目录视为对方领地，不动；新建自己的用不冲突的名字。

## 活动锁

| 文件 | 会话 | 开始 | 说明 |
|---|---|---|---|
| （无活动锁） | — | — | 接手轮（W）已收尾，见下方「已释放」最后 5 行 |

## 已释放

| 文件 | 会话 | 区间 | 结果 |
|---|---|---|---|
| src/ctfy_agent/crew.py + src/ctfy_agent/agent_main.py | W（接盘 2026-11-10） | 11-10 接手轮 | 重复被拦动作断路器（`reason=action_loop`，`_STOP_MAP` 映射到 `max_turns`）。`smoke_action_loop.py` 23 条绿；`smoke.py` 175/0；对非重复动作字节透明 |
| src/ctfy_agent/tools_local.py + tools/shims/shim.py | W（接盘 2026-11-10） | 11-10 接手轮 | ① 修同主机换端口绕过 / 兄弟域名过度放宽（`smoke.py::case_hardening` 断言）；② shell 子进程挂 `tools/pylibs`（PYTHONPATH + `*.libs` DLL）与缺库提示；③ `rm -rf`→`rmdir /s /q`、`ls -la`→`dir /a`；④ shim `head/tail -c`。`smoke_local_tools.py` 全绿（ls 断言按新语义更新） |
| src/ctfy_agent/attachments.py（新建）+ src/ctfy_agent/gui/{platform_worker,app}.py | W（接盘 2026-11-10） | 11-10 接手轮 | 附件自动下载（真实 DASCTF 验证：unipickle.py 90B/sha256 `96309f79…`）+ 开跑前实例清扫 + 扫盘后台化。`smoke_attachments.py` 26 条绿 |
| src/ctfy_agent/mcp_client.py + gui/onboard.py + gui/toolmap.py + gui/ocr_tools.ps1 | W（接盘 2026-11-10） | 11-10 接手轮 | ① `McpHttpClient.close()`（只新增）；② `probe()` finally 关会话；③ OCR 去掉 PIL 依赖、`.ps1` 重写为 UTF-8 BOM+CRLF。`smoke_ps1.py` 绿、GUI selftest OK |
| config/agent.json + src/ctfy_agent/config.py + roles/{ai,misc}.* + tools/{fetch_pylibs,sync_roles,analyze_loop_waste}.py | W（接盘 2026-11-10） | 11-10 接手轮 | `max_llm_calls` 160→240（证据：`tools/analyze_loop_waste.py` 量化可省 ≥176 次）；角色三源重新同步；本地依赖库装上并 gitignore `tools/pylibs`；`.gitignore` 补该行 |
| docs/HANDOFF-2026-11-10.md + tests/{smoke,smoke_local_tools,smoke_ai_role,smoke_webai_gap}.py + run.cmd + .gitignore | W（接盘 2026-11-10） | 11-10 接手轮 | 文档回填本轮真值（§7.1/§8.1/§9/§10）；既有断言按新语义更新（175/0）；`run.cmd` 归一 CRLF |
| runs/_stats.py | Y（镜像/验证线） | 10-30 19:00–19:20 | 验收通过：bank-500k-v1 全指标复现（35/10、22/2/1、15,331,789 tok、23.5/39.0 calls）；门 A 后迁 tools/ 前重新上锁 |
| docs/contest-harvest-checklist.md | Y（镜像/验证线） | 10-30 19:00–19:25 | M4 交付完成，单轮写完即释放 |
| runs/_p2v_batch.py | Y（镜像/验证线） | 10-30 19:40–19:55 | M2 两臂启动器完成，离线消融验证通过（A 臂 profile={}）；只写未跑，开跑需 --confirm-gate-a |
| runs/_audit_behavior.py | Y（镜像/验证线） | 10-30 20:20–20:50 | 行为审计器完成；三批实测（v0/paced/p0p1b）跑通，口径已写入 audit 文档 §1 |
| docs/audit-behavior-2026-10-30.md | Y（镜像/验证线） | 10-30 20:50–21:00 | 审计结论 + O1–O3 优化待办（门 A 后实现） |
| docs/spec-o1-http-cache.md | Y（镜像/验证线） | 10-30 21:10–21:25 | O1 实现级规格（只读调研所得，未改代码） |
| docs/platform-submit-opsheet.md | Y（镜像/验证线） | 10-30 21:40–21:50 | 平台提交逐屏操作单（第 1 项交付） |
| docs/spec-o2-o3-tools.md | Y（镜像/验证线） | 10-30 21:50–22:00 | O2 收窄规格 + O3 关闭记录（第 2 项交付） |
| docs/note-zhipu-model-choice.md | Y（镜像/验证线） | 10-30 22:00–22:10 | 正式赛模型选型备忘（第 3 项交付） |
| docs/audit-budget-exhausted-2026-10-31.md | X（agent 优化线） | 10-31 15:00–15:05 | 回填 O-A 实验结果（4/33 转化，预算非主因）+ 改建议顺序（已提交 82d5938） |
| config/agent.json（roles.web.system） | X（agent 优化线） | 10-31 15:10–15:40 | O-D 撞墙/空手判据两条（web 提示词唯一源）；JSON 无法写横幅，改为本行留痕 |
| src/ctfy_agent/crew.py | X（agent 优化线） | 10-31 15:10–15:40 | O-E：COORDINATOR_PROMPT 增加「先提交再补链路」+ 错误值防重复提交 |
| src/ctfy_agent/tools_local.py | X（agent 优化线） | 10-31 15:30–15:40 | 修 http_burst 归一化漏「浮点 epoch」（smoke_burst_group 既有 flaky，10 连跑 0 失败） |
| src/ctfy_agent/tools_webscan.py | X（agent 优化线） | 10-31 16:00–16:15 | fuzz 命中全 401/403 时给认证层 note（与 burst advice 对齐） |
| src/ctfy_agent/{solver,tools_webscan,tools_domain_ext,tools_misc_ext,tools_websql,tools_websec}.py | X（agent 优化线） | 10-31 17:50–18:00 | 工具输出硬上限改为 env AGENT_TOOL_TEXT_MAX 可覆盖（默认 6000 不变，O-C A/B 用） |
| runs/_oc_patch.py, runs/_oc_ab.py, runs/_oc_report.py | X（agent 优化线） | 10-31 17:50–18:50 | O-C 参数化脚本 + A/B 驱动 + 报告生成 |
| runs/_retry_round.py | X（agent 优化线） | 10-31 18:00–18:10 | 重试轮机制（未解未撞预算题同预算再跑 + 并集统计） |
| src/ctfy_agent/tools_local.py | X（agent 优化线） | 10-31 19:00–19:20 | O-F：http_request 新增 multipart files/form + _build_multipart（含沙箱越界校验） |
| config/agent.json | X（agent 优化线） | 10-31 19:20–19:25 | web 提示词：上传点优先用 files，curl -F 降为兜底（JSON 无法加 WIP 标识） |
| tests/smoke_http_multipart.py | X（agent 优化线） | 10-31 19:20–19:35 | 新增 O-F 冒烟 10 条断言（全量 29/29 通过） |
| tests/smoke_burst_group.py | X（agent 优化线） | 10-31 16:00–16:15 | 新增 /authwall 路由 + 2 条 advice 断言（28/28 通过） |
| src/ctfy_agent/ledger.py + tests/smoke_evidence.py | Y（镜像/验证线） | 10-30 22:40–23:10 | **agent 打磨**：M4 证据归一修复（嵌套 JSON 内层证据被误杀 → 真实回放 10/14 转接受；smoke.py 144 通过）；**未提交**，门 A 统一提交 |
| config/agent.json + roles/{ai,misc}.json + roles/{ai,misc}.system.txt | Y（镜像/验证线） | 10-31 03:30–03:50 | **提示词补丁**：web/ai 引导 greps 多搜与 fresh 重取；misc/rev/crypto/pwn/web 引导 write_file 落盘脚本；三源零漂移自检通过、全量 28/28（已提交 504b2e1） |
| docs/bigbank-report-2026-10-31.md + docs/bigbank-results-2026-10-31.csv | Y（镜像/验证线） | 10-31 06:00–06:30 | **大规模综合测试报告**：132 题实跑，77 解出（58.3%）；重叠 35 题 28.6%→48.6%；含环境限制与复现命令 |
| runs/_agent_forensics.py, runs/_rej_diag.py | Y（镜像/验证线） | 10-30 22:20–23:00 | 行为取证 / 拒收诊断工具（离线，零实例零 token） |
| docs/audit-agent-2026-10-30.md | Y（镜像/验证线） | 10-30 23:10–23:55 | 本轮 agent 诊断与修复记录（C/A/B 三节 + 口径修正 + 门 A 待办） |
| runs/_timeline.py, runs/_err_infra.py, runs/_subset_score.py, runs/_access_scan.py | Y（镜像/验证线） | 10-30 23:25–23:50 | 单题时间线 / 预算去向与错误分类 / 可达性口径 诊断脚本（离线、零 token） |
| src/ctfy_agent/progress.py + src/ctfy_agent/solver.py | Y（镜像/验证线，门 A 已开） | 10-31 00:10–01:00 | **A1 环境失败断路器**：`failure_kind` 分类 + `EnvFailureTracker`（连续 ≥3 提示 / ≥6 收口 / 占比 ≥30% 预算提示），接进 `_solve_once` 与 `run_role` 两条循环；验收 `runs/_env_replay.py` 通过（SAMPLE-003 第 7 次调用即提示、SAMPLE-004 第 7 次） |
| src/ctfy_agent/tools_local.py + src/ctfy_agent/tools_misc_ext.py + config/agent.json + roles/{ai,misc}.json + tests/smoke_local_tools.py | Y（镜像/验证线，门 A 已开） | 10-31 00:20–01:10 | **B1 `write_file` 工具**（沙盒锚定 + 512KB 上限 + 建父目录，6 个角色 local 名单同步 config/roles 双源）/ **B2 Windows 命令别名自动改写**（python3→python、ls→dir…，不再直接拒收）/ **B5 http_burst 上限文案**；B4 复核为已满足（`decode_blob` 早已 required:["text"]）。验收 `tests/smoke_local_tools.py` 26 条全过 + 全量 smoke 回归绿（已提交 1e850ce） |
| src/ctfy_agent/tools_local.py + src/ctfy_agent/ledger.py + tests/smoke_http_cache.py | Y（镜像/验证线，门 A 已过） | 10-31 01:30–02:40 | **O1 多 pattern grep（`greps` 数组）+ 非 200 响应缓存 + `fresh=1`**；顺带修 `_cache_put` 的 `dict.popitem(last=False)` 潜伏崩溃、账本键补 greps/fresh。验收 `tests/smoke_http_cache.py` 24 条全过 + 全量 27/27 smoke 回归绿；规格 `docs/spec-o1-http-cache.md` 已标注实现偏离（已提交 73b4a6a） |
| src/ctfy_agent/tools_local.py + tests/smoke.py + tests/smoke_burst_group.py | Y（镜像/验证线，门 A 已过） | 10-31 02:50–03:20 | **O2' burst 分组归一化**：`_burst_group_key`（payload 回显→`<V>`，时间/epoch/UUID/hex/长数字→`<N>`）+ 状态码进分组键；组增 `raw_count`、汇总增 `raw_distinct_bodies`；race 模式输出不变。验收 `tests/smoke_burst_group.py` 12 条全过 + `smoke.py` 既有 burst 断言按新语义更新后 144/0；规格 `docs/spec-o2-o3-tools.md`，O3 关闭 |
| docs/capability-gap-2026-10-31.md | X（agent 优化线） | 10-31 20:10–20:45 | 新增：各方向能力盘点（新建文件，无需互斥） |
| src/ctfy_agent/tools_local.py | Y → 已释放（O-G 判据） | 11-01 14:30–15:10 | 新增 _dns_unreachable_advice + 4 处请求错误路径接入 |
| src/ctfy_agent/crew.py | Y → 已释放（O-G 协调者规则） | 11-01 15:10–15:25 | COORDINATOR_PROMPT 加「接入层不可达→本轮只发 done」 |
| config/agent.json | Y → 已释放（O-G 提示词） | 11-01 15:00–15:20 | web system 加「内网不可达判据」 |
| tests/smoke_vpn_advice.py | 新建后已释放 | 11-01 15:05–15:20 | O-G 冒烟 5 条 |
| docs/p0-calibration-2026-11-01.md | 新建后已释放 | 11-01 15:40–16:00 | P0 校准结果 |
| src/ctfy_agent/method_cards.py | X → 已释放（AI 方向） | 11-01 16:10–16:40 | 新增 ASI01-标识符 oracle 卡（20→21 张） |
| src/ctfy_agent/tools_local.py | X → 已释放（AI 方向） | 11-01 16:20–17:20 | chat 解析 tool_calls（JSON/OpenAI/SSE）+ 新增 extra 参数 |
| config/agent.json + roles/ai.system.txt | X → 已释放（AI 方向） | 11-01 17:20–17:35 | AI 提示词加「注入面不只在 message」（零漂移已校验） |
| tests/smoke_ai_oracle.py | 新建后已释放 | 11-01 16:30–17:40 | AI 方向冒烟 14 条 |
| tests/smoke.py + tests/smoke_ai_role.py | X → 已释放（AI 方向） | 11-01 17:30–17:40 | AI 卡张数断言 20→21 |
| docs/ai-oracle-2026-11-01.md | 新建后已释放 | 11-01 17:40–18:00 | AI 方向补齐记录 |
| src/ctfy_agent/crew.py + src/ctfy_agent/solver.py + tests/smoke_ctxopt.py | **并行对话（非本线）** | 11-01 起 | S1b 轮间会话重置等；本线未触碰、不提交 |
| HANDOFF.md + docs/OPENING.txt | Z（交接线） | 11-03 21:00–21:30 | 对外交接版重写（A–H+附录结构；关键数字全部标注实测口径；新增缺口 #11：总入口依赖未入库的 runs/bench_stats.py，干净 clone 151/1；B.4 列全未提交 WIP 处置清单）；OPENING.txt 同步浓缩；随本轮提交 |
| docs/plan-p2v-validation-and-alignment.md | Y（镜像/验证线） | 10-30 19:00 起 | 悬置超 24h 过期，按约定第 3 条交用户裁决；2026-11-08 用户裁决释放。文档状态停在「方案待批」，WIP 横幅已删；真机验证线由 ctfarena/fix 分支后续实测报告取代（见 docs/CTFARENA_FIX_REPORT.md） |
