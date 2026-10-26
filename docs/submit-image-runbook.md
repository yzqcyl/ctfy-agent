# 提交 agent 镜像到平台 — 最终状态

## ✅ 镜像已就绪

```
ghcr.io/yzqcyl/ctfy-agent-pub:v1
```

- **完全匿名可拉取**（无需任何凭据，平台可直接 pull）
- 9 层 / 47.2 MB 传输体积（解压 126.5 MB）
- Entrypoint `["python", "-m", "ctfy_agent", "agent"]`，不依赖 WORKDIR
- 构建 + 契约校验在 GitHub Actions 的真 Docker 里跑过，8 个步骤全绿

## 平台页面怎么填

1. 「我的 Agent」→ **指定一个镜像** → 选「你已经发布的镜像」→ 填
   `ghcr.io/yzqcyl/ctfy-agent-pub:v1`
2. **取名字**：随便（例如 `ctfy-v1`），版本号 `v1`
3. **指明谁来付费** ⚠️ 必须选 **OpenAI 兼容** 的 provider（DeepSeek 的 key 就是）。
   **不要选 Anthropic** —— 我们的适配层只认 `LLM_FLAVOR=openai`，否则直接 `refused` 0 分。
4. 提交审核，等运营方批准。批准后跑的就是这份字节。
5. 审核通过后用 **「测评我的 Agent」** 跑一遍测评集，确认全链路。

## 为什么包名带 `-pub`

GitHub 的容器包可见性**只在创建时继承仓库可见性，之后不会同步**。
`ctfy-agent` 这个包是在仓库还是私有时创建的，所以永远是 private，而且 API 改可见性需要
`delete:packages` 权限（当前 token 没有）。

解决办法：仓库转公开后，用**新包名**重新推送 → 新包在公开仓库下创建 → 默认 public。
workflow 里两个包名都会推，正式用的是 `-pub` 那个。

## 怎么重新构建

改动代码后直接 `git push`（workflow 已配 `push: branches: [master]` 自动触发），
或到仓库 Actions → `build-agent-image` → Run workflow 手动触发。

产物还有一份 `ctfy-agent.tar.gz` 存在 Actions 运行页的 Artifacts 里，可下载留档。

## 相关文件

- `.github/workflows/build-agent-image.yml` — 云端构建流水线
- `tools/container_check.py` — B1 契约验收（体积/目录/cwd/五条失败路径）
- `tools/publish_image.py` — 建仓/推送/触发/轮询/可见性一条命令
- `tools/verify_image.py` — 匿名拉取 + 运行契约核验
