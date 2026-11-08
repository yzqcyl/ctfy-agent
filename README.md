# TPCTF 2026 · 智能体团队赛道 — 解题框架

纯 Python 标准库（不装任何依赖）的多智能体 CTF 解题框架：1 个协调者 + 6 个方向专家
（web / pwn / rev / crypto / misc / ai），通过平台 MCP 工具解题并当场提交。
交付形态为**容器镜像**（`ghcr.io/yzqcyl/ctfy-agent-pub:v5`，详见 HANDOFF §B.2）。

> 一切关键数字以 [HANDOFF.md](HANDOFF.md)（口径标注版）为准；本 README 只做快速上手。

```
E:\ctfy_project\
├─ run.cmd                     # 入口：run.cmd doctor / whoami / challenges / crew / demo
├─ .env.example                # 令牌 + 模型 Key 模板（复制为 .env，.env 不进 git）
├─ config\agent.json           # 7 角色、预算、工具面（roles 与 roles\*.json 零漂移由 smoke 守卫）
├─ roles\                      # ai/misc 角色卡（json + system.txt）
├─ src\ctfy_agent\             # 框架本体（31 个模块：编排/平台/状态预算/工具/方法卡）
├─ tests\                      # 总入口 smoke.py（174 项断言）+ 30 余个独立套件
├─ tools\                      # container_check / publish_image / verify_image / secret_scan 等
├─ Dockerfile                  # 镜像构建（内置 ffuf/SecLists/sqlmap/capstone，版本+sha256 钉死）
├─ docs\                       # 80+ 篇：契约/方案/审计/实测报告/操作单
└─ runs\                       # 运行产物：state.json / transcripts/（gitignore）
```

## 四条命令上手

```powershell
# 1) 配密钥：复制 .env.example 为 .env，填 CTFY_TOKEN 与模型 Key
Copy-Item .env.example .env ; notepad .env

# 2) 只读自检：MCP 握手 + 工具清单 + 语义动作映射 + 模型配置
$env:PYTHONIOENCODING="utf-8"; $env:NO_PROXY="127.0.0.1,localhost"
.\run.cmd doctor

# 3) 看我是谁、报了哪场比赛（team / competition_id / 赛期）
.\run.cmd whoami
.\run.cmd competitions          # 所有可参与的赛事与练习集

# 4) 拉题单（自动选中你已报名的比赛；也可 --competition <id> 指定练习集）
.\run.cmd challenges
.\run.cmd crew  CTFARENA-001    # 协调者 + 6 方向专家分工（生产路径）

# 没令牌、没 Key 也能先看效果 —— 假 MCP + 脚本化 LLM 跑通全链路：
.\run.cmd demo
```

`run.cmd` 会自动找 Python（本机用 DSH 自带解释器，找不到才回落到 `python`）。
Windows 坑：先设 `PYTHONIOENCODING=utf-8`；本机有代理时必须 `NO_PROXY=127.0.0.1,localhost`。

## 模型（单模型契约）

| 项 | 值 | 改哪里 |
| --- | --- | --- |
| 模型 | `deepseek-v4.1-flash-1017` | `config/agent.json` 的 `model` 或 `DEEPSEEK_MODEL` |
| 端点 | OpenAI 兼容 `/chat/completions`（本地填哪家网关都行，当前用阿里云百炼） | `DEEPSEEK_BASE_URL` |
| 温度 | `0.2` | `temperature` |
| 单轮上限 | `4096` tokens（reasoning 烧穿时 P8 自适应放大） | `max_tokens` |

**比赛容器例外**：平台契约一次运行只允许一个 provider 的一个模型，角色级 `model`
会被入口剥掉；`LLM_API_KEY` 为空必须 refused，绝不回退自有 key。

## 费用上限（硬刹车）

token / 调用次数 / 金额三层刹车，每题累计、按题落盘、跨角色共享；重跑同一题会**接着算**，
不能靠重跑绕过。另有墙钟刹车（`AGENT_DEADLINE_*`）与缓存折价开关（`budget_cache_discount`，
实测输入成本 −44%）。

| 项 | 默认 | 说明 |
| --- | --- | --- |
| `max_tokens_per_challenge` | `800000` | 单题累计 token 上限（可用 `AGENT_MAX_TOKENS_PER_CHALLENGE` 覆盖调档） |
| `max_llm_calls` | `160` | 单题累计模型调用次数上限 |
| `max_cost` | `0.0` | 单题金额上限（0=不限，填单价才生效） |
| `submit_limit` | `3` | 单题提交次数上限 |

## 多智能体协作（crew）

`.\run.cmd crew <challenge_id>`：协调者（40 步）读题、派活、提交、收尾；6 个专家分工：

| 角色 | 步数 | 专长（本地工具去重 23 个） |
| --- | --- | --- |
| web | 20 | http_request/burst、fuzz、authdiff、jsrecon、sqli、jwt_forge、http_map |
| ai | 20 | chat（对端注入）、http、shell、decode_blob |
| pwn | 20 | pwngen（cyclic 拼装）、pwnfind（偏移反查）、elfpeek、tcp |
| rev | 20 | elfpeek（disasm/relocs/dynamic/bytecode）、hexpeek |
| crypto | 20 | rsatool（wiener/fermat/pollard…）、aesblock、prng、hexpeek |
| misc | 16 | decode_blob、unpack_archive、http、shell |

共享同一份笔记、实例与提交状态；三层刹车跨角色累计。分工同时就是权限边界：
专家碰不到 `submit_answer`，协调者碰不到 `http_request`/`shell`。
**注意**：角色加 `"model"` 无效（单模型契约，入口会剥掉）。

## 这套框架刻意做对的几件事

| 机制 | 为什么 | 在哪 |
| --- | --- | --- |
| **工具名自动归类** | 平台真实工具名未知，靠词义映射 + `tool_map` 兜底 | `platform.py` |
| **提交幂等** | 同 flag 只交一次、重复题号拦截、已判错回显"别再交" | `state.py` + `solver.py` |
| **写操作不重试** | `submit/start/stop` 失败先查状态，绝不自动重发；429 回滚失败计数 | `mcp_client.py` |
| **实例守卫** | start 失败先清残留再重试；每题 start 5 次硬上限，配额剩 1 次绝不重试 | `instance_guard.py` |
| **墙钟刹车** | token/calls 拦不住的"磨到 session 结束"，`reason=wall_clock` 可单独统计 | `budget.py` |
| **合规护栏** | `http_request` 只能打平台下发的实例；沙盒锚定防仓库根污染 | `tools_local.py` |
| **状态机落盘** | 断点续跑；题面指纹变了就丢弃 checkpoint | `state.py` + `checkpoint.py` |
| **转录留存** | 每题一条 JSONL，复盘、申诉、调 prompt 都靠它 | `state.py` |
| **方法卡** | 25 张题型打法卡按 tags 命中注入 | `method_cards.py` |
| **交接卡** | 每题收尾自动渲染五要素 handoff md | `handoff.py` |

## 自测

```powershell
& "$env:USERPROFILE\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe" tests\smoke.py
```

总入口 **174 项断言全绿**（CI 同跑）；另有 30 余个独立套件（`smoke_rev / smoke_pwn /
smoke_crypto / smoke_webai_gap / smoke_ai_*`…，其中需真实 `.env` 的 8 个在 CI 显式 SKIP）。
改动后必须全绿再提交；密钥扫描 `tools/secret_scan.py --all` 每次都跑。

## 现状（2026-11）

- **代码线**：`ctfarena/fix` 分支（含 CTFARENA 16 项修复：预算早停 / 实例就绪等待 /
  429 处理 / QA 通道 / 多问题提交等），已推送 origin；v3 为镜像基线线。
- **实测**：CTFARENA 115 题已解 85（34 平台历史 + 51 真实解出）；剩余 30 题归因见
  [docs/CTFARENA_FIX_REPORT.md](docs/CTFARENA_FIX_REPORT.md) 与
  [docs/CTFARENA_OPTIMIZATION_ANALYSIS.md](docs/CTFARENA_OPTIMIZATION_ANALYSIS.md)。
- **交付镜像**：v5 已构建推送并通过平台姿态探针；`ctfarena/fix` 的修复**尚未**进镜像，
  赛前需决策是否重建 v6（新 tag 新 digest，勿覆盖已审核 tag）。
- 框架层缺陷已基本清完；进一步提升解出率的方向是更强模型 / 更高预算，而非继续调框架
  （实验依据见 HANDOFF §E 与上述两份报告）。
