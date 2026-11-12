# web / AI 安全「剩余短板」补齐记录（2026-10-30 · M6 补丁）

> 依据：会话截图清单（web 与 ai 两张「剩余短板」）。
> 本文件记录：① 已落地的修复与精确 diff（JSON 无法写注释，以本文件为准）；② 本地无法修复、需环境/授权的事项。

## 1. 已落地（12 项，全部可回归）

### 1.1 AI 安全（4 项）

| # | 短板（截图原文） | 修复 | 证据/落点 |
|---|---|---|---|
| A1 | ai 角色工具面没有 verify_exploit（checker 型 AI 题要绕协调者） | platform += `verify_exploit`；提示词新增「校验通道」：checker 型直接 `verify_exploit(instance_id=UUID, question_id=questions[].id)`，flag 型用 verify_answer | `config/agent.json` roles.ai.platform、`roles/ai.json`、`roles/ai.system.txt`；用例 `case_ai_verify_exploit_channel` |
| A2 | 无 shell | `allow_shell: false → true`，local += `shell`、`decode_blob`；提示词新增「载荷构造」：base64/URL/JSON 转义、长注入文本、multipart 用 `python3 -c` 落盘或 `curl -F`，解码用 decode_blob | 同上；用例 `case_ai_shell_and_decode` |
| A3 | 步数还是 12 | `max_steps: 12 → 14`（与 rev/pwn/crypto 对齐）；提示词「上限 12 步：…约 8 轮」→「上限 14 步：…约 10 轮」 | 用例 `case_ai_steps_14` + `smoke_ai_role` 契约 |
| A4 | 038+ 面 37.5% 还有大提升空间 | 根因定位为「能力面缺口」：checker 通道缺失（无法自校验）+ 无载荷构造能力（shell/decode_blob）+ 步数偏少。A1–A3 即针对性补齐；038+ 属 category=web 由协调者按 tags 路由给 ai，方法卡（AI_CARDS 20 张）已覆盖 | A1–A3 落地；剩余提升需真机数据验证 |

### 1.2 Web（3 项）

| # | 短板（截图原文） | 修复 | 证据/落点 |
|---|---|---|---|
| W1 | recon 波动大（同一题 007 两轮 185k vs 801k，差 4 倍） | 提示词【2 枚举】新增「侦察预算（防跑偏）」：同轮 http_map ≤2 次（crawl 1 + probe 1）、同一 URL 不重复抓、侦察投入 ≤ 总步数 1/3、连续两轮无新线索立即转验证/利用 | `config/agent.json` roles.web.system；用例 `case_web_recon_budget` |
| W2 | report 证据校验偏严（历史 report_rejected 13 次） | 提示词【4 收口】evidence 条款细化：从**同一份**输出原样复制 20–200 字符连续正文（可含换行、空白差异容忍），不跨两次输出拼接、不加省略号、不改写/翻译/概括 | 同上；用例 `case_web_evidence_precision`。校验逻辑本体（ledger.verify_evidence：≥4 字符、允许空白折叠、必须连续子串）未改，只把判据前置到提示词 |
| W3 | SQLILABS/UPLOADLABS 专项题库（69+21 题）没有专门打法卡；upload-labs 类文件上传没有专用工具/卡片 | ① `method_cards.py` 新增 2 张专用卡：SQLi 靶场序列（union_based/quote_breakout/double_quote/integer_context/boolean_blind/stacked_queries/multi_query/order_by/post_body/waf_bypass）、文件上传靶场（file_upload/zip_slip/jsp_upload）；② 提示词【3 验证】新增「上传点」：http_request 只支持 body/body_b64、**不生成 multipart**，必须 `curl -F 'f=@x.php;filename=x.php;type=image/png'` | `src/ctfy_agent/method_cards.py`（`[web-ai-gap 2026-10-30]` 注释）；用例 `case_web_sqli_labs_card` / `case_web_upload_via_curl` / `case_cards_no_overmatch` |

### 1.3 精确 diff（JSON 无法注释，以此为准）

```diff
# config/agent.json（roles.ai）
- "max_steps": 12,
- "allow_shell": false,
+ "max_steps": 14,
+ "allow_shell": true,
  platform: [..., "verify_answer"]
+ ，新增 "verify_exploit"
  local: ["http_request", "http_map", "chat"]
+ + "shell", "decode_blob"
  system: 追加【工具面】两行（校验通道 / 载荷构造）；「上限 12 步」→「上限 14 步」

# config/agent.json（roles.web.system，仅提示词，工具面不动）
【2 枚举】+ 「- 侦察预算（防跑偏）：…http_map 最多 2 次…≤ 总步数 1/3…」
【3 验证】+ 「- 上传点：http_request 只支持 body/body_b64、不生成 multipart——用 shell 的 curl -F …」
【4 收口】evidence 条款扩写为「同一份输出 + 20–200 字符连续正文 + 不拼接/不省略/不改写」
硬约束 7 「只有 10 步：2+3+3+1+1」→「只有 14 步：2 指纹/4 枚举/5 验证/1 收口/2 缓冲」（§5 审计修复 ①）

# src/ctfy_agent/crew.py（DEFAULT_ROLES.ai，§5 审计修复 ②，并行会话已同意）
- "ai": {"max_steps": 12, "allow_shell": False, "local": ["http_request","http_map","chat"]}
+ "ai": {"max_steps": 14, "allow_shell": True, "local": […, "shell", "decode_blob"]}
  platform 组名不变：oracle 组 = verify_answer + verify_exploit，校验通道本就覆盖

# roles/ai.json + roles/ai.system.txt（与 config 零漂移，test 自动对账）
同上四项；system 与 config 的 roles.ai.system 逐字一致（strip 后比较）
```

**未动**：web 平台工具白名单（仍 10 个，裁工具护栏不破）、web 本地工具面（仍 10 个）、校验逻辑源码、协调者提示词。

## 2. 本地无法修复、需环境/授权（3 项，登记在案）

| # | 短板（截图原文） | 原因 | 需要什么 |
|---|---|---|---|
| X1 | 003/004/012（平台占位/VPN-only，本地无隧道） | 平台侧占位题/需 VPN 才能触达实例 | 平台侧修复或 VPN 隧道；本地无可绕过路径，仅能等环境 |
| X2 | 011（需 Linux 容器复测） | 本机无 Docker，无法起 Linux 容器复跑 | 有 Docker 的机器/CI runner |
| X3 | M6 镜像重建被「本机无 Docker」卡着（和 v4 重建同一件事） | 与 X2 同源：无 Docker 无法 build/push 镜像 | 有 Docker 的机器；届时 v4 一并带 capstone（pwn/rev 反汇编引擎） |

## 3. 验收

| 套件 | 结果 |
|---|---|
| `tests/smoke.py`（全量） | **144 passed / 0 failed** |
| `tests/smoke_ai_role.py` | OK（10 工具白名单含 verify_exploit / chat+shell+decode_blob / 14 步 / 零漂移） |
| `tests/smoke_webai_gap.py`（新） | **12/12** |
| `tests/smoke_pwn.py` / `smoke_crypto.py` / `smoke_rev.py` | 24/24 · 27/27 · 15/15（回归未受影响） |
| `tests/smoke_ai_chat / ai_agent / ai_mcp / ai_infra / webmap` | 全部 OK |

## 4. 风险与后续

1. **零漂移契约**：config 的 roles.ai 与 `roles/ai.json`+`roles/ai.system.txt` 必须同步改，`tests/smoke_ai_role.py` 会硬断言；后续改 AI 角色记得三处一起。
2. **shell 进 AI 角色**是能力面扩大：沙盒仍由 `shell` 工具自身的路径/命令护栏约束（与 web/rev 同款），未放宽全局策略。
3. **真机效果待验证**：W1/W2 是提示词层修补，实际降载/降 rejected 率需跑批数据证明；A1–A3 同理（建议 next：stalled 题复跑，需模型预算授权）。

## 5. 代码审计（2026-10-30 第二轮 · 九项独立核查）

| # | 检查 | 结果 |
|---|---|---|
| 1 | 端到端功能：ai 角色 `LocalTools(allow_shell=True)` | ✅ decode_blob 解码、shell `python -c` 执行、`allow_shell=False` 时 ToolError 拦截（"shell 未开启"）——crew L600 `allow_shell=bool(spec.get(...))` → solver L906 → LocalTools 全链通 |
| 2 | 上下文成本（schema 增量） | ✅ 实测 +1071 字符 ≈ **+267 token/步**（shell+decode_blob+verify_exploit 占位；真实 verify_exploit 平台 spec 未离线可得，估 +150~250 tok/步）。按 100 calls/题上限 ≈ ≤5 万 token/题 ≈ 4% 预算，可接受 |
| 3 | 真实题库全量匹配 | ✅ catalog 793 题过 `method_cards`：**SQLi 靶场 67 道、上传靶场 23 道全部命中专项卡**；全库平均 0.77 张、最大 5 张（上限 8，无截断风险） |
| 4 | 注入面 | ✅ 2 张新卡无 `{`/`FLAG{`，纯固定文案 |
| 5 | 禁字面量 | ✅ ai/web 新提示词均不含 `report`/`evidence`/`PLAN`/`[[`——框架自动补规则（EVIDENCE_RULE 等）不被吞 |
| 6 | 步数一致性 | ✅ ai：config=14 == 提示词「上限 14 步」；web：修复后 config=14 == 提示词「只有 14 步」 |
| 7 | DEFAULT_ROLES.ai 兜底 | ✅ **已修**：兜底同步为 14 步/开 shell/local+shell+decode_blob（platform 组名 oracle 本就含 verify_exploit）；新增 `case_default_ai_fallback_sync` 钉死 |
| 8 | multipart 说法 | ✅ HTTP_SPEC 实测无 files/multipart 字段，「不生成 multipart」准确（body_b64 手拼 boundary 理论可行，curl -F 是更稳指路） |
| 9 | verify_exploit 修复层 | ✅ ∈ `_INSTANCE_TOOLS` 且 ∈ `_QUESTION_TOOLS`（instance_id/question_id 自动补齐与 instance_not_found 重试都覆盖） |

**发现处置（2 项 LOW，2026-10-30 已修复，并行会话已同意）**：
1. **web 步数预存漂移**：提示词硬约束 7 改「只有 14 步：2 指纹/4 枚举/5 验证/1 收口/2 缓冲」（与 config max_steps=14 对齐；misc 核查为 10==10 一致，未动）；`tests/smoke.py` web_playbook needle 同步「只有 14 步」。
2. **DEFAULT_ROLES.ai 兜底陈旧**：12→14、allow_shell False→True、local += shell/decode_blob（带 `[web-ai-gap 2026-10-30]` 标识）。**更正**：审计原文说「兜底缺 verify_exploit」不准确——platform 用组名，oracle 组 = verify_answer + verify_exploit 本就覆盖；实际只缺步数/shell/local 三项。新增 `case_default_ai_fallback_sync` 钉死兜底关键字段，防再漂移。

**提示词黑话清除（用户复查发现）**：发给模型的提示词里不应有内部里程碑编号——「【工具面（M6 补齐）】」→「【工具面】」（config + roles/ai.system.txt 两处同步，零漂移复验通过）。9 份提示词全量扫描：无 toolbox/日期/编号类黑话残留（rev/pwn 的「标识串」是领域用语，保留）。代码/测试里的 `[web-ai-gap 2026-10-30]` 等标识按约定保留（区分并行会话的未提交改动）。

**临时脚本与沙盒目录已清理**；修复后全套件回归绿（§3，smoke_webai_gap 12/12）。

**收尾预防性修复**：仓库 `core.autocrlf=true` 且原无 `.gitattributes`——提示词文件一旦被 checkout 成 CRLF，零漂移契约（按 `\n` 逐字比对）会在 Windows 上假红。新增 `.gitattributes` 钉 `roles/*.system.txt text eol=lf`（`git check-attr` 验证：text=set / eol=lf），契约用例复跑绿。
