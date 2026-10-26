# 提交 agent 镜像 — 最终状态与操作单

## ✅ v2 已冻结，提交这个

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

> ⚠️ `:latest` 现在指向 v2 的 digest（workflow 同时推 `v2` 和 `latest`）。提交时请**显式填 `:v2`**，别用 `latest`。

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
