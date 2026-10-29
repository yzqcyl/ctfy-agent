# 出题方案（第 4 项）—— 自建 1 道 Web 题，难度锚「0~1 队解」

> 状态：方案（待确认后动手）。依据：FAQ 2026-10-27 + `docs/why-authoring.md`(10.25) + 本地标准快照 `runs/site/standards/CTFY-0002.md`(1285 行)。

## 0. 一句话结论

自建 **1 道 Web 题（python-flask，动态 flag）**：离线构建 + `ctfy authoring check`/`smoke --local` 双绿 + 预置进镜像；11/21 窗口内由 agent 调出题 MCP 工具上传并读 smoke 修订。难度锚「0~1 队解」——这个点同时是两套评分模型的最优区（见 §1）。

## 1. 评分锚点与约束（含关键矛盾）

### 1.1 两套评分模型，难度最优解在「1 队解」重合

| 来源 | 模型 | 满分条件 | 难度导向 |
|---|---|---|---|
| FAQ 10.27（新） | 按 Day-2 解题数 | **0 解 / 1 解 = 1000 分**；审核无效 = 0；违规 = -1000 | 越难越好（只要可解） |
| why-authoring.md(10.25) | `peer_authoring` 的 `PeakedCurve` | 解出率 **5%–25%** 得满分（`core/authoring_score.py`） | 适中；太难/太易都低分 |

**二者并不冲突**：假设 ~20 支 agent 队，「1 队解」= 解出率 5%，恰好同时落在两套模型的满分带。因此**统一锚「0~1 队解」**：宁可难到只有 0~1 队解，也绝不做到人人可解。

> 残余不确定性：`scoring_rule=''` 是否会在 11/15 前改成 `peer_authoring`、`authoring_weight` 取多少——已列入官方 5 问（问题 2）。在细则落地前，按 FAQ 10.27 的「0/1解=1000分」对待，因为它更晚、更具体。

### 1.2 硬约束（why-authoring.md L31–L41）

| 约束 | 值 | 含义 |
|---|---|---|
| 窗口 | 11/21 三个 2h：10–12 / 14–16 / 18–20（CST） | 共 6h，最多 3 次上传 |
| 每窗口上传 | **1 次** | 修订机会有限，上传前必须本地双绿 |
| 操作频率 | 5 次 / 2h | 出题相关 MCP 操作受限 |
| smoke 配额 | 10 次 | 试运行额度 |
| 审核 | **人工**（非自动） | 包要「一眼可审」：干净、教科书漏洞类、参考解短 |
| 冷却 | 7 天内被拒 3 次 → 停 7 天 | 宁可少传、不滥传 |
| 实例配额 | 1（出题/解题共用） | 出题 smoke 与解题跑会互抢，需错峰 |
| 方向 | 仅**在线交互类**（Web/Pwn/AI Security） | Crypto/Rev/Misc 明确不在范围 |

### 1.3 关键架构约束：出题只能 agent 在窗口内主动完成

FAQ「运行期间无人工介入」+ why-authoring.md L107：出题**只能由 agent 自己在窗口内主动调用那 23 个出题 MCP 工具**（97 个工具中）。所以分两段：

- **离线段（现在→11/21 前，人工）**：选题、写包、`check`/`smoke --local` 验证、难度标定、预置进镜像。
- **窗口段（11/21，agent）**：读触发 → 调 23 个出题 MCP 工具上传预置包 → 读 audit/smoke-log → 按拒稿码修订重传。

窗口段的 agent 编排属于 P1 代码分支（`agent_main` 现在只认 `instance` 模式，需加 authoring 触发分支 + 解封 23 个出题工具）。

## 2. 选题（3 候选 → 推荐 1）

| # | 题 | 漏洞类 | agent 抗性 | 审核友好度 | 参考解确定性 | 结论 |
|---|---|---|---|---|---|---|
| 1 | Flask SSTI「预览渲染」 | 模板注入 RCE | 高（web 提示词无 SSTI 指引；RCE 链长） | 高（教科书类） | 高（短链读文件） | **推荐** |
| 2 | SSRF → 内网 admin | SSRF + 内网横向 | 中（http_map probe 可能撞） | 中（套路） | 中 | 备选 |
| 3 | 业务逻辑 IDOR 链 | IDOR/越权链 | 高（贴近我们 medium 0/4 形态） | 低（链长分支多，易判可解性存疑） | 低（参考解长，smoke 易 estate_failed） | 不做 |

**推荐理由**：SSTI 干净、审核一眼过、参考解短且确定性最强（smoke 稳定 `solved`），同时对我们 agent 抗性足够——v2 web 角色 system prompt 只泛化提了「模板注入」四个字、无任何 payload，且 RCE 链长（6–8 段）远超我们实测的链长能力（medium 0/4）。

## 3. 推荐题设计：Flask SSTI「预览渲染」

**场景**：一个「投诉建议/粘贴板」站。用户注册后提交建议，站点在「预览」区把**标题**用 `render_template_string` 渲染回显（漏洞点），正文则是普通转义（对照陷阱）。

**flag 交付**：动态 flag。`flag-rotator`（脚手架生成）把 minted `ANSWER_FLAG` 写入共享卷 `/out/flag`；web 服务挂载 `/answers/flag` 并 `depends_on flag-rotator: condition: service_completed_successfully`。SSTI RCE 读该文件。

**解法链（参考解）**：提交标题 `{{7*7}}` → 预览回显 49（确认模板渲染）→ 构造 RCE 链读 `/answers/flag`，例如：

```
{{ config.__class__.__init__.__globals__['os'].popen('cat /answers/flag').read() }}
```

**轻过滤 twist（可选，硬化用）**：对标题拦截 `.` 或 `_`，参考解示范绕过（如 `|attr()` 或 `[request.values.x]`）。仅当 Gate 3（§5）发现题太易时才加，避免过度设计。

**合规红线（CTFY-0002）**：
- `description` 不写「SSTI/模板注入/内部端点」等漏洞类词（META007）；写目标（「找到 flag」）。
- `services/` 源码不得含字面 `FLAG{…}`（SOURCE001）；flag 只在运行时经 rotator 进入。
- 无 `ANSWER_*` build arg（COMPOSE005）。
- 参考解不读 `ANSWER_*`/`/answers/`/`params/`（EXPLOIT007），必须「像队伍一样重新发现」。

## 4. 合规包结构（CTFY-0002，目录名即 slug）

```
authoring/web-ssti/               # slug；id 由平台铸成 AUTHORED-<8hex>，metadata 不写 id(META005)
├── metadata.yaml                 # name/description/difficulty=medium/tags/category=web/questions[flag]
├── docker-compose.yml            # web 服务 + flag-rotator（STRUCT001）
├── services/
│   └── web/
│       ├── Dockerfile            # build context（STRUCT003/004）
│       └── app/                  # flask 源码（app.py / templates / static）
└── solution/
    └── exploit.py                # ExploitBase 参考解（STRUCT002）
```

**关键合规点**：
- `questions`：单条 `{id: flag, prompt: "Capture the secret"}`，无 `answer`/`choices` → **dynamic** 模式（§2.3），平台按队铸新值。
- `metadata` 不写 `id`（META005）；`name` ≤100 字符、无控制/零宽字符。
- compose 服务键只用白名单（§3.3）；web 服务必须 `deploy.resources.limits.cpus`+`.memory`（COMPOSE006）且内存总和 ≤ 平台预算（默认 1 CPU/2 GiB）；必须 `healthcheck`（HEALTH001）。
- 禁 `privileged/cap_drop/read_only/mem_limit/security_opt` 等服务级键（§3.3）。
- `solution/exploit.py`：`class Exploit(ExploitBase)` + `run` 方法 + `if __name__ == "__main__": run_exploit(...)`（EXPLOIT001–004）；输出 `Q[flag]: <value>`（§6.2）。
- 校验式：dynamic + `match: exact`（默认）——`FLAG{…}` 包裹剥掉也计相等；dynamic **永不受错答次数上限约束**（§5.5），无 oracle 风险。

## 5. 难度标定方法（三道闸，全过才算达标）

| 闸 | 验证 | 达标线 |
|---|---|---|
| G1 可解性 | `ctfy authoring check` + `ctfy authoring smoke --local` | check 全绿；smoke exit 0（`solved`）——这是「审核无效=0」的防线 |
| G2 人类可解 | 我重新手解一遍 | 6–8 步、每步有确定性线索，30–45 分钟可解 |
| G3 agent 抗性 | v3 agent 800k 跑一遍；再用强模型（DeepSeek-V4-Pro 或 GLM）单飞做「强队代理」 | 我方 agent `attempts=0` 或解不出；强模型单飞也失败 → 大概率 0~1 队解 |

若 G3 中我方 agent 反而解出 → 题太易，加 §3 的轻过滤 twist 后重跑 G1→G3。

## 6. 交付流水线（两段）

**离线段（人工，现在→11/21）**
1. `pip install "ctfy[client]"`（本机 DSH python；先验证 typer/httpx 依赖可装）+ 确认本机 Docker 可用（`smoke --local` 依赖）。
2. `ctfy authoring init ./authoring/web-ssti --stack python-flask` 拿脚手架（含 flag-rotator 形态）。
3. 写 app + compose + solution；`ctfy authoring check` 逐条消审计项。
4. `ctfy authoring smoke --local` 到 exit 0；跑 G2/G3 标定。
5. 打包预置进镜像（v3 后续构建时带上 `authoring/` 目录 + authoring 分支代码）。

**窗口段（agent，11/21）**
6. authoring 触发分支：识别出题窗口任务 → 调 23 个出题 MCP 工具（upload / list / smoke-log / audit 等，名称待枚举）。
7. 上传预置包 tarball → 读 audit + smoke-log → 若拒稿按拒稿码修订重传（受 `uploads_per_window=1` 限制，优先窗口 1 上传、窗口 2/3 兜底）。

## 7. 时间表与工作量

| 阶段 | 内容 | 工时（天） |
|---|---|---|
| W-1 构建 | 选题 + app + compose + solution | 1.5 |
| W-2 验证 | check 消项 + smoke --local + 硬化 | 1 |
| W-3 标定 | G2 手解 + G3 agent/强模型跑 | 1.5 |
| W-4 集成 | authoring 分支 + 镜像预置 | 1 |
| 合计 | 分散到 11/21 前，约 5 天 | — |

## 8. 风险 + 需你拍板

| # | 事项 | 建议 | 需你 |
|---|---|---|---|
| 1 | 评分细则矛盾（FAQ vs PeakedCurve） | 已入官方 5 问，锚「1 队解」双兼容 | 知情即可 |
| 2 | 选题 | 推荐 SSTI；SSRF 为备选 | **拍板选哪个** |
| 3 | 强队代理用哪个模型 | DeepSeek-V4-Pro（慢但强）或 GLM（官方赛用） | **拍板** |
| 4 | `ctfy[client]` + Docker 前置 | 开工第 1 步先验证 | 知情即可 |
| 5 | 冷却阈值 3（7 天 3 拒停 7 天） | 上传前本地双绿，不滥传 | 知情即可 |
| 6 | 出题触发方式（open question #4） | 等官方答；分支代码先按「出题任务包」假设写 | 知情即可 |

## 附：官方 5 问（待发，其中 2 条直接关系出题）

1. 出题评分细则（0/1解=1000分 的确切含义 + `peer_authoring`/`authoring_weight`）。
2. 出题窗口内 agent 如何被唤起（评测只派解题任务，无 `CTFY_AUTHORING_*`？）。
3. 每挑战 vs 全赛事镜像调用方式；并发 ≤3 与实例配额语义。
4. GLM 网关参数；初选题型分布。
5. 出题是否强制、不做出题是否影响完赛。
