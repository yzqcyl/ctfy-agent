# TPCTF 2026 · 智能体团队赛道 — 解题框架

纯 Python 标准库的多智能体 CTF 解题框架：1 个协调者 + 6 个方向专家
（web / pwn / rev / crypto / misc / ai），通过平台 MCP 工具解题并当场提交。
交付形态为**容器镜像** `ghcr.io/yzqcyl/ctfy-agent-pub:v7.2`（197.7 MB / 19 层，digest 见
[docs/submit-image-runbook.md](docs/submit-image-runbook.md)）。

> 文档地图：[docs/README.md](docs/README.md)（当前有效文档）｜能力现状：
> [docs/capability-report-2026-11-12.md](docs/capability-report-2026-11-12.md)｜
> 交接：[docs/HANDOFF-2026-11-10-r2.md](docs/HANDOFF-2026-11-10-r2.md)。
> 历史文档已归档到 `docs/archive/`（保留 git 历史，不再据此判断现状）。

```
E:\ctfy_project\
├─ run.cmd                     # 入口：run.cmd doctor / whoami / challenges / crew / demo
├─ run_gui.cmd / run_gui.pyw    # 本地可视化界面（仅本地开发用，**不进提交镜像**）
├─ .env.example                # 令牌 + 模型 Key 模板（复制为 .env，.env 不进 git）
├─ config\agent.json           # 7 角色、预算、工具面（与 roles\*.json 零漂移由 smoke 守卫）
├─ roles\                      # 6 个角色卡（json + system.txt）
├─ src\ctfy_agent\             # 框架本体（53 个模块：编排/平台/状态预算/工具/方法卡/GUI）
├─ tests\                      # 总入口 smoke.py（180 断言）+ 59 个独立套件
├─ tools\                      # container_check / publish_image / verify_image / secret_scan 等
├─ Dockerfile                  # 镜像构建（内置 ffuf/SecLists/sqlmap/capstone/pwntools/pycryptodome/gmpy2/sympy/z3）
├─ docs\                       # 14 篇当前文档 + docs\archive\（历史）
└─ runs\                       # 运行产物：state.json / transcripts/（gitignore）
```

## 四条命令上手

```powershell
# 1) 配密钥：复制 .env.example 为 .env，填 CTFY_TOKEN 与模型 Key
Copy-Item .env.example .env ; notepad .env

# 2) 只读自检：MCP 握手 + 工具清单 + 语义动作映射 + 模型配置
$env:PYTHONIOENCODING="utf-8"; $env:NO_PROXY="127.0.0.1,localhost"
.\run.cmd doctor

# 3) 看我是谁、报了哪场比赛
.\run.cmd whoami
.\run.cmd competitions

# 4) 拉题单 / 跑一题（crew = 生产路径：协调者 + 6 专家分工）
.\run.cmd challenges
.\run.cmd crew  SAMPLE-001

# 没令牌、没 Key 也能先看效果 —— 假 MCP + 脚本化 LLM 跑通全链路：
.\run.cmd demo
```

`run.cmd` 会自动找 Python（本机用 DSH 自带解释器，找不到才回落 `python`）。
Windows 坑：先设 `PYTHONIOENCODING=utf-8`；本机有代理时必须 `NO_PROXY=127.0.0.1,localhost`。

## 模型（单模型契约）

| 项 | 值 | 改哪里 |
| --- | --- | --- |
| 模型 | `deepseek-v4.1-flash-1017` | `config/agent.json` 的 `model` 或 `DEEPSEEK_MODEL` |
| 端点 | OpenAI 兼容 `/chat/completions`（本地填哪家网关都行，当前用阿里云百炼） | `DEEPSEEK_BASE_URL` |
| 温度 | `0.2` | `temperature` |
| 单轮上限 | `4096` tokens（reasoning 烧穿时自适应放大） | `max_tokens` |

**比赛容器例外**：平台契约为一次运行一个 provider 的一个模型，角色级 `model` 会被入口剥掉；
`LLM_API_KEY` 为空必须 refused，绝不回退自有 key。

## 预算与刹车（硬上限）

| 项 | 当前值 | 说明 |
| --- | --- | --- |
| `max_tokens_per_challenge` | `800000` | 单题累计 token 上限（跨角色/跨轮累计，重跑接着算） |
| `max_llm_calls` | `240`（代码兜底 160） | 单题累计模型调用次数上限；平台 `task.max_turns` 优先 |
| `max_steps` | `40` / 角色 | 每角色每轮步数 |
| `max_calls_per_step` | `8` | 一步内最多并行工具调用 |
| `oracle_limit` | `100` | judge/oracle 类调用上限 |
| `submit_limit` | `3` / 题号（平台 5 轮） | 本地幂等闸 + 平台提交轮次 |

另有墙钟刹车（`AGENT_DEADLINE_*`）、连接窗口（`connect_timeout=40s` / `connect_deadline=5400s`）
与缓存折价开关（`budget_cache_discount`）。

## 多智能体协作（crew）

`.\run.cmd crew <challenge_id>`：协调者（40 步）读题、派活、提交、收尾；6 个专家分工：

| 角色 | 步数 | 本地工具（去重后 27 个派发 + note/report/traffic 特殊通道） |
| --- | --- | --- |
| web | 40 | http_request/http_map/http_burst/fuzz/authdiff/jsrecon/sqli/jwt_forge/decode_blob/shell/write_file(+traffic) |
| ai | 40 | http_request/http_map/chat/shell/write_file/decode_blob |
| rev | 40 | shell/write_file/elfpeek/hexpeek/tcp/tcp_batch/netcheck/listen/z3solve |
| crypto | 40 | shell/write_file/hexpeek/rsatool/aesblock/prng/tcp/tcp_batch/netcheck/listen/z3solve/pycrypt |
| pwn | 40 | shell/write_file/elfpeek/hexpeek/pwngen/pwnfind/tcp/tcp_batch/netcheck/listen/z3solve/pwnrop |
| misc | 40 | http_request/http_map/shell/write_file/decode_blob/unpack_archive/hexpeek/tcp/tcp_batch/netcheck/listen/z3solve |

共享同一份笔记、实例与提交状态；预算刹车跨角色累计。分工同时就是权限边界：
专家碰不到 `submit`，协调者碰不到 `http_request`/`shell`。模型只能看到平台白名单里的 28 个
MCP 工具（`list_instances`/`stop_instance`/`submit_answer` 等只给 host 侧 CLI）。

## 这套框架刻意做对的几件事

| 机制 | 为什么 | 在哪 |
| --- | --- | --- |
| **工具名自动归类** | 平台真实工具名未知，靠词义映射 + `tool_map` 兜底 | `platform.py` |
| **提交幂等 + 换值重交** | 同 flag 只交一次；按题号记错交额度，换题号不被别的题号锁死 | `state.py` + `solver.py` |
| **写操作不重试** | `submit/start/stop` 失败先查状态，绝不自动重发；429 回滚失败计数 | `mcp_client.py` |
| **实例守卫 + 负缓存** | start 失败先清残留再重试；5 次硬上限；平台明说「无环境」的题不再自启 | `instance_guard.py` + `solver.py` |
| **墓碑自愈** | 实例端口失效时按题重建（无 `list_instances` 平台用按题原语） | `solver.py` |
| **墙钟刹车** | token/calls 拦不住的"磨到 session 结束"，`reason=wall_clock` 可单独统计 | `budget.py` |
| **合规护栏** | `http_request` 只能打平台下发的实例；沙盒锚定防仓库根污染 | `tools_local.py` |
| **状态机落盘** | 断点续跑；题面指纹变了就丢弃 checkpoint | `state.py` + `checkpoint.py` |
| **转录留存** | 每题一条 JSONL，复盘、申诉、调 prompt 都靠它 | `state.py` |
| **方法卡** | 题型打法卡按 tags 命中注入 | `method_cards.py` |
| **交接卡** | 每题收尾自动渲染五要素 handoff md | `handoff.py` |

## 提交镜像的范围红线（用户 2026-11-12 定，重建必须遵守）

- **不得打包**：① 可视化界面 `src/ctfy_agent/gui/`；② 多平台连接适配器
  `gui/platform_adapter.py`、`platform_worker.py`、`profiles.py`、`toolmap.py`。
- **允许**：跨平台库连接能力（`pylibs.py` 的 `CTFY_PYLIBS` 搜索、`tools/fetch_pylibs.py`、Linux 库目录约定）。
- 强制手段：`.dockerignore` 整包排除 `src/ctfy_agent/gui/`；`Dockerfile` 构建期断言
  `find_spec('ctfy_agent.gui') is None`（误加回即构建失败）。详见
  [docs/submit-image-runbook.md](docs/submit-image-runbook.md) 顶部红字。

## 自测

```powershell
# 全量（59 个套件，约 4 分钟）
$env:PYTHONPATH='src;tools/pylibs'; $env:PYTHONIOENCODING='utf-8'
& "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" -u runs\_mon\run_all_tests.py

# 总入口单测（180 断言）
& "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" tests\smoke.py
```

改动后必须全绿再提交；密钥扫描 `python tools/secret_scan.py --all` 每次都跑。
本机跑测试建议加 `-B` / `PYTHONDONTWRITEBYTECODE=1`（火绒会把部分 `.pyc` 误判成后门并删除，见 HANDOFF §7）。

## 现状（2026-11-12）

- **代码线**：`ctfarena/fix` 分支；HEAD 含 v7.2 镜像对应代码 + libs 接入（pwntools/pycryptodome/z3 等）
  + P2 三修（无环境负缓存 / 平台 attempt 回写 / ctf2 墓碑按题自愈）。
- **镜像**：v7.2 已构建并通过匿名拉取；**v7.3 待建**——v7.2 漏装 z3-solver（`z3solve` 工具在镜像内会缺库），
  已在 Dockerfile 补齐，重建后需确认构建日志有 `z3 selftest ok`。
- **题库实测**（`runs/plat/state.json`，2026-11-10 数据）：SAMPLE 自建题 17/17；外部真实题 13/26
  （REVERSE 8/8、FORENSICS 2/4、MISC 3/5、**PWN 0/4、CRYPTO 0/5**）。归因与下一步见能力评估报告 §3/§8。
- **待办**：PWN/CRYPTO 真实题复验（烧平台配额，需用户批准）；格攻击/磁盘镜像取证工具缺口（未做）。