# 断网（零公网）姿势自查 — 2026-11-19

> 下一阶段比赛要求**全程断网**。本文给出：我们的 agent 在零公网下能不能跑、依赖什么、
> 已做的验证与还需要主办方/我们确认的点。

## 1. 规则口径（仓库内审计文档 v2.5 §二）

> 解题 Agent：**零公网**：仅授权平台接口、**大模型网关**、题目入口；工具依赖**预装**；禁代理/重定向/子进程绕过。

我们完全按这个口径设计的：运行期只碰 **平台 MCP / 平台 REST（实例 VPN 配置）/ 大模型网关 / 题目实例**，
其余工具全部预装。

## 2. 结论：**能行**，但前提是「大模型网关由平台注入」

| 依赖 | 断网下是否可用 | 依据 |
|---|---|---|
| 大模型调用 | ✅ 可用（平台注入 `LLM_API_KEY/LLM_BASE_URL/LLM_MODEL`）<br>⚠️ 若没注入 → 回落到本地 `api.deepseek.com`，断网必挂 | `agent_main.py` 优先读 `LLM_*`/`OPENAI_*`；`config.py` 也支持网关回退；端点不在规则白名单会写 `llm_endpoint_alert` 告警 |
| 平台 MCP / REST | ✅ 授权入口（比赛内网） | `platform.py` / `netbridge.fetch_vpn_config` 只访问平台域名 |
| 题目实例 | ✅ per-instance WireGuard，`netbridge` 用平台 REST 拉配置 + 本地 wireproxy 桥 | SAMPLE-023 实测 2.8 分钟解出 |
| 工具链 | ✅ 全预装（pwntools/gdb/z3/capstone/unicorn/ROPgadget/ffuf/sqlmap/binwalk/foremost/steghide/exiftool/7z/john…），SecLists/sqlmap 在构建期打进镜像 | Dockerfile；CI 已跑「断网 + 只读根 + 非 root → dry_run exit 0」 |
| 运行时安装 | ✅ 不需要 pip/apt | `tools/offline_audit.py` 扫描：运行期无 pip/apt/curl/wget 外网调用 |
| DNS | ⚠️ 需内网 DNS 或 hosts 兜底 | 比赛机 `/etc/hosts` 已有 `ctfy.tsinghua.edu.cn` 等映射；容器 `--network host` 继承宿主 |
| 代理 | ⚠️ 不要设置指向公网的 `HTTP(S)_PROXY` | 若环境有代理变量，需指向平台内网网关 |

## 3. 已做的验证

- `python tools/offline_audit.py` → **✅ 运行期无公网依赖（白名单内）**：
  - 白名单域名：`ctfy.tsinghua.edu.cn` / `status.ctfy.io` / 各 LLM 官方端点 / `ctf2.dasctf.com`（练习平台）；
  - 运行期 HTTP 客户端只有 3 处，全是平台/题目授权入口（平台 REST、实例探测、题目 HTTP 工具）；
  - Dockerfile 里的 pip/apt/GitHub 下载都是**构建期**，不影响运行期断网。
- v8 镜像 CI 已包含「断网 + 只读根 + 非 root 下 dry_run exit 0」校验。
- 比赛机实测：镜像 + wireproxy + per-instance 隧道 + 平台提交全链路通过（SAMPLE-023）。

## 4. 两种比赛形态分别怎么跑

### A. 平台托管形态（正式赛主流，推荐）
平台跑我们的镜像：`ENTRYPOINT python -m ctfy_agent agent` + `/ctfy/work/task.json` + 注入 `LLM_*`。
**我们什么都不用做**；只需保证：镜像 tag/digest 正确、平台注入 env 生效。
- 若平台注入缺失：`result.json.llm_endpoint_alert` 会告警，agent 会在断网下所有 LLM 调用失败 →
  这是赛后第一时间可查的证据（我们已把告警写进结果）。

### B. 自己 VPS 驱动形态（本场训练/测试用）
平台不注入 env，则必须由我们把网关配置喂给容器：
```bash
# /root/comp/llm.env（600）
LLM_BASE_URL=https://<网关>/v1
LLM_API_KEY=<平台发的 key>
LLM_MODEL=<平台指定模型名>
```
启动脚本 `runs/_ssh/start_offline.sh`：先验证网关可达（容器内 curl `/models`），再
`docker run --network host --env-file /root/comp/llm.env ... tools/comp_auto.py run ...`。
**断网前必须确认**：`LLM_BASE_URL` 指向内网网关、key 有效、模型名正确。

## 5. 断网前的检查清单（赛前 5 分钟）

1. `python tools/offline_audit.py` 输出 ✅；
2. 容器内验证网关：`curl -sS -m 8 $LLM_BASE_URL/models -H "Authorization: Bearer $LLM_API_KEY"`；
3. 平台 MCP 可达：`python tools/comp_auto.py plan --competition-id <id> --profile CTFY`；
4. DNS：`getent hosts ctfy.tsinghua.edu.cn`（或 hosts 兜底）；
5. 无公网代理变量污染：`env | grep -i proxy`；
6. 镜像内工具：`docker run --rm --entrypoint python <img> -c "import pwn,z3,capstone"`。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 平台未注入 `LLM_*`，断网全挂 | 结果里 `llm_endpoint_alert` 会标红；赛前用 B 方案 env-file 兜底；赛后写进复盘 |
| 网关域名不在规则白名单（正式赛只允许智谱） | 我们的白名单告警会提示；正式赛应使用平台给的智谱端点 |
| 内网 DNS 不可用 | 容器 `--network host` + 宿主 `/etc/hosts` 兜底；镜像内不依赖公网 DNS |
| 工具依赖联网（个别工具想更新字典） | ffuf SecLists/sqlmap 已预装；工具调用不触发更新 |

## 7. 现有实现位置

- `src/ctfy_agent/agent_main.py`：`LLM_*` 注入优先 + 端点白名单告警（写 `result.json.llm_endpoint_alert`）
- `src/ctfy_agent/config.py`：`DEEPSEEK_*`/`OPENAI_BASE_URL`/`OPENAI_API_KEY` 网关回退
- `tools/offline_audit.py`：静态审计运行期公网依赖
- `runs/_ssh/start_offline.sh`：自驱动形态的断网启动脚本（env-file + 网关预检）