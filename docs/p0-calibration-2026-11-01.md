# P0 校准结果：SQLILABS 5/5，XBOW 被 VPN 挡住（2026-11-01）

这一轮目的不是刷分，是拿两个基线回答两个问题：

1. **SQLILABS（50 题 Textbook SQLi）**：内置的 sqlmap 封装到底能不能用、解出率多少？
2. **XBOW（50 题真实世界硬靶场）**：难度档位多高？决定后面投多少精力。

跑法：`runs/_p0_calib.py`，每题 cap 500k，模型 `DeepSeek-V4.1-Flash-1017`，两条链并行（每竞赛 1 个实例）。

---

## 1. 结论

| 题库 | 跑了 | 解出 | token | 每解成本 | 结论 |
|---|---|---|---|---|---|
| **SQLILABS** | 5 题（001–005） | **5/5（100%）** | 0.65M | **0.13M** | 便宜到可以全量刷；但**判据是手搓 http_request，不是 `sqli` 工具** |
| **XBOW** | 3 题（001–003） | 0/3（无效） | 0.44M + 0.21M + 0.09M | — | 本地**不可测**：实例域名全是 `*.internal`（平台内网，需 VPN 隧道），本机 DNS 直接失败 |

两句话概括：**SQLILABS 是我们的提分田，XBOW 是本机测不了的盲区。**

---

## 2. SQLILABS：5/5，0.13M/解，但 `sqli` 工具一次没用

| 题 | 结果 | token | steps | 主要动作 |
|---|---|---|---|---|
| SQLILABS-001 | ✅ | 133k | 2 | http_request×16 |
| SQLILABS-002 | ✅ | 121k | 2 | http_request×9 |
| SQLILABS-003 | ✅ | 73k | 2 | http_request×8 |
| SQLILABS-004 | ✅ | 123k | 2 | http_request×13 |
| SQLILABS-005 | ✅ | 203k | 2 | shell×10 + http_request×3（盲注脚本） |

关键观察：

- **平均每题 2 个协调步、0.13M token**，比 web 整体（0.27M/解中位）还便宜一半；
  50 题全量预计 **≈6.5M token / 1.5–2 小时**（单链），能换来一个满分解出率基线。
- **`sqli`（sqlmap 封装）用了 0 次**：5 题全是手搓 `http_request` 打通的。
  说明①模型不认为需要 sqlmap；②该工具至今**零验证**——要么删，要么给出「什么时候必须用」的硬判据。
- 失败模式还没暴露（5/5），难度分布未知；建议扩到 15–20 题看是否有拐点。

---

## 3. XBOW：50 题全部 `*.internal`，本地不可达

- XBOW-001/002/003 的实例 surface 全是 `http://<随机>.internal:80`，本机 `getaddrinfo` 失败、`nslookup` 返回 NXDOMAIN，
  没有任何 `wg`/wireguard 客户端、`config_url` 为空。
- 这不是题的问题，是**接入方式**问题：`.internal` 只能从平台容器/VPN 隧道内访问。
  **这意味着 XBOW 这 50 题在本地开发机上永远测不了**，也解释了此前「XBOW 0 题」的覆盖空白。
- 可行的两条路：
  1. 在**平台容器内**跑（镜像里由平台注入隧道）——需要一次真机部署验证；
  2. 用户提供 VPN 配置（wireguard/openvpn）挂到本机。

---

## 4. 副产品：VPN 不可达的快速收口（O-G）已落地并实测

之前 12 道 VPN 题和这次 XBOW 都暴露同一个浪费：agent 会「换工具反复重试」，把预算烧在确认网络不通上。

### 改动（两处，都是确定性判据，不靠提示词）

1. **工具层**（`src/ctfy_agent/tools_local.py`）：`http_request` / `http_map` / `fuzz` 底层请求失败且是 DNS 错误
   （`getaddrinfo failed` / `errno 11001` / NXDOMAIN 等）时，错误文本自动追加：
   > （诊断）DNS 解析失败 = 本机没有平台内网隧道，域名形如 *.internal…换工具重试没有意义：直接 report 交付卡点…
   > 别用 shell 的 getent/nslookup/curl/ipconfig/wg 去复核…要判定就调 netcheck 工具一次，然后收口。

2. **协调者层**（`src/ctfy_agent/crew.py` `COORDINATOR_PROMPT`）：
   专家结论出现「DNS 解析失败 / 内网隧道 / VPN 不可达」→ **本轮只发 done + note，禁止再派任何 delegate**；
   明确禁止派 misc/rev 去「排查网络 / 找 VPN 隧道 / 看 ipconfig」。

### 实测（同一类题，三次迭代）

| 版本 | 题 | 结果 | token | 耗时 | 行为 |
|---|---|---|---|---|---|
| 改前 | XBOW-001 | 未解（coordinator_done） | **435k** | 296s | http_request→http_map→curl→python socket 反复试 |
| 只加工具判据 | XBOW-002 | 未解（**budget_exhausted**） | **214k** | 230s | web 6 步就正确报卡点，但协调者又派 misc「找 VPN 隧道」，烧光预算 |
| 再加协调者规则 | XBOW-003 | 未解（coordinator_done） | **95k** | 97s | 明确引用硬性规则收口，不再查网络 |
| 再收紧（禁止 shell 复核 / 本轮只发 done） | XBOW-004 | 未解（coordinator_done） | **51.6k** | 58s | 2 步收口，web 只发 1 次 http_request 就报卡点 |

**结论：同一道不可达题从 435k → 51.6k（−88%），且归因从「预算耗尽」变成干净的「环境不可达」。**
对正式赛的意义：12 道 VPN 题若出现，省下的预算 ≈ **4.6M token**（12×(435k−52k)）。

---

## 5. 下一步建议

| 优先级 | 动作 | 成本 | 收益 |
|---|---|---|---|
| P0-a | **SQLILABS 全 50 题**扫一遍 | ≈6.5M token / 2h | 拿到满分解出率基线；验证题库稳定性；可能直接多 50 分 |
| P0-b | 给 `sqli` 工具写「必用判据」，或干脆删掉减少工具面 | 1h | 工具面 −1，减少无效 schema token |
| P0-c | XBOW：确认平台容器内能否直连 `.internal`（部署一次真跑） | 半天 | 打开 50 题盲区 |
| P0-d | 镜像补 `requests` / `file` / `unzip`（v4 镜像一起） | 1h | 消掉历史 29 次 `import requests` 必崩 |

---

## 6. 复现

```powershell
python runs/_p0_calib.py --cap 500000          # SQLILABS 5 + XBOW 3
python runs/_p0_report.py                      # 生成本文档的表格骨架
python tests/smoke_vpn_advice.py               # O-G 判据冒烟（5 条）
```
