# 提交 agent 镜像 — 最终状态与操作单

## ✅ v3 已构建、匿名可拉取（2026-10-29）——**提交用这个**

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v3` |
| digest | `sha256:e658b9f6a466209813d1278bfa379b38e0275cf5fe0f0d8f41459f423bbf8a56` |
| 构建 | GitHub Actions run `40837543846`（branch `v3`、sha `5a7f8c5`）→ 全绿 |
| 代码基线 | commit `5a7f8c5`：集群-B 接线（`clusterb_k` 默认不启用）+ LLM 读超时修复 + P2 web 工具链（ffuf / SecLists / sqlmap） |
| 非 root | ✅ `Config.User="app"`（uid 10001、`HOME=/home/app`、`PYTHONPATH=/ctfy/src`） |
| 入口 | `Entrypoint=["python","-m","ctfy_agent","agent"]`、`WorkingDir=/ctfy`、`Cmd=None` |
| 体积 | 16 层，匿名传输 59.1 MB（解压 149.4 MB，平台上限 400 MB） |
| 匿名拉取 | ✅ token / manifest / config 三层全 200（`python tools/verify_image.py ghcr.io/yzqcyl/ctfy-agent-pub:v3`） |
| CI 契约 | ✅ 五条路径全 `exit 0`（dry_run / no_task / bad_run_mode / no_creds / no_mcp） |
| 构建内自检 | ✅ ffuf（`ffuf selftest ok`）、sqlmap（产出 injectable 判定）、302 跳转探针（`decoy_hits=0`） |
| `latest` | 已随本次构建指向 v3（`ctfy-agent:latest` / `ctfy-agent-pub:latest`）——提交仍请**显式填 `:v3`** |

### 与 v2.1 的差异（净改进，无行为回归）

- `config/agent.json` 不加 `clusterb_k` → k=1 路径与 v2.1 逐字等价（10-29 实测：k=3@511k 花 493,615 tok 未解出，k=1@511k 用 313,076 tok 达成，见 `docs/clusterb-p1-result.md`）。
- 新增 LLM 读超时兜底：单次模型挂起不再让整题报废（`llm.py` 把裸 `TimeoutError` 归一为可重试 `LLMError` + 1 次重试；容器默认读超时 120→240s）。
- 新增 web 工具链：ffuf 2.1.0 + SecLists 三张表 + sqlmap 1.10.10（版本/字节数/sha256 全钉死，构建期实测跑通）。

### 下一步（浏览器，人工）

1. 平台「配置本次测评」→ 镜像地址填 `ghcr.io/yzqcyl/ctfy-agent-pub:v3`
2. 点「重新检查」→ 注册 / 协议检查 / 试运行 / 审核 全绿
3. 提交审核 → 通过后用「测评我的 Agent」跑一遍

### ⚠️ v3 已知风险：思考模式未显式关闭

v3 镜像里**没有** `LLM_ENABLE_THINKING=0`（`.env` 容器读不到，平台也不注入）。本地所有实测都在 `LLM_ENABLE_THINKING=0` 下完成；v3 实跑是端点默认（百炼默认开思考），可能把 `max_tokens=8192` 烧在 `reasoning_content` 上 → `content` 空、`finish_reason=length`、白丢一步。
- 观察点：评估结果里 `stop_reason` 是否 `max_turns`/`halted`、`tool_calls_made` 是否异常少、有没有空回答。
- 处理：Dockerfile 加 `ENV LLM_ENABLE_THINKING=0` 重建 v4（初赛百炼 OK；11-21 正式赛换智谱后需确认该字段兼容性）。
- 决策记录：2026-10-29 先用 v3 跑免费样例验证端到端，再决定是否重建 v4。

---

## 历史：`v2.1`（非 root 修复版，已被 v3 取代）

## ⚠️ 2026-10-27 平台实测：`:v2` 被「协议检查」拒绝（以 root 运行）

平台「注册并检查」对 `ghcr.io/yzqcyl/ctfy-agent-pub:v2` 的判定：

| 检查项 | 结果 |
|---|---|
| 已注册为 `ctfy-agent-pub` | ✅ |
| 协议检查 | ❌ 以 root 运行——用户镜像必须以非 root 用户运行 |
| 试运行 | ❌ 同上 |
| 审核 | ⚠️ 等待人工审核（修复前不可运行） |

**根因**：`Dockerfile` 没有 `USER` 指令（`Config.User=""` → docker 判 root）。
**修复**（已入库）：Dockerfile 末尾
```dockerfile
RUN useradd --create-home --uid 10001 --shell /bin/bash app
ENV HOME=/home/app
USER app
```
运行期不需要 root：平台把 `/ctfy/work` 以 0o777 的 staging 目录 `docker cp` 进容器
（`providers/docker_provider/harness.py`），运行期状态全部写在 `/ctfy/work/runs`，`/ctfy` 只读即可。

**重建 + 重新检查**（不要覆盖 `:v2`）：
1. 构建新 tag：`python tools/publish_image.py --tag v2.1`（或本地 `docker build` 后 push）
2. 页面「配置本次测评」→ 镜像地址改成 `ghcr.io/yzqcyl/ctfy-agent-pub:v2.1` → 点「重新检查」
3. 五项全绿（注册 / 协议检查 / 试运行 / 审核）后再提交审核

## 历史：`v2.1` 已构建并校验（2026-10-27 23:37 +08:00）——已被 v3 取代

| 项 | 值 |
|---|---|
| 镜像 | `ghcr.io/yzqcyl/ctfy-agent-pub:v2.1` |
| digest | `sha256:6406286f857e5cbe6efd36645e1347a3f8c8f9b94efda179c03904cf9fe151bb` |
| 构建 | GitHub Actions run `40532627504`（branch `master`、sha `429d280`）→ 全绿 |
| 非 root | ✅ `Config.User="app"`（uid 10001，`HOME=/home/app`、`PYTHONPATH=/ctfy/src`） |
| 入口 | `Entrypoint=["python","-m","ctfy_agent","agent"]`、`WorkingDir=/ctfy` |
| 体积 | 10 层，压缩后 47.4 MB |
| 契约 | CI 内五条路径全部 `exit 0`（dry_run / no_task / bad_run_mode / no_creds / no_mcp） |
| 代码基线 | **与已实测的 v2 完全一致**，仅增加非 root 运行（master 线最小改动） |

校验方式（不启 docker daemon，直接查 registry API）：
`python runs/_ghcr_check.py yzqcyl/ctfy-agent-pub v2.1`

**下一步（人工）**：平台「配置本次测评」→ 镜像地址改为 `ghcr.io/yzqcyl/ctfy-agent-pub:v2.1` → 点「重新检查」→ 五项全绿后提交审核。

## 历史：`v2` 已冻结（已被 v3 取代，勿再提交）

```
ghcr.io/yzqcyl/ctfy-agent-pub:v2
```

| 项 | 值 |
|---|---|
| **digest** | `sha256:73b39430873a1bf3d213e2881722a7aa69d78d1a24932790e932043306cf09e5` |
| 构建源码 | commit `21488518f1770cd77b7a79916245a91a3b859e90`（Actions run #6，`head_sha` 已核对一致） |
| 平台视角拉取 | 完全匿名可拉取（token/manifest/config 三层全 200，`tools/verify_image.py` 实测） |
| 体积 | 9 层，传输 47.2 MB（解压 126.5 MB；平台 guideline 8 GiB） |
| 架构 | amd64 / linux（与 worker 一致） |
| Entrypoint | `["python", "-m", "ctfy_agent", "agent"]`，Cmd 为空，不依赖 WORKDIR |
| 平台合规判定 | **passed**（平台自己的 `harness_conformance`：0 finding） |
| 镜像内容核验 | ✅ 层内确认含 `http_burst` / `jwt_forge` / `ctxcompress` / `follow_redirect` / `config/agent.json` |

### v2 相比 v1 新增能力

- `jwt_forge`：none / HS256-512 / RS256（内置测试密钥 + jwk 头注入），secret 永不回显、只进账本指纹
- `http_request` P0：`elapsed_ms`、`OPTIONS`、`follow_redirect=false`（看原始 302+Location）、`body_b64`
- `http_burst`：并发竞态（Barrier 同时打）+ `{v}` 批量枚举，≤24 请求
- 上下文压缩 `ctxcompress`：工具结果瘦身 + 事实常驻 + 两级滑窗
- 审计修复：burst 缓存失效、follow_redirect 绕过缓存、参数类型护栏、跨 host cookie 防泄漏

## 📦 v1 归档（旧代码，不要提交）

```
ghcr.io/yzqcyl/ctfy-agent-pub:v1
digest sha256:a68ebe6e619b95191cc5168c9e35f2e92788970cfca823093ba8d1100d66ec4b
```

v1 未被移动（已核验 digest 不变），但它**不含**上面的新工具。保留仅为留档。

> ⚠️ 历史段落，已失效：`:latest` 现在指向 **v3**（两个包都是）。提交时请显式填 `:v3`，别用 `latest`。

## 平台页面操作单（4 步，必须浏览器）

> 为什么不能我代做：`/eval/*` 这组路由要求 `user` 类型 token，而 API tokens 页只发
> `fine_grained`；铸造 `user`/`agent` token 本身又要 `user` 会话。平台刻意堵死了
> 细粒度 token 注册镜像这条路（避免泄露的 play token 提权），没有 API 绕法。

### 1. 指定镜像

选「你已经发布的镜像」，填：

```
ghcr.io/yzqcyl/ctfy-agent-pub:v2
```

### 2. 取名字

- **名字（id）**：`ctfy-v2` —— 只能是小写字母数字，可带 `.` `_` `-`；你的命名空间会自动加
- **显示名**：`ctfy-v2`（随意）
- **版本**：`v2`（随意，自由文本）
- 不要填 `source_url`（除非你想公开源码仓库地址）

### 3. ⚠️ 指明谁来付费

必须选 **OpenAI 兼容** 的 provider。**DeepSeek 的 key 是 OpenAI 兼容的，直接用**。

**绝对不要选 Anthropic** —— 我们的适配层只认 `LLM_FLAVOR=openai`，收到 anthropic
会立刻写 `stop_reason=refused` 并退出，等于 0 分。

### 4. 提交审核

等运营方批准。批准后跑的就是 `sha256:73b39430…` 的字节。

## 审核通过之后

用页面上的 **「测评我的 Agent」** 选一个测评集跑一遍。这一跑等价于正式评分路径，
跑完检查：

- 结果文件写了没有（没写 = `no_result_file`，直接失败）
- `stop_reason` 是不是 `done`
- 有没有 `answers`

平台侧的运行判定规则（`inspect_probe`）：缺 `result.json` 是 **blocking**，
非零退出只是 advisory，没有 `schema_version` 也是 advisory。

## 重新构建（发新版本时）

```powershell
$env:GH_TOKEN = "ghp_xxx"     # 需要 repo + workflow 权限
python tools/publish_image.py --tag v3
```

**必须显式给 `--tag`**：workflow 的默认 tag 是 `v1`，不带 `inputs.tag` 触发会把已提交的
v1 移走（平台判 `tag_moved`）。`publish_image.py` 现在会显式传 `inputs.tag`，并在
`--tag v1` 时打印警告。

推 master 本身是安全的（自动触发已移除，推了什么都不会重建）；危险只在手动 dispatch
时把 tag 填成已提交的那个。

产物还有一份 `ctfy-agent.tar.gz` 在运行页的 Artifacts 里。

## 相关工具

| 文件 | 用途 |
|---|---|
| `.github/workflows/build-agent-image.yml` | 云端构建流水线（仅手动触发） |
| `tools/container_check.py` | B1 契约验收（体积/目录/cwd/五条失败路径） |
| `tools/publish_image.py` | 建仓/推送/触发/轮询/可见性；**用 `--tag` 指定版本** |
| `tools/verify_image.py` | 匿名拉取 + 运行契约核验（随时可自检） |
