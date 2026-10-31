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
| docs/plan-p2v-validation-and-alignment.md | Y（镜像/验证线） | 10-30 19:00 | 方案已批；按门推进时更新，门 C 后释放 |
| docs/audit-budget-exhausted-2026-10-31.md | X（agent 优化线） | 10-31 15:00 | 回填 O-A 实验结果行 + 改建议顺序；写毕即释放 |

## 已释放

| 文件 | 会话 | 区间 | 结果 |
|---|---|---|---|
| runs/_stats.py | Y（镜像/验证线） | 10-30 19:00–19:20 | 验收通过：bank-500k-v1 全指标复现（35/10、22/2/1、15,331,789 tok、23.5/39.0 calls）；门 A 后迁 tools/ 前重新上锁 |
| docs/contest-harvest-checklist.md | Y（镜像/验证线） | 10-30 19:00–19:25 | M4 交付完成，单轮写完即释放 |
| runs/_p2v_batch.py | Y（镜像/验证线） | 10-30 19:40–19:55 | M2 两臂启动器完成，离线消融验证通过（A 臂 profile={}）；只写未跑，开跑需 --confirm-gate-a |
| runs/_audit_behavior.py | Y（镜像/验证线） | 10-30 20:20–20:50 | 行为审计器完成；三批实测（v0/paced/p0p1b）跑通，口径已写入 audit 文档 §1 |
| docs/audit-behavior-2026-10-30.md | Y（镜像/验证线） | 10-30 20:50–21:00 | 审计结论 + O1–O3 优化待办（门 A 后实现） |
| docs/spec-o1-http-cache.md | Y（镜像/验证线） | 10-30 21:10–21:25 | O1 实现级规格（只读调研所得，未改代码） |
| docs/platform-submit-opsheet.md | Y（镜像/验证线） | 10-30 21:40–21:50 | 平台提交逐屏操作单（第 1 项交付） |
| docs/spec-o2-o3-tools.md | Y（镜像/验证线） | 10-30 21:50–22:00 | O2 收窄规格 + O3 关闭记录（第 2 项交付） |
| docs/note-zhipu-model-choice.md | Y（镜像/验证线） | 10-30 22:00–22:10 | 正式赛模型选型备忘（第 3 项交付） |
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
