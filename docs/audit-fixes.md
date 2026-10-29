# TPCTF 2026 Agent 全局审计 — 修复摘要

> 本文档是「全局代码审计」后、用户授权「修复」与「推进」两轮改动的事实记录。
> 仅覆盖**本会话修复**；并行会话改动（tools_domain_ext / artifacts / traffic 落库等）不在此列。
> 验证基线：`tests/smoke.py` 三轮 **97/97 passed, 0 failed**（零回归）。

## 0. 总览

| 编号 | 严重度 | 主题 | 文件 | 状态 |
|---|---|---|---|---|
| P0-致命 | 致命 | FLAG_RE 三套分裂 | ledger.py + ctxcompress.py + tools_misc_ext.py | ✅ |
| P0-1 | 高·模型 | looks_failed 成功误判失败 | ledger.py | ✅ |
| P0-2 | 高·bug | run_role gate 传死 role | solver.py | ✅ |
| P0-3 | 高·bug | submit 异常锁死正确答案 | state.py + solver.py | ✅ |
| P0-4 | 高·bug | default_question_id 跨题污染 | state.py + crew.py + solver.py | ✅ |
| P0-5 | 高·安全 | sqlmap 重定向逃逸白名单 | tools_websql.py | ✅ |
| P0-6 | 高·模型 | no-progress 刹车失效（时变字段） | solver.py | ✅ |
| P0-7 | 中·模型 | 容器上下文 16k≠32k 承诺 | agent_main.py | ✅ |
| P1-1 | 中·bug | normalize_args 不含 headers | ledger.py | ✅ |
| P1-2 | 中·bug | GET 缓存键不含自定义头 | tools_local.py | ✅ |
| P1-3 | 中·模型 | 否定句误判「声称发现」 | ledger.py | ✅ |
| P1-4 | 中·模型 | evidence/flag 观测通道混流 | crew.py | ✅ |

未修（附理由）：截断告知（价值边缘）、DEFAULT_ROLES 同步（双源漂移）、solve/run_role 双循环合并（高风险维护性）、prompt 内容（需单独批准）。

---

## 1. P0-致命：FLAG_RE 三套分裂 → 单源

**问题**：ledger / ctxcompress / tools_misc_ext 各写一份 flag 正则，口径不一：
- ledger 松版无长度上限 → CSS `body{color:red}` 误报；
- ctxcompress 紧版 `FLAG\{` 无 IGNORECASE → 仓库实际格式 `flag{}`/`ctf{}` 全盲；
- 三处谁改谁漂移，无唯一真源。

**修复**：ledger 定为唯一源，另两处 `from .ledger import FLAG_RE`。

```python
# ledger.py（唯一真源）
FLAG_RE = re.compile(r"[A-Za-z0-9_]{2,}\{[^}\s:]{4,120}\}")

# ctxcompress.py / tools_misc_ext.py（改为 import，删除本地定义）
from .ledger import FLAG_RE
```

**验证**：`FLAG_RE is FR2 is FR3`；`body{color:red}`/`.error{display:none}` 不匹配；`flag{}`/`FLAG{}`/`ctf{}`/`TPCTF{}` 全兼容；`ctf{xx}`（内容 <4）正确拒绝。

---

## 2. P0-1：looks_failed 成功误判失败 → 语义字段判定

**问题**：除 http_request 外所有工具用子串匹配 `FAILURE_MARKERS`（含 "failed"/"401"/"error"/"拒绝"…）。
http_burst 输出恒含字面键 `"failed"`、fuzz 命中天然 401/403、sqli 发现注入时 type 是 "error-based"、
authdiff baseline 天然 401 —— 成功调用全被判失败，12 次后整个工具方向被「证伪」并注入「禁止再试」。

**修复**：按工具语义字段判「成功」，命中成功信号直接放行；未命中回退子串匹配（不引入新误判）。

```python
_STRUCTURED_SUCCESS = {
    "http_burst": ("ok",),                      # ok>0
    "fuzz": ("matched",),                       # matched>0
    "sqli": ("injectable",),                    # injectable=True
    "http_map": ("links", "api_hints", "hits"), # 爬到/探测到端点
    "jsrecon": ("api", "secrets"),              # 抽到 API 面/密钥
    "traffic": ("total", "shown"),              # 检索到记录
}
_STRUCTURED_TOOLS = frozenset(_STRUCTURED_SUCCESS) | {"authdiff"}  # authdiff 特判 rows 非 same

def looks_failed(text, tool=""):
    if tool == "http_request": ...            # 原结构化 status
    if tool == "chat":
        if "[reply]" in text and "(空回复)" not in text:
            return False                      # 多轮诱导前几轮必拒，不判失败
    if tool in _STRUCTURED_TOOLS and text.startswith("{"):
        data = json 解析
        if _structured_success(tool, data):
            return False
    return any(marker in lowered for marker in FAILURE_MARKERS)   # 回退
```

**验证**：8 工具成功场景不误判、失败场景仍判失败、chat 拒绝回复不判失败。

---

## 3. P0-2：run_role gate 传死 role

**问题**：`solver.py` 双循环各有一处 gate。solve 循环 gate/记账都传 `"solver"`（一致，无 bug）；
run_role 循环 gate 传 `"solver"`、记账传真实 role（`role=role`）→ crew 模式 GET 去重闸失效，
专家角色反复重发同一请求（每步约 7k token 浪费）。

**修复**：run_role 的 gate 改传真实 role。

```python
# solver.py（run_role 循环，用 platform_names 上下文唯一定位）
- gate = self._ledger_gate(ledger, name, args, role="solver")
+ gate = self._ledger_gate(ledger, name, args, role=role)
```

---

## 4. P0-3：submit 异常锁死正确答案

**问题**：`reserve_submit` 在 `call_raw` 前落账（attempts+1、flag 入 flags_tried）。
网络异常时异常穿透 crew.solve → 整 run 作废；重跑提交同一 flag 被 `reserve_submit` 判 "duplicate" ——
正确答案被幂等永久锁死。

**修复**：新增回滚方法 + call_raw 包 try。

```python
# state.py（新增）
def release_submit(self, challenge_id, flag):
    with _lock_exclusive(self._lock_path()):
        self._refresh_flags(challenge_id)
        item = self.require(challenge_id)
        digest = flag_hash(flag)
        if digest in item.flags_tried:
            item.flags_tried.remove(digest)
            item.attempts = max(0, item.attempts - 1)
        item.touch(); self.save()

# solver.py（call_raw 包 try）
try:
    result = self.platform.call_raw(submit_name, payload)
except Exception as exc:
    self.store.release_submit(challenge_id, answer)
    return f"[提交异常] 网络错误，本次提交未送达平台（已回滚提交额度）：{type(exc).__name__}: {exc}", False
```

---

## 5. P0-4：default_question_id 跨题污染

**问题**：多问题题的「下一道 question_id」写进 `config.extra["default_question_id"]`（共享 config）。
CLI 批量跑多题时，题 A 写下的 id 污染题 B 的提交。

**修复**：落盘 per-challenge 字段。

```python
# state.py（Challenge dataclass 新增字段）
next_question_id: str = ""   # 多问题题：下一道待提交的 question_id（per-challenge 隔离）

# crew.py（写入）
- self.config.extra["default_question_id"] = rest[0]
+ challenge.next_question_id = rest[0]
+ self.store.save()

# solver.py（读取）
- question_id = str(args.get("question_id") or self.config.extra.get("default_question_id") or "flag")
+ question_id = str(args.get("question_id") or self.store.require(challenge_id).next_question_id or "flag")
```

---

## 6. P0-5：sqlmap 重定向逃逸白名单

**问题**：`--max-redirects=3` 让 sqlmap 跟随重定向，而 sqlmap 无逐跳回调，
跳转目标不经 `_check_target` 白名单复查 —— 全链路唯一「重定向逃逸白名单」出口。

**修复**：禁止 sqlmap 跟随重定向，跳转目标改由模型用 http_request 显式访问（会过白名单）。

```python
# tools_websql.py
- "--timeout=10", "--retries=0", "--threads=1", "--max-redirects=3",
+ "--timeout=10", "--retries=0", "--threads=1", "--max-redirects=0",
```

---

## 7. P0-6：no-progress 刹车失效（时变字段）

**问题**：no-progress 用 `str(text)` 全文哈希。工具输出带 `elapsed_ms`/`ms` 耗时键，
「同样的失败输出」每次哈希都不同 → `step_progress` 恒真 → 原地打转刹车永不触发（039/043 实机形态）。

**修复**：哈希前剥离时变字段。

```python
# solver.py
_TIMEVARY_KEYS = frozenset({"elapsed_ms", "ms", "duration", "ts", "timestamp",
                            "started_at", "finished_at", "created_at", "updated_at"})

def _stable_output(text: str) -> str:
    raw = str(text).strip()
    if raw.startswith("{"):
        try:
            data, _ = json.JSONDecoder().raw_decode(raw)
        except (JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if str(k).lower() not in _TIMEVARY_KEYS}
            return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return raw

# 哈希处
- digest = hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()[:16]
+ digest = hashlib.sha1(_stable_output(str(text)).encode("utf-8", "replace")).hexdigest()[:16]
```

**验证**：`elapsed_ms` 100/200 但内容相同 → 哈希一致；内容变化 → 哈希不同。

---

## 8. P0-7：容器上下文 16k≠32k 承诺

**问题**：`agent_main.py` 无条件把 `max_context_tokens` 覆盖成 16000，而 agent.json 与各角色提示词
承诺 32k —— 模型按 32k 规划，16k 就丢旧步骤。

**修复**：

```python
# agent_main.py
- DEFAULT_MAX_CONTEXT = 16000
+ DEFAULT_MAX_CONTEXT = 32000   # 对齐 agent.json 与角色提示词的「上下文 32k」承诺
```

---

## 9. P1-1：normalize_args 不含 headers

**问题**：http_request 去重键只看 method/url/body，不看 Cookie/Authorization/X-*。
「登录前/后同 URL 重读」被误拦，拦截文案还断言「重试不会改变结果」—— 与 cookie jar 自动续期语义相反。

**修复**：提取「决定结果」的头指纹进键；UA/Accept 等无关头忽略。

```python
# ledger.py
_HEADER_KEYS_SENSITIVE = frozenset({
    "cookie", "authorization", "proxy-authorization", "x-api-key", "x-auth-token",
})

def _header_fingerprint(headers) -> str:
    ...  # 只取 _HEADER_KEYS_SENSITIVE 与 x-* 头，排序后 json 序列化

# normalize_args http_request 分支
hdr = _header_fingerprint(args.get("headers"))
if hdr:
    out["headers"] = hdr
```

---

## 10. P1-2：GET 缓存键不含自定义头

**问题**：缓存键 `(netloc, path_q, cookie)` 不含 Authorization/X-*，
头绕过/鉴权题「带 X-Role:admin 与无头请求」共用缓存 → 假阳/假阴旧响应。

**修复**：

```python
# tools_local.py
hdr_sig = tuple(sorted(
    (str(k).lower(), str(v)) for k, v in forwarded.items()
    if str(k).lower() == "authorization" or str(k).lower().startswith("x-")
))
cache_key = (netloc, path_q, effective_cookie, hdr_sig)
```

---

## 11. P1-3：否定句误判「声称发现」

**问题**：`_NEG_CLAIM_RE` 否定词与断言词两集不全，且只处理「否定前缀」。
「未能成功登录」「没能绕过 WAF」「密码是错的」「口令是无效的」被 `claims_discovery` 判成「声称有发现」，
M4 强制补 evidence → 幻觉 report 被拒 → 空转。

**修复**：补否定前缀 + 断言词同步 + 新增后置否定。

```python
# ledger.py
_NEG_CLAIM_RE = re.compile(
    r"(未|没|没有|无|未能|没能|没法|不能|无法|not|no|never|cannot|can't|failed to|unable to|did not|didn't|couldn't)\s*"
    r"(找到|发现|拿到|取得|绕过|泄露|获得|确认|成功登录|登录成功|凭证有效|"
    r"found|obtained|bypassed|leaked|confirmed|logged in|valid credential)",
    re.IGNORECASE,
)
_NEG_CLAIM_POST_RE = re.compile(
    r"(密码是|口令是|凭证是|password is|credential is)\s*(错的|错误|无效|无效的|不对|incorrect|wrong|invalid)",
    re.IGNORECASE,
)

# claims_discovery
raw = _NEG_CLAIM_RE.sub("", _norm_scalar(text))
raw = _NEG_CLAIM_POST_RE.sub("", raw)   # 新增
```

---

## 12. P1-4：evidence/flag 观测通道混流

**问题**：`ledger.observations` 混入两类——工具输出收割的 flag 形态（`_harvest_observation`）
与 report evidence 溯源（`_check_report` 里 `ledger.observe(evidence)`）。后者可能是凭据/路径等非 flag 子串。
候选闸门取 `observations[-1]` 推去 submit → 非 flag 空烧 submit 次数还扣 attempts。

**修复**：候选闸门只推 flag 形态观测。

```python
# crew.py
flag_cands = [o for o in ledger.observations if FLAG_RE.search(o.text)]
if flag_cands and challenge.attempts == 0 and not flag_nudged:
    flag_nudged = True
    messages.append({"role": "user",
                     "content": _FLAG_CANDIDATE_HINT.format(cand=flag_cands[-1].text)})
```

---

## 13. 测试同步

`tests/smoke.py` `case_sqli_functional` 的 argv 断言：`--max-redirects=3` → `--max-redirects=0`。

---

## 附：验证记录

| 验证 | 结果 |
|---|---|
| `tests/smoke.py`（三轮：P0-a→P0-b→P1/P2） | 97/97, 97/97, 97/97 |
| 针对性验证 1（FLAG_RE/looks_failed/否定句/headers） | 18/18 |
| 针对性验证 2（7 工具结构化判定 + 稳定哈希） | 核心项全通过 |
| 针对性验证 3（chat 特判 4 态） | 4/4 |
