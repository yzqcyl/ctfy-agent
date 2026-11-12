# EDIT-LOCKS —— 跨会话文件编辑锁（防两个对话同时改一个文件）

> **约定（2026-10-30 起生效，两个会话都遵守）：**
> 1. **改任何「非本轮新建」的文件之前**，先在下表登记一行；**会跨轮继续改的文件必须登记**。
> 2. 看到文件已有别人的**活动锁** → 不要改；等对方释放，或让用户协调。
> 3. 文件稳定（写完/已提交/不再动）就**删掉该行**（可挪到「已释放」留痕）；悬置超 24h 的锁视为过期，交用户裁决。
> 4. 活动锁的文件，**文件头加一行横幅**：`<!-- WIP: <会话>, <起时>, 见 docs/archive/EDIT-LOCKS-2026-11-11.md -->`（代码文件放注释/文档字符串首行），完成时一并删除。
> 5. `runs/` 下对方的 `_*` 脚本与批目录视为对方领地，不动；新建自己的用不冲突的名字。

## 活动锁

| 文件 | 会话 | 开始 | 说明 |
|---|---|---|---|
| src/ctfy_agent/{state.py,solver.py,crew.py} + src/ctfy_agent/instance_guard.py + tests/smoke_instance_quota_scope.py（新建，不改 gui/） | W（本地 plat 重跑：instance_ops_limit 作用域修复 2026-11-11） | 11-11 19:20 | 重跑 17 题时 8 题第一轮已记满 5 次 start_instance，旧闸门读**持久化** `tool_counts` → 第二轮 52 次「已达本题上限」拦截、无法起实例。改为：① 闸门读「本次运行（进程）」计数（`Store._run_calls`，按 state.json 路径隔离、同进程 lane 共享）；② 每轮解题入口（`Solver._solve_once` / `Crew.solve`）`reset_run_calls` 清零本题计数——长驻进程（GUI 批量跑题库不退出）连跑多轮也能重新获得配额；③ `instance_recover` 自愈次数同口径；④ 持久化 `tool_counts` 降级为审计账；`start_remaining` 同口径。新增 23 条回归（含「上一轮用满 → 新一轮能重新 start / 同轮仍拦得住 / 长驻进程 reset 后放行 / 自愈次数重置」）。 |
| （无活动锁） | — | — | 本地 plat 测试监视轮（2026-11-11 17:55）已收尾，见下方「已释放」首行 |

## 已释放

| 文件 | 会话 | 区间 | 结果 |
|---|---|---|---|
| src/ctfy_agent/gui/{app.py,platform_worker.py} + tests/smoke_gui_theme.py + docs/ui-design-2026-11-10.md | W（GUI 六轮：状态自洽 + 配色 2026-11-11 凌晨） | 11-11 05:40–06:20 | 用户发现任务面板出现「已解出 / 未解出」自相矛盾行。根因：`ok = submitted or flag` 为真但本地没有 flag 时，状态写「已解出」而详情落到兜底文案「未解出」（典型场景：平台 `my_solved_ids` 说已解、本 agent 没记到 flag）。新增统一收口 `_board_finish()`：有 flag → 已解出；ok 但无 flag → **平台已解** + 「平台已解出（本地无 flag 记录）」（worker 也补了这个 note）；失败 → 未解出 + 具体原因（预算耗尽/超时/异常），并纳入终态停表。状态加配色 tag：已解出绿+浅绿底、已解出（跳过）绿字灰底、解题中蓝+浅蓝底、未解出/出错红+浅红底、待开始/已取消灰字白底。`smoke_gui_theme` 36 条（新增状态自洽+配色用例：四种收口、tag 颜色、平台已解停表、事件路径同口径）；44 个 smoke 全绿、GUI selftest OK |
| src/ctfy_agent/solver.py + src/ctfy_agent/platform.py + tests/smoke_instance_id_repair.py | W（本地 plat 测试监视轮 2026-11-11） | 11-11 17:55–18:05 | 本地 plat 测试里 `verify_exploit` 的 instance_id 被填成 host/host 前缀（SAMPLE-008 两次 404、2026-10-29 SAMPLE-007 同形态）→ 新增 `looks_like_instance_uuid()` + solver 参数修正：**只**把「本题实例回执里出现过的 host / host 前缀」纠回登记的 UUID，合法 UUID / 别的 UUID / 无关值一律不动；已失败回执追加含本题 UUID 的确定性提示；无实例时仍抛原 PlatformError。新增 21 条回归（含「正确 UUID 判题通过仍返回 solved=True 并落库 submitted / 正确路径零纠正记录」的不破坏断言）。44 个 smoke 文件全绿（smoke.py 180/0、耗时 170s） |

| 文件 | 会话 | 区间 | 结果 |
|---|---|---|---|
| src/ctfy_agent/crew.py + src/ctfy_agent/gui/app.py + tests/smoke_gui_theme.py + docs/ui-design-2026-11-10.md | W（GUI 五轮：实时口径/费用 2026-11-11 凌晨） | 11-11 04:40–05:30 | 用户质疑「面板显示的是历史吧/边栏应该是总的/费用没显示」。查实：`Crew.solve` 的 `spending` 是**该题历史累计**（state.json 跨轮继承，预算闸需要），直接显示导致「面板一行 519147 / 顶栏 19457」两套口径打架。改成事件同时带 `snapshot_*`（累计，闸用）与 `run_*`（本次增量 = 快照−开跑基线，含 run_tokens/run_calls/run_cost/run_prompt/completion_tokens）：面板行内只显示本次增量 + 绝对轮次（续跑取事件值不回退）+ 本次用时；顶栏显示**所有题本次增量之和**（上限 = 单题上限×题数）；离线单题也统一成「首条 budget 当基线做差」。花费：配了 `price_*` 用真实 cost（4 位小数），没配就按默认费率 2.0/8.0 元每百万 token 用 run_prompt/completion 估算并带 `≈` 前缀（开跑日志说明）。`smoke_gui_theme` 34 条（重写面板用例：历史 500000 但本次 1234 只显示 1234、顶栏合计 2000、上限×2、绝对轮次、无单价≈/有单价真实值、老口径兼容）；43 个 smoke 全绿，GUI selftest OK |
| src/ctfy_agent/gui/{app.py,theme.py} + tests/smoke_gui_theme.py + docs/ui-design-2026-11-10.md | W（GUI 四轮：设置页主题化 + 子弹窗 2026-11-11 凌晨） | 11-11 03:40–04:30 | 设置对话框整窗换成与主界面同一套：毛玻璃标题条 + 圆角胶囊页签（平台档案/模型/预算，选中蓝底白字、未选中白底深字、间距 8px）+ 白色卡片 + `RoundedEntry`（Token/API Key 走 `show="*"`，给 RoundedEntry 补了 show 参数）+ 主题圆角按钮；**尺寸自适应**：按页内容期望尺寸算窗口大小再夹到屏幕 90%，窗口可缩放、每页 `ScrollFrame` 缩小时出滚动条；工具映射 / 一键接入两个子弹窗同步换主题（背景/文本框/按钮，尺寸按 DPI 换算）。顺带把题库列宽收窄（40/130/64/260→38/110/58/180），避免平台表单把左栏撑宽挤压右栏（横条仍可滚，xview≈0.83）。`smoke_gui_theme` 34 条（新增设置对话框用例）、43 个 smoke 全绿、GUI selftest OK。⚠️ 中途一次补丁用错锚点把 app.py 写坏，已从 HEAD 恢复并重做，最终 diff 已人工复核 |
| src/ctfy_agent/crew.py + src/ctfy_agent/gui/{app.py,theme.py} + tests/smoke_gui_theme.py + tools/gui_preview.py + docs/ui-design-2026-11-10.md | W（GUI 三轮：表格滚动条 + 任务面板列 2026-11-11 凌晨） | 11-11 02:40–03:30 | ① 题库/已解出/任务面板三张表全改 grid 布局，**上下+左右滚动条**都齐（列宽固定不 stretch，长题名/长详情/整段 flag 拖横条看全）；顺手修「所有滚动条都看不见」的老问题：ttk 滚动条厚度由样式 `arrowsize` 决定，原先 `arrowsize=0` 退化 1px，现 `ui_px(11)`（150% 屏 ≈16px）。② 任务面板「轮次/Tokens/花费」不动：平台批量走 `Crew.solve`，它原来只发 `budget_plan`，不发 GUI 依赖的 `budget`/`step` → 在 Crew.solve 补发（开跑基线 / 每轮 step+budget / 收口 budget）；花费未配单价（price_*=0）时显示 `-` 并在开跑日志提示，配了显示 4 位小数；预览工具新增 `--mode platform`。`smoke_gui_theme` 30 条、43 个 smoke 全绿、GUI selftest OK |
| src/ctfy_agent/gui/{theme.py,app.py,platform_worker.py} + src/ctfy_agent/crew.py + tests/smoke_gui_theme.py + tools/gui_preview.py + docs/ui-design-2026-11-10.md | W（GUI 二轮：清晰度/页签/计时 2026-11-11 凌晨） | 11-11 01:20–02:30 | 用户反馈三件事：① 字体要清晰 → 建窗**前**声明 Per-Monitor V2 DPI 感知 + `tk scaling=(dpi/72)*0.9`（96dpi 仍 1.2、150%→1.8，等效像素比不变但按物理像素渲染），行高/列宽/左栏宽度改 `theme.ui_px()`，表格正文 11pt/表头 10pt 粗体；② 复现/任务面板/日志三页签改自绘 `theme.TabBar`：选中主色填充+白字、未选中白底+深字、圆角 10px、间距 8px（ttk.Notebook 做不到）；③ 「任务面板瞬间一堆已解出且还在计时」：`_board_tick` 原来对所有行重算 `now-started` → 加 `finished` 停表；并查清「秒解」真相 = `Crew.solve` 两层已解出预检（state `submitted_ok` / 平台 `my_solved_ids`）零成本返回 → worker 标记 `presolved`，界面显示「已解出（跳过）」+ 日志注明「未重烧」；同时把「重跑已解出」做成真的：GUI→`solve_many(force_ids)`→`Crew.solve(force_rerun=True)` 跳过两层预检（默认 False，其它调用逐字不变）。`smoke_gui_theme` 25 条、43 个 smoke 全绿、GUI selftest OK |
| src/ctfy_agent/gui/{theme.py（新建）,app.py} + tests/smoke_gui_theme.py（新建） + tools/gui_preview.py（新建） + docs/ui-design-2026-11-10.md（新建） | W（GUI 视觉改版 2026-11-10 深夜） | 11-10 23:30–11-11 01:10 | 按用户给的 CSS 规范重做 tkinter 界面：毛玻璃 + GitHub 配色、Inter Bold/Regular（中文字体回退）、大标题逐字绘制 -0.02em、200ms `cubic-bezier(.4,0,.2,1)` 过渡的圆角按钮（8px/12×24/悬停上浮 2px/按下 0.98）、20px 圆角卡片（0.05 黑边框 + 双层阴影）、12px 圆角输入框（#F9FAFB/#9CA3AF/聚焦 2px 主色）、15 个 Feather 图标、渐变分隔线、骨架屏+转圈加载。三个功能需求：跑题库「跳过/重跑已解出」（读 `runs/<rid>/state.json`）、复现栏按日期分层（Treeview 日期父节点）、左栏与各列表滚动条。修了分栏容器误挂 root 导致左栏被挤出视口的问题（默认窗口改自适应 1320×900，开始/停止免滚动可见）。`smoke_gui_theme` 20 条、`smoke.py` 180/0、43 个 smoke 文件全绿、GUI selftest OK；业务逻辑零改动 |
| src/ctfy_agent/agent_main.py + src/ctfy_agent/crew.py + config/agent.json + roles/ai.* + tests/smoke_agent_qa.py + tests/smoke_ai_role.py + docs/plan-prelim-2026-11-15.md | W（资格赛/初选赛聚焦轮 2026-11-10 深夜） | 11-10 22:40–23:30 | 审计终版里 11-14/15 相关的三件小改：① `LLM_ENDPOINT_ALLOWLIST`（手册 12 家）+ `_llm_endpoint_note` → `result.json` 恒有 `llm_endpoint_alert` 字段 + stderr 告警（只告警不拦截）；② `_final_rollup` writeup 追加 `degraded_run_mode`；③ 提示词两条纪律（verify_answer 反穷举 / 平台漏洞转申报，coordinator + ai 双处，三源同步）。`smoke_agent_qa` 6/6（新增 2 用例）、`smoke_ai_role` +3 断言、`smoke.py` 175/0；plan-prelim 追加 §7 现场监控 runbook |
| docs/audit-rules-v2.5-2026-11-10.md（第二遍全量细读扩写）+ runs/_rules/*（PDF 提取件，gitignored） | W（规则审计 2026-11-10 第二遍） | 11-10 22:00–22:40 | 逐页读完 v2.5 手册 7 页，把第一遍 7 处扩成 **15 处**（新增：LLM 注入回落链路、本队题得分规避、出题约束清单〔镜像白名单/六禁止/每轮配额/倒扣分〕、公开仓库保密边界、平台漏洞处置义务、10h 吞吐、隔离纪律、白名单 12 家口径），更新 `241c845` 已修状态 + 比赛事实卡。PDF 提取：pypdf 7.0.1（wheel 手工解包，pip 临时目录 ACL 坑绕开），全文存 `runs/_rules/rules_text.txt` |
| src/ctfy_agent/instance_guard.py + src/ctfy_agent/solver.py + src/ctfy_agent/gui/platform_worker.py | W（接盘 2026-11-11 二次轮） | 11-11 只读 + 离线 | 补「无 `list_instances` 平台」的按题原语（`supports_reap/query_environment/environment_live/delete_environment`），并把 `_recover_instance_slot` 的失败恢复扩成「查→复用 / 明确不存在才删一次→重试」。只读实测 DASCTF ctf2 无列表能力（`ctf2_get_environment` 回 `{"environment":{"status":"none"}}`）。`tests/smoke_instance_recovery.py` 24 条绿；`smoke.py` 175/0、p1/p2/gate/evidence/brake/clustera/clusterb 全绿 |
| src/ctfy_agent/crew.py + src/ctfy_agent/agent_main.py | W（接盘 2026-11-11 二次轮） | 11-11 离线 | ① 每题写「生效预算+来源」note + `budget_plan` 事件；② `COORDINATOR_PROMPT` 加「flag 必须来自靶标真实产物（仓库/样例 `flag.txt` 不算答案）」纪律。`smoke_action_loop.py` 25 条绿 |
| tools/analyze_rejections.py（新建）+ tests/smoke_instance_recovery.py（新建）+ docs/HANDOFF-2026-11-10.md | W（接盘 2026-11-11 二次轮） | 11-11 | 被拒答案溯源工具（证据：8 条被拒答案前缀全对、来源为公开仓库/样例占位 flag）+ 文档回填 §7.2/§8.1b/§8.2/§9/§10 |
| docs/HANDOFF-2026-11-10-r2.md（新建）+ docs/HANDOFF-2026-11-10.md（顶部加指针） | W（接盘 2026-11-10 第二轮收尾） | 11-10 17:20 | 生成第二份独立接盘文件：现状/提交/批量结果/4 个 bug/机制速查/平台真值/验证清单/雷区/待办/文件速查/接手顺序 |
| src/ctfy_agent/tools_local.py + src/ctfy_agent/solver.py + src/ctfy_agent/instance_guard.py + src/ctfy_agent/crew.py | W（接盘 2026-11-11 二次轮·真实批量后） | 11-11 16:00–17:00 | 真实批量 3 题跑出 4 个新问题并修：① `practice_ground_id` 显式映射（schema 严格 + 服务端必填）；② 工具 `isError` 软失败不再当成功；③ `NOT_FOUND` 失败不做删环境重试；④ 跨 run 沙盒污染护栏 + 协调者提示词。回归：`smoke_instance_recovery` 29 条、`smoke_local_tools` 6 条新增、`smoke_action_loop` 25 条、`smoke.py` 175/0 全绿 |
| src/ctfy_agent/crew.py + src/ctfy_agent/agent_main.py | W（接盘 2026-11-10） | 11-10 接手轮 | 重复被拦动作断路器（`reason=action_loop`，`_STOP_MAP` 映射到 `max_turns`）。`smoke_action_loop.py` 23 条绿；`smoke.py` 175/0；对非重复动作字节透明 |
| src/ctfy_agent/tools_local.py + tools/shims/shim.py | W（接盘 2026-11-10） | 11-10 接手轮 | ① 修同主机换端口绕过 / 兄弟域名过度放宽（`smoke.py::case_hardening` 断言）；② shell 子进程挂 `tools/pylibs`（PYTHONPATH + `*.libs` DLL）与缺库提示；③ `rm -rf`→`rmdir /s /q`、`ls -la`→`dir /a`；④ shim `head/tail -c`。`smoke_local_tools.py` 全绿（ls 断言按新语义更新） |
| src/ctfy_agent/attachments.py（新建）+ src/ctfy_agent/gui/{platform_worker,app}.py | W（接盘 2026-11-10） | 11-10 接手轮 | 附件自动下载（真实 DASCTF 验证：unipickle.py 90B/sha256 `96309f79…`）+ 开跑前实例清扫 + 扫盘后台化。`smoke_attachments.py` 26 条绿 |
| src/ctfy_agent/mcp_client.py + gui/onboard.py + gui/toolmap.py + gui/ocr_tools.ps1 | W（接盘 2026-11-10） | 11-10 接手轮 | ① `McpHttpClient.close()`（只新增）；② `probe()` finally 关会话；③ OCR 去掉 PIL 依赖、`.ps1` 重写为 UTF-8 BOM+CRLF。`smoke_ps1.py` 绿、GUI selftest OK |
| config/agent.json + src/ctfy_agent/config.py + roles/{ai,misc}.* + tools/{fetch_pylibs,sync_roles,analyze_loop_waste}.py | W（接盘 2026-11-10） | 11-10 接手轮 | `max_llm_calls` 160→240（证据：`tools/analyze_loop_waste.py` 量化可省 ≥176 次）；角色三源重新同步；本地依赖库装上并 gitignore `tools/pylibs`；`.gitignore` 补该行 |
| docs/HANDOFF-2026-11-10.md + tests/{smoke,smoke_local_tools,smoke_ai_role,smoke_webai_gap}.py + run.cmd + .gitignore | W（接盘 2026-11-10） | 11-10 接手轮 | 文档回填本轮真值（§7.1/§8.1/§9/§10）；既有断言按新语义更新（175/0）；`run.cmd` 归一 CRLF |
| runs/_stats.py | Y（镜像/验证线） | 10-30 19:00–19:20 | 验收通过：bank-500k-v1 全指标复现（35/10、22/2/1、15,331,789 tok、23.5/39.0 calls）；门 A 后迁 tools/ 前重新上锁 |
| docs/archive/contest-harvest-checklist.md | Y（镜像/验证线） | 10-30 19:00–19:25 | M4 交付完成，单轮写完即释放 |
| runs/_p2v_batch.py | Y（镜像/验证线） | 10-30 19:40–19:55 | M2 两臂启动器完成，离线消融验证通过（A 臂 profile={}）；只写未跑，开跑需 --confirm-gate-a |
| runs/_audit_behavior.py | Y（镜像/验证线） | 10-30 20:20–20:50 | 行为审计器完成；三批实测（v0/paced/p0p1b）跑通，口径已写入 audit 文档 §1 |
| docs/archive/audit-behavior-2026-10-30.md | Y（镜像/验证线） | 10-30 20:50–21:00 | 审计结论 + O1–O3 优化待办（门 A 后实现） |
| docs/archive/spec-o1-http-cache.md | Y（镜像/验证线） | 10-30 21:10–21:25 | O1 实现级规格（只读调研所得，未改代码） |
| docs/archive/platform-submit-opsheet.md | Y（镜像/验证线） | 10-30 21:40–21:50 | 平台提交逐屏操作单（第 1 项交付） |
| docs/archive/spec-o2-o3-tools.md | Y（镜像/验证线） | 10-30 21:50–22:00 | O2 收窄规格 + O3 关闭记录（第 2 项交付） |
| docs/archive/note-zhipu-model-choice.md | Y（镜像/验证线） | 10-30 22:00–22:10 | 正式赛模型选型备忘（第 3 项交付） |
| docs/archive/audit-budget-exhausted-2026-10-31.md | X（agent 优化线） | 10-31 15:00–15:05 | 回填 O-A 实验结果（4/33 转化，预算非主因）+ 改建议顺序（已提交 82d5938） |
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
| docs/archive/bigbank-report-2026-10-31.md + docs/archive/bigbank-results-2026-10-31.csv | Y（镜像/验证线） | 10-31 06:00–06:30 | **大规模综合测试报告**：132 题实跑，77 解出（58.3%）；重叠 35 题 28.6%→48.6%；含环境限制与复现命令 |
| runs/_agent_forensics.py, runs/_rej_diag.py | Y（镜像/验证线） | 10-30 22:20–23:00 | 行为取证 / 拒收诊断工具（离线，零实例零 token） |
| docs/archive/audit-agent-2026-10-30.md | Y（镜像/验证线） | 10-30 23:10–23:55 | 本轮 agent 诊断与修复记录（C/A/B 三节 + 口径修正 + 门 A 待办） |
| runs/_timeline.py, runs/_err_infra.py, runs/_subset_score.py, runs/_access_scan.py | Y（镜像/验证线） | 10-30 23:25–23:50 | 单题时间线 / 预算去向与错误分类 / 可达性口径 诊断脚本（离线、零 token） |
| src/ctfy_agent/progress.py + src/ctfy_agent/solver.py | Y（镜像/验证线，门 A 已开） | 10-31 00:10–01:00 | **A1 环境失败断路器**：`failure_kind` 分类 + `EnvFailureTracker`（连续 ≥3 提示 / ≥6 收口 / 占比 ≥30% 预算提示），接进 `_solve_once` 与 `run_role` 两条循环；验收 `runs/_env_replay.py` 通过（SAMPLE-003 第 7 次调用即提示、SAMPLE-004 第 7 次） |
| src/ctfy_agent/tools_local.py + src/ctfy_agent/tools_misc_ext.py + config/agent.json + roles/{ai,misc}.json + tests/smoke_local_tools.py | Y（镜像/验证线，门 A 已开） | 10-31 00:20–01:10 | **B1 `write_file` 工具**（沙盒锚定 + 512KB 上限 + 建父目录，6 个角色 local 名单同步 config/roles 双源）/ **B2 Windows 命令别名自动改写**（python3→python、ls→dir…，不再直接拒收）/ **B5 http_burst 上限文案**；B4 复核为已满足（`decode_blob` 早已 required:["text"]）。验收 `tests/smoke_local_tools.py` 26 条全过 + 全量 smoke 回归绿（已提交 1e850ce） |
| src/ctfy_agent/tools_local.py + src/ctfy_agent/ledger.py + tests/smoke_http_cache.py | Y（镜像/验证线，门 A 已过） | 10-31 01:30–02:40 | **O1 多 pattern grep（`greps` 数组）+ 非 200 响应缓存 + `fresh=1`**；顺带修 `_cache_put` 的 `dict.popitem(last=False)` 潜伏崩溃、账本键补 greps/fresh。验收 `tests/smoke_http_cache.py` 24 条全过 + 全量 27/27 smoke 回归绿；规格 `docs/archive/spec-o1-http-cache.md` 已标注实现偏离（已提交 73b4a6a） |
| src/ctfy_agent/tools_local.py + tests/smoke.py + tests/smoke_burst_group.py | Y（镜像/验证线，门 A 已过） | 10-31 02:50–03:20 | **O2' burst 分组归一化**：`_burst_group_key`（payload 回显→`<V>`，时间/epoch/UUID/hex/长数字→`<N>`）+ 状态码进分组键；组增 `raw_count`、汇总增 `raw_distinct_bodies`；race 模式输出不变。验收 `tests/smoke_burst_group.py` 12 条全过 + `smoke.py` 既有 burst 断言按新语义更新后 144/0；规格 `docs/archive/spec-o2-o3-tools.md`，O3 关闭 |
| docs/archive/capability-gap-2026-10-31.md | X（agent 优化线） | 10-31 20:10–20:45 | 新增：各方向能力盘点（新建文件，无需互斥） |
| src/ctfy_agent/tools_local.py | Y → 已释放（O-G 判据） | 11-01 14:30–15:10 | 新增 _dns_unreachable_advice + 4 处请求错误路径接入 |
| src/ctfy_agent/crew.py | Y → 已释放（O-G 协调者规则） | 11-01 15:10–15:25 | COORDINATOR_PROMPT 加「接入层不可达→本轮只发 done」 |
| config/agent.json | Y → 已释放（O-G 提示词） | 11-01 15:00–15:20 | web system 加「内网不可达判据」 |
| tests/smoke_vpn_advice.py | 新建后已释放 | 11-01 15:05–15:20 | O-G 冒烟 5 条 |
| docs/archive/p0-calibration-2026-11-01.md | 新建后已释放 | 11-01 15:40–16:00 | P0 校准结果 |
| src/ctfy_agent/method_cards.py | X → 已释放（AI 方向） | 11-01 16:10–16:40 | 新增 ASI01-标识符 oracle 卡（20→21 张） |
| src/ctfy_agent/tools_local.py | X → 已释放（AI 方向） | 11-01 16:20–17:20 | chat 解析 tool_calls（JSON/OpenAI/SSE）+ 新增 extra 参数 |
| config/agent.json + roles/ai.system.txt | X → 已释放（AI 方向） | 11-01 17:20–17:35 | AI 提示词加「注入面不只在 message」（零漂移已校验） |
| tests/smoke_ai_oracle.py | 新建后已释放 | 11-01 16:30–17:40 | AI 方向冒烟 14 条 |
| tests/smoke.py + tests/smoke_ai_role.py | X → 已释放（AI 方向） | 11-01 17:30–17:40 | AI 卡张数断言 20→21 |
| docs/archive/ai-oracle-2026-11-01.md | 新建后已释放 | 11-01 17:40–18:00 | AI 方向补齐记录 |
| src/ctfy_agent/crew.py + src/ctfy_agent/solver.py + tests/smoke_ctxopt.py | **并行对话（非本线）** | 11-01 起 | S1b 轮间会话重置等；本线未触碰、不提交 |
| docs/archive/HANDOFF-2026-11-08-对外交接版.md + docs/OPENING.txt | Z（交接线） | 11-03 21:00–21:30 | 对外交接版重写（A–H+附录结构；关键数字全部标注实测口径；新增缺口 #11：总入口依赖未入库的 runs/bench_stats.py，干净 clone 151/1；B.4 列全未提交 WIP 处置清单）；OPENING.txt 同步浓缩；随本轮提交 |
| docs/archive/plan-p2v-validation-and-alignment.md | Y（镜像/验证线） | 10-30 19:00 起 | 悬置超 24h 过期，按约定第 3 条交用户裁决；2026-11-08 用户裁决释放。文档状态停在「方案待批」，WIP 横幅已删；真机验证线由 ctfarena/fix 分支后续实测报告取代（见 docs/archive/CTFARENA_FIX_REPORT.md） |
