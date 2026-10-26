# 提交 agent 镜像 — 最终状态与操作单

## ✅ 镜像已冻结，可以提交

```
ghcr.io/yzqcyl/ctfy-agent-pub:v1
```

| 项 | 值 |
|---|---|
| **digest** | `sha256:a68ebe6e619b95191cc5168c9e35f2e92788970cfca823093ba8d1100d66ec4b` |
| 平台视角拉取 | 完全匿名可拉取（实测 token/manifest/config 三层全 200） |
| 体积 | 9 层，传输 47.2 MB（解压 126.5 MB；平台 guideline 8 GiB） |
| 架构 | amd64 / linux（与 worker 一致） |
| Entrypoint | `["python", "-m", "ctfy_agent", "agent"]`，Cmd 为空，不依赖 WORKDIR |
| 平台合规判定 | **passed**（用平台自己的 `harness_conformance` 规则模块跑，manifest + dry-run 两半场均零 finding） |

自动构建已关闭，`:v1` 与 `:latest` 都钉在上面这个 digest。**不要再往 master 推代码**——
推了也不会重建（触发已移除），但如果你手动跑了 workflow 且 tag 仍填 `v1`，tag 就会移动，
审核就会被 `tag_moved` 标记。发新版本请用新 tag（如 `v2`）。

## 平台页面操作单（4 步，必须浏览器）

> 为什么不能我代做：`/eval/*` 这组路由要求 `user` 类型 token，而 API tokens 页只发
> `fine_grained`；铸造 `user`/`agent` token 本身又要 `user` 会话。平台刻意堵死了
> 细粒度 token 注册镜像这条路（避免泄露的 play token 提权），没有 API 绕法。

### 1. 指定镜像

选「你已经发布的镜像」，填：

```
ghcr.io/yzqcyl/ctfy-agent-pub:v1
```

### 2. 取名字

- **名字（id）**：`ctfy-v1` —— 只能是小写字母数字，可带 `.` `_` `-`；你的命名空间会自动加
- **显示名**：`ctfy-v1`（随意）
- **版本**：`v1`（随意，自由文本）
- 不要填 `source_url`（除非你想公开源码仓库地址）

### 3. ⚠️ 指明谁来付费

必须选 **OpenAI 兼容** 的 provider。**DeepSeek 的 key 是 OpenAI 兼容的，直接用**。

**绝对不要选 Anthropic** —— 我们的适配层只认 `LLM_FLAVOR=openai`，收到 anthropic
会立刻写 `stop_reason=refused` 并退出，等于 0 分。

### 4. 提交审核

等运营方批准。批准后跑的就是上面那个 digest 的字节。

## 审核通过之后

用页面上的 **「测评我的 Agent」** 选一个测评集跑一遍。这一跑等价于正式评分路径，
跑完检查：

- 结果文件写了没有（没写 = `no_result_file`，直接失败）
- `stop_reason` 是不是 `done`
- 有没有 `answers`

平台侧的运行判定规则（`inspect_probe`）：缺 `result.json` 是 **blocking**，
非零退出只是 advisory，没有 `schema_version` 也是 advisory。

## 重新构建（发新版本时）

Actions → `build-agent-image` → Run workflow → tag 填 `v2`（不要覆盖 v1）。

产物还有一份 `ctfy-agent.tar.gz` 在运行页的 Artifacts 里。

## 相关工具

| 文件 | 用途 |
|---|---|
| `.github/workflows/build-agent-image.yml` | 云端构建流水线（手动触发） |
| `tools/container_check.py` | B1 契约验收（体积/目录/cwd/五条失败路径） |
| `tools/publish_image.py` | 建仓/推送/触发/轮询/可见性一条命令 |
| `tools/verify_image.py` | 匿名拉取 + 运行契约核验（随时可自检） |
