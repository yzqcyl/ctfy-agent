# 平台提交操作单（浏览器，人工执行 / 2026-10-30 版）

> 目标：把已有镜像 `ghcr.io/yzqcyl/ctfy-agent-pub:v3` 注册进平台、过检查、提交审核、跑免费样例。
> 前置事实：v3 已构建（Actions run `40837543846` 全绿）、匿名可拉取已验证、digest
> `sha256:e658b9f6a466209813d1278bfa379b38e0275cf5fe0f0d8f41459f423bbf8a56`、`User=app`（非 root）。
> 全程**不需要**等并行会话/门 A；v4 以后可更新版本。

## 屏 1：配置本次测评 → 镜像地址

- 填：`ghcr.io/yzqcyl/ctfy-agent-pub:v3`（**显式 tag，绝不填 `latest`**）
- 点「重新检查 / 注册并检查」
- **预期**：注册 ✓、协议检查（非 root）✓、试运行 ✓、审核 ✓——五项全绿
- 失败排查：
  | 现象 | 处置 |
  |---|---|
  | 拉取失败 / 4xx | 核对拼写；远端复核：`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v3` |
  | 「以 root 运行」 | 镜像选错（v3 已 `User=app`），返回核对地址 |
  | 契约检查失败 | 点详情看哪条；v3 CI 五条契约全绿，对照 `docs/submit-image-runbook.md` 历史 |
  | 平台「维护中」 | 等 10–30 分钟重试，别高频刷 |

## 屏 2：模型与密钥

| 字段 | 填写 | 说明 |
|---|---|---|
| 模型 | 目录里选 **DeepSeek V4 Flash** | 评测用模型 |
| 提供商 | **OpenAI** | = openai 方言，**必须**；填错→容器 `stop_reason=refused` |
| 付费方 | 自己的 key（付费方选我） | 初赛自备 key |
| 密钥保存 | 名称如 `dashscope-deepseek`，密钥 = `.env` 里的 `DEEPSEEK_API_KEY`，备注 `dashscope` | 密钥**不要截图/外传** |
| （若有）Base URL | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 只有平台提供该字段才填 |

- 失败排查：密钥无效 → 去百炼控制台确认 key 有效、有额度、地域正确；换 key 后重新保存再检查。

## 屏 3：高级设置（若有）

| 项 | 值 |
|---|---|
| 每题采样次数 | 1 |
| 单次运行时限 | 0（不限） |
| 最大模型轮数 | 0（不限） |
| 停止派发的费用阈值 | 2（USD） |

## 屏 4：提交审核 → 跑免费样例

1. 「提交审核」→ 等通过（人工/平台侧，周期不可控，这是唯一外部依赖）；
2. 通过后 →「测评我的 Agent」→ 跑免费样例（少量题）。

## 屏 5：结果与观察点（跑完必须回看）

| 观察 | 期望 | 异常含义 |
|---|---|---|
| 容器退出码 | 0 | 非 0 = 入口/环境问题（对照 v3 CI 契约） |
| `stop_reason` | `done` / 正常结束 | **`refused`** = 提供商方言/密钥问题（回到屏 2） |
| `result.json` | schema_version=2，有 answers | 空回答 + `halted` = 思考烧 token 的已知风险点（记录，不急着改） |
| 日志 | stderr 有结构化事件 | stdout 不被平台读取，正常 |

## 证据留存（回填文档）

- 截图：注册检查页、审核状态、样例结果页；
- 记录：提交时间、镜像 digest、平台 run id、样例 solved/总数、stop_reason 分布；
- 回填：`docs/submit-image-runbook.md` 顶部表格 + `HANDOFF.md`（门 A 后统一对账，避免与并行会话冲突）。

## 之后

- v4 出来（toolbox + P0/P1/P2）→ 新 tag `:v4`，**别 move `:v3`**；平台允许更新镜像版本，重新走屏 1。
- 已提交版本按 digest 钉住；同一版本不重复 push。