# P3 方案（v3.3）：越权对照 + JS 侦察 + SQLi 自动化（浏览器挂起）

> 定稿依据：2026-11 定稿会。已拍板四项：
> 1. **范围** = Phase 1（`authdiff` + `jsrecon`）+ Phase 2（`sqlmap` / `sqli`）；
> 2. **体积红线** = 先量再定（CI 体积探针拿到真实数字后决策）；
> 3. **浏览器** = 挂起，等 Phase 0 探针结果再选路线（Playwright / CDP / xz 载荷 / 放弃）；
> 4. **本文件落库**，与 `docs/plan-v1.md`、`docs/b1-adapter-plan.md` 同级。

---

## 0. 为什么不是「原定 P3 = 浏览器」

原三阶段计划里 P3 定义为「C 浏览器 Playwright」。定稿时按实测证据重排：

- `HANDOFF.md` 结论：三臂实测 + SAMPLE-009 手动探测表明**失败根因是 flag 发现（access-control 面），非编排/调参可解**。浏览器不解决越权。
- 体积：v2 镜像实测 **126.5 MB 解压 / 47.2 MB 压缩**；自设门禁 400 MB（`container_check.py --max-mb 400`），可用预算 ≈ 270 MB。而 Playwright + `chromium-headless-shell` + apt 依赖预估 **+360~480 MB**（详见表 2），大概率顶破门禁。
- 结论：**先做零体积、直击瓶颈的两件（Phase 1），再做体积可控的 SQLi（Phase 2）**；浏览器降级为「量完再说」。

---

## 1. 硬约束（均已核实，非估算）

| 项 | 事实 | 来源 |
|---|---|---|
| 镜像体积基线 | v2：解压 126.5 MB / 压缩 47.2 MB | `docs/submit-image-runbook.md` |
| 体积闸门 | 我方自设 400 MB；主办方仅要求「不要太大」，无具体数值 | `container_check.py:242`、`b1-adapter-plan.md:429` |
| stdlib 自扫 | `case_stdlib_only` 扫 `src/ctfy_agent/**/*.py` 全量 → 任何第三方 import（含 playwright、pymysql）不得出现在 src/ | `tests/smoke.py:1348` |
| 工具契约 | `*_SPEC` 模块级常量 + 同名方法 + 参数名一致；`clean`（无默认值参数）必须快速 ToolError | `smoke.py` case_misc_contract |
| 角色门控 | `crew._role_specs` 按 `local` 名单挂 SPEC；`tools_local.specs()` 为框架全集 | `crew.py:401` |
| 运行期网络 | 只允许平台下发的实例域；构建期下载允许 | 平台规则 |
| 本地环境 | **无 Docker**，镜像体积/构建只能靠 CI 实测 | 既定事实 |

---

## 2. Phase 1：两个零体积工具

### 2.1 `authdiff` —— 越权对照探针（直击实测瓶颈）

**要解决**：只拿到一个会话的前提下，快速判定「这个端点是不是忘了鉴权」。手工做法是发两次请求肉眼比对，在 10 步预算里太贵。

**输入**（全部走 `http_request` 同一白名单 + 同一 cookie jar）：

| 参数 | 说明 |
|---|---|
| `url` | 必填，目标 URL |
| `method` | `GET`/`POST`（默认 GET） |
| `body` | 可选 |
| `headers` | 可选（不多于 6 个头） |
| `variants` | 变体集合，默认 `anon,bypass,path`，可只选其一 |

**三组变体**：

1. `authed`：原样（带当前 jar 会话）——基线；
2. `anon`：**剥掉 Cookie** 重放 → 若与基线同状态码同长度同内容哈希 ⇒ 匿名可读（水平/垂直越权线索）；
3. `bypass`：加常见绕过头（`X-Forwarded-For: 127.0.0.1`、`X-Original-URL`、`X-Rewrite-URL`、`X-Admin: true`）→ 与基线不同 ⇒ 服务端信任代理头；
4. `path`：路径变形（`/x` → `/X`、`//x`、`/./x`、`/x/`、`/x..;/`、`/%2e/x`、`/x%00`）→ 与基线不同（尤其 200↔403 翻转）⇒ 路径归一化绕过。

**输出**（≤6000）：基线三元组 + 每个变体的 `status/len/hash8/差异判定`，加一条「结论」字段（如 `匿名可读 = 疑似缺失鉴权`）。命中全部落 P1 流量库（`source=authdiff`）。

**安全**：只访问白名单内目标；请求数 ≤ 1 + 1 + 4 + 7 = 13，与 `http_burst` 同量级；不加任何危险开关。

**工作量**：≈ 150 行 + smoke 假服务器用例。

### 2.2 `jsrecon` —— JS 静态侦察

**要解决**：模型只有 10 步，SPA 题里「把 bundle 落盘再 grep」要烧 3~4 步；`http_map` 只抓静态链接/表单，抓不到 JS 里的 API 面。

**流程**：取 HTML → 收 `<script src>` + 内联 script + `sourceMappingURL` → 逐个拉取（同域、白名单、单个 ≤512KB、最多 6 个）→ 正则抽取 → 去重排序 → 输出。

**抽取类别**：

| 类别 | 例 |
|---|---|
| API 路径 | `/api/v1/admin/users`、`fetch("/x")`、`axios.post(` |
| 参数名 | `params:{...}` 键、`?a=` 模式、`searchParams.set(` |
| 路由表 | Vue/React `path:` + `component`、`createRouter` |
| 密钥线索 | `api_key|token|secret|Bearer ` 邻域（只报「有」与位置，不回显长串） |
| 注释/调试 | `TODO|FIXME|debug|internal|admin` |
| sourceMap | `//# sourceMappingURL=`（提示可拉 .map 还原源码） |

**输出**（≤6000）：分类计数 + 高置信条目（含 `admin/api/internal` 优先），并提示「可 traffic 检索」。抓取记录落流量库（`source=jsrecon`）。

**与 `http_map` 分工**：`http_map` = 静态站图（链接/表单/字段）；`jsrecon` = JS 内 API 面。两者不重叠。

**工作量**：≈ 250 行 + smoke 用例（本地假 HTTP 服务器托管假 HTML + 假 bundle）。

### 2.3 接线（两工具相同的四处）

1. `src/ctfy_agent/tools_websec.py`（新，纯 stdlib）：`AUTHDIFF_SPEC`、`JSRECON_SPEC`、`run_authdiff()`、`run_jsrecon()`；
2. `tools_local.py`：import 两个 SPEC、`specs()` 追加、`authdiff()`/`jsrecon()` 适配方法（注入 `_check_target` / 会话 / 记录回调）；
3. `crew.py`：`if "authdiff" in local: ...`、`if "jsrecon" in local: ...`；
4. `config/agent.json`：`web.local += ["authdiff","jsrecon"]` + 提示词补一句（**必须保留 `case_web_playbook` 的 needle、不得出现 `report`/`evidence`/`PLAN`/`[[`/`submit_answer`**）。

---

## 3. Phase 2：`sqlmap` / `sqli` 工具

### 3.1 构建期安装（Dockerfile）

- 版本钉死 + 精确字节数 + `sha256sum -c`（有值时）；构建日志打印 sha256 供事后全量钉死（与 ffuf 同范式）；
- 只装主程序，不装 `--with-deps` 里的多余项；`/opt/sqlmap` root 只读 755；
- **不自带任何第三方 python 包**（sqlmap 纯 Python，避免与 `case_stdlib_only` 的镜像洁净度冲突）。

### 3.2 `sqli` 工具封装

| 参数 | 说明 |
|---|---|
| `url` | 必填，注入点 URL（可含 `*` 标记） |
| `data` | 可选，POST body（`-p` 推断） |
| `param` | 可选，指定参数名 |
| `technique` | 默认 `BEUSTQ`，可收窄（如 `T` 只做时间盲注） |
| `level` / `risk` | 默认 1/1，硬上限 3/2 |
| `timeout_s` | 默认 60，硬上限 120（含 `--time-sec` 自动调低） |

- 命令行**全硬编码**：`--batch --disable-coloring --flush-session --level --risk --technique --timeout=10 --retries=0 --threads=1 --max-redirects=3 --output-dir=<沙盒临时>`（`--data`/`-p` 按需追加）；`--forms`/`--crawl` 不传（sqlmap 默认即关）；不暴露 `--os-shell`/`--file-write`/`--file-read`/`--proxy`/`--tor`；
- 解析输出：只回「是否存在注入 / 注入点参数·类型 / DBMS / 一段可复用 Payload」，绝不回显 dump 全量；
- 命中落流量库（`source=sqli`）；
- 超时即杀进程树，返回部分结论 + `stopped_early`。

**工作量**：≈ 200 行 + smoke 用假 sqlmap 脚本（同 fuzz 的 stub 范式）。

---

## 4. Phase 3（挂起）：浏览器 + 体积探针

### 4.1 体积探针（Phase 0，先做才有决策依据）

在 CI 增加一个**手动触发**的探针 workflow（不动正式构建）：
- 基础镜像上装 Playwright + `chromium-headless-shell`；
- 打印 `docker image inspect --format '{{.Size}}'`、`docker history` 分层、`du -sh /usr/local/lib/python3.12/site-packages/playwright /opt/ms-playwright /usr/lib/x86_64-linux-gnu`；
- 输出结论表 → 决定 L1/L2/L3/放弃。

### 4.2 体积账（预估，置信度已标）

| 组件 | 增量（解压） | 置信 |
|---|---|---|
| `playwright==1.49.1` manylinux wheel（44,163,300 B，sha256 `753ca90ee31b4b03d165cfd36e477309ebf2b4381953f2a982ff612d85b147d2`）+ `greenlet==3.1.1`/`pyee==12.0.0`，解压含自带 node driver | +100~130 MB | 中 |
| `chromium-headless-shell` rev **1148**（Chromium 131.0.6778.33）解压 | +200~260 MB | 中低 |
| chromium apt 依赖（nss/gbm/atk/cups/pango/cairo/alsa 等） | +60~90 MB | 中 |
| **合计** | **+360~480 MB** → 总计 **487~607 MB** | — |

| 阶梯 | 手段 | 预估总量 | 代价 |
|---|---|---|---|
| L1 | 只装 headless-shell + 最小 apt + 删 locales/swiftshader | ~430~470 MB | 仍可能超 |
| L2 | 不装 playwright 包，stdlib 直连 CDP（自写 WebSocket，~350 行） | ~330~380 MB | 无自动等待，自管生命周期 |
| L3 | L2 + 构建期 xz 压缩载荷、运行时解压 | ~230~280 MB | 需验证可执行权限；冷启 +3~5s |
| L4 | 放弃浏览器 | +0 | 无 JS 执行能力 |

### 4.3 若做，硬性要求

- `page.route` 级白名单拦截（与 `http_request` 同一 allowlist），禁非 http(s) scheme，禁第三方跳转；
- `--no-sandbox --disable-dev-shm-usage --disable-gpu`（容器内非 root；渲染器被攻破 = app 用户权限，与 shell 工具同级，不新增边界）；
- cookie 与现有 jar 双向同步；XHR 落流量库（`source=browse`，≤40 条、body ≤64KB）；
- 只输出文本（不截图），避免体积与上下文浪费。

---

## 5. 验收标准

| 阶段 | 验收 |
|---|---|
| Phase 1 | smoke 全绿（新增 2 个 functional 用例 + 自动契约覆盖）；`container_check.py --local` 5/5；提示词护栏 `case_web_playbook` PASS；镜像体积零变化（源码 +`~15KB`） |
| Phase 2 | smoke 全绿（`sqli` 契约 + 假 stub 流程）；CI 构建通过且体积增量 ≤ 18MB（Kali 口径 sqlmap 安装体积 13.00 MB 校准）；`sqlmap --version` 在自检中打印 |
| Phase 3 | 仅当体积探针实测 ≤ 目标值才启动；否则记录结论并关闭 |

---

## 6. 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| `authdiff` 变体请求触发目标风控/封 IP | 实例被封，后续全灭 | 变体数硬上限 13；请求间隔 100~300ms；单次调用只跑一轮 |
| 路径变体被 WAF 视为扫描 | 同上 | 默认只跑 `anon+bypass`，`path` 需显式点名 |
| `jsrecon` 拉到大 bundle 撑爆内存 | OOM | 单文件 ≤512KB、总数 ≤6、流式丢弃 |
| `sqlmap` 被 WAF 拦或跑飞 | 浪费 1 步 | 默认 60s 超时 + `--threads=1` + 只回结论 |
| sqlmap 与 stdlib 自扫冲突 | 容器契约失败 | sqlmap 装 `/opt/sqlmap`（非 src/），src 侧只 subprocess 调用 |
| 浏览器最终放不进 400MB | 白做 3~4 天 | 已挂起，先探针后决策 |
| 无本地 Docker | 构建类改动只能 CI 验证 | Phase 1 纯源码（本地可验）；Phase 2 的 Dockerfile 段在 CI 首建验证 |

---

## 7. 排期

| 阶段 | 内容 | 预估 |
|---|---|---|
| Phase 1 | `authdiff` + `jsrecon` + 接线 + 提示词 + smoke | 1.5 天 |
| Phase 2 | `sqlmap` 构建期安装 + `sqli` 封装 + smoke | 0.5~1 天 |
| Phase 0' | CI 体积探针（与 Phase 1/2 并行） | 0.5 天 |
| Phase 3 | 浏览器（视探针结果） | 3~4 天 |

> 提交节奏：Phase 1/2 完成后打 v3.3 镜像（`workflow_dispatch` 手动、tag=`v3`），**不覆盖已提交的 v2**。

---

## 8. 实施状态（截至本轮）

| 项 | 状态 | 证据 |
|---|---|---|
| Phase 1 `authdiff` | ✅ 已落地 | `src/ctfy_agent/tools_websec.py`（三态/绕过头/路径变形 + 无会话结论打折） |
| Phase 1 `jsrecon` | ✅ 已落地 | 同上（bundle 抽取 API/参数/路由/密钥/sourceMap/注释，输出超限按优先级裁列表） |
| Phase 2 `sqli` | ✅ 已落地 | `src/ctfy_agent/tools_websql.py`（命令行全硬编码、只回结论、超时杀进程） |
| Phase 2 sqlmap 内置 | ✅ Dockerfile 段 | `1.10.10` + sha256 `9dece8eb…e399` 钉死；构建期 `--version` + 真跑一次自检 |
| 接线四处 | ✅ | `tools_local.specs()/方法`、`crew._role_specs` 门控、`config/agent.json` web.local、web 提示词第 5 条与枚举段 |
| 回归 | ✅ | smoke **96 passed / 0 failed**（新增 `websec_contract`/`websec_functional`/`sqli_contract`/`sqli_functional`） |
| 容器契约 | ✅ | `container_check.py --local` 5/5，exit 0 |
| 审查 + 修复轮 | ✅ 已修 A1–A4 + B1–B5 | 见下「P3 审查修复记录」 |
| 镜像构建 | ⏳ 待 CI | 需手动 `workflow_dispatch`（tag=`v3`）；sqlmap 实际增量以构建日志 `du -sh /opt/sqlmap` 为准 |
| Phase 3 浏览器 | ⏸ 挂起 | 按决策先做 CI 体积探针，再定 L1/L2/L3 |

**P3 审查修复记录**（口径：全量读码 + 本地实证 + 外部事实核验）：
- **A1** `_map_fetch` 匿名变体 302 后被 `_decorate_redirect` 重新认证 → opener 的 `capture/decorate` 改按 `use_jar` 传入，匿名请求的 Set-Cookie 也不再回写 jar。
- **A2** jsrecon 相对 `src` 拍平到站点根 → 改 `urljoin(target, src)`。
- **A3** sqli 多参数目标的逐参告警把 `injectable` 重置为 False → 去掉负向重置，`injectable = 命中 or bool(findings)`。
- **A4** sqlmap 唯一不逐跳白名单复核的出网点（`--max-redirects=3`）→ Dockerfile 加**跳转探针**（第二个端口 302，只打印 `decoy_hits` 不 fail），v3 首建观测后再定夺。
- **B1** authdiff/jsrecon 加总墙钟预算（45s/60s）+ `stopped_early`；**B2** 自检残渣清理扩到 `/tmp/sqlmap-out`、`/root/.sqlmap`；**B3** `AUTHDIFF_MAX_ROWS` 12→14；**B4** `_map_fetch` 落库带 `req_body=body`；**B5** sqli SPEC 写明「单次可发数百请求」。
- **E1–E4** 补回归：anon+302 不重新认证、Set-Cookie 不污染 jar、相对 src 正确解析、多参数注入判定不误报、三工具角色门控。

**顺带修复的漂移**：`config/agent.json` 里 6 个角色模型被统一改为 `DeepSeek-V4.1-Flash-1017`（并行会话的未提交改动），但 `roles/ai.json`、`roles/misc.json` 未同步，触发 `case_misc_contract`/`case_ai_contract` 的防漂移断言。只同步了这两个 fragment 的 `model` 字段，**未回退 config**；若并行会话的方向相反，需要反向同步。

