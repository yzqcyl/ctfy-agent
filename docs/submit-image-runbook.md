# 提交 agent 镜像到平台（全程不碰校园网虚拟网卡）

## 背景

- 平台要求：一个能拉到 `task.json`、写出 `result.json`、退出码 0 的容器镜像（CTFY-0001 v2）。
- 本机没有 Docker（校园网禁虚拟网卡），且 `ghcr.io` 在本机 TLS 被中断、Docker Hub 被 DNS 污染。
- 方案：**GitHub Actions 云端构建 → 推 ghcr.io → 平台页面填引用**。构建/校验/推送都在 GitHub 的机器上，本地只需浏览器。

## 一次性的准备工作（约 5 分钟）

1. 登录 GitHub，新建一个**私有**仓库，例如 `ctfy-agent`（不要勾 README，空仓即可）。
2. 在仓库页面点 **Settings → Actions → General → Workflow permissions**：
   把 **Read and write permissions** 勾上（推 GHCR 需要 `packages: write`）。
3. 把本目录源码推上去（`.gitignore` 已确保 `.env`、`runs/`、`.tools/`、`misc-bank/` 等**不会被上传**，密钥不外泄）：

```powershell
cd E:\ctfy_project
git init
git add .
git commit -m "TPCTF agent image + build workflow"
git branch -M main
git remote add origin https://github.com/yzqcyl/ctfy-agent.git
git push -u origin main
```

> 若推送时要登录：用 GitHub 的 Personal Access Token（Settings → Developer settings → PAT，勾 `repo` + `workflow`）当密码，别用账号密码。

## 每次发版（构建 → 校验 → 推送）

1. 打开仓库 **Actions** 标签 → 左侧选 `build-agent-image` → **Run workflow**（tag 保持 `v1` 或改成新版本号）→ 绿色 Run 按钮。
2. 等它跑完（约 3–6 分钟）。四个步骤全绿即成功：
   - `Build image`
   - `校验：B1 契约` ← 这一步在**真 Docker** 里跑：体积、目录洁净度、cwd 无关、五条失败路径全部 exit 0 且写出合法 result.json
   - `上传制品`（可下载 `ctfy-agent.tar.gz` 留档）
   - `推送到 GHCR`
3. 完成后镜像引用就是：`ghcr.io/yzqcyl/ctfy-agent:v1`。

## 让平台能拉到镜像（关键）

私有仓库推出来的 ghcr 包**默认也是私有**，平台拉不动。二选一：

- **A（推荐）**：GitHub 头像 → **Packages** → `ctfy-agent` → 右侧 **Package settings** → 底部 **Danger Zone → Change visibility → Public**。
  若该按钮不可用，说明 ghcr 不允许私有仓库的包公开，转 B。
- **B**：把仓库设为 Public（源码里没有密钥，安全；代价是别人能看到实现）。

## 平台页面填报

1. 「我的 Agent」→ 指定镜像 → 填 `ghcr.io/yzqcyl/ctfy-agent:v1`（选「你已经发布的镜像」）。
2. 取名字 + 版本号随意。
3. 提交审核，等运营方批准（批准后跑的就是这份字节）。

## ⚠️ 付费那步必须选 OpenAI 兼容 provider

我们的适配层**只认 `LLM_FLAVOR=openai`**。所以「指明谁来付费」别选 Anthropic 的 key，
要选 **OpenAI 兼容** 的（**DeepSeek 的 key 就是**，直接可用）；否则镜像会写 `stop_reason=refused` 直接 0 分。

## 审核通过后

用页面上的 **「测评我的 Agent」** 选一个测评集跑一遍，确认结果文件/退出码/答案链路都正常。
这一跑等价于正式评分路径。
