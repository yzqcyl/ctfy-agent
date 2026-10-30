# O1 实现规格：多 pattern grep + 非 200 响应缓存

> 状态：**已实现（2026-10-31，门 A 后）**。依据：`docs/audit-behavior-2026-10-30.md`（实测）。
> 实现落点：`src/ctfy_agent/tools_local.py`（渲染/缓存/schema）、`src/ctfy_agent/ledger.py`（去重键）、
> `tests/smoke_http_cache.py`（24 条离线断言，全过）。
> **实现偏离（相对本规格原稿，均记录理由）**：
> 1. schema 不用 `"type": ["string","array"]`（全仓库无 union 类型先例，provider 严格校验会连累
>    核心工具）；改为 `grep` 保持 string 不变 + 新增 `greps` 字符串数组参数，运行时合并。
> 2. 403/404 不是「单列 64 条」，而是**超容时优先逐非 200 条目**（总容量仍 16）；理由：总容量
>    16 < 64 时单列上限无意义，真正要保的是 200 的真实攻击面。
> 3. `grep + offset`：offset>0 时先切窗口再搜（报绝对偏移）；offset=0 仍搜全文（旧输出逐字节兼容）。
> 4. 顺带修掉一个潜伏 bug：`_cache_put` 的 FIFO 用了 `dict.popitem(last=False)`（OrderedDict API），
>    缓存超过 16 条必崩；此前因 200-only 缓存很少超 16 而没暴露，O1 让 403/404 进缓存后会更早触发。

## 1. 现状（先别重复造轮子）

HTTP 响应缓存**已存在**（并行会话 v3 改动），已具备：

| 能力 | 位置 | 说明 |
|---|---|---|
| 缓存键 | `tools_local.py:871` | `(netloc, path_q, cookie, auth/x-头签名)` —— **不含 grep**，故换 grep 也是缓存命中 |
| 命中后本地切片/筛 | `:885-897` | 无 grep/offset → 返回桩（`_render_cache_stub`，:742）；带 grep/offset → 从缓存正文筛/切，**省一次网络往返** |
| 容量控制 | `_cache_put` :728 | FIFO，`CACHE_MAX_ENTRIES` / `CACHE_MAX_BYTES` 双限 |
| 失效 | `_invalidate_host` :724；`solver.py:853` | 按 host 清；每次 `_solve_once` 清空（防跨题串味） |
| 生命周期 | `solver.py:265/908/1196` | Solver 实例级、**同题内跨角色共享**（web/rev/crypto 共用一份） |
| 遥测 | `:877-884` | 命中写入 traffic（`cache_hit=1`） |

**结论：O1 的工作量不是建缓存，而是补两个缺口。**

## 2. 缺口（审计量化）

1. **单 pattern 限制造成「同 URL 多 grep」重复调用**：v0 33 个 URL 出现多 grep、paced 11 个
   （各自额外 1–3 次调用）。缓存省了网络，但**调用次数没省**——而调用数才是预算单位（≈12k tok/次）。
2. **只缓存 GET+200**（`:962` `if method == "GET" and status == 200`）：重复探测的 403/404 页每次
   都重新下载；审计里 P0 后残留的 8+6 次无信息重复基本都落在这里。

## 3. O1a：`grep` 支持多 pattern（主收益）

### 3.1 Schema（`tools_local.py` HTTP_SPEC）

```python
"grep": {"type": "string", ...},                     # 旧参数，语义不变
"greps": {                                            # O1a 新增（数组先例：http_burst.values）
    "type": "array", "items": {"type": "string"},
    "description": "正则数组（最多 6 个，与 grep 并用）；任一命中即返回，输出按偏移升序 + grep_matched 计数",
},
"fresh": {"type": "boolean", ...},                    # O1b：跳过缓存读
```

> 运行时把 `grep` 与 `greps` 合并成列表；**只有一个 pattern 时走旧渲染路径，输出逐字节不变**。

### 3.2 语义

- `grep` 规范化：`str → [str]`；去空、去重、保序，**截断到 6 个**（超出在返回里提示）。
- 匹配：逐 pattern `re.finditer`，合并所有命中，按**字符偏移升序**去重（同偏移重叠片段取首个 pattern），
  每条带 `±80` 字符上下文与偏移（沿用 `_render_http` :1054 现有格式）。
- 上限：**每 pattern ≤8 条、全局 ≤30 条**（沿用现有 30 条总限）；超限时在尾部报告
  `"grep_truncated": {"pat": 命中总数, ...}`，让模型知道有被截断的命中。
- 非法正则：该 pattern 单独报错（`{"pattern": "...", "error": "bad regex"}`），不影响其余。
- 输出新增字段：`"grep_matched": [{"pattern": p, "count": n}, ...]`；单 pattern 时该字段可省略（保持旧输出形状不变，降 token）。

### 3.3 兼容与回归

- `grep=""` / 缺省：行为与现在逐字节一致（有单测兜底：旧输出快照）。
- `grep="x"`：语义不变，仅可能多一个 `grep_matched` 字段——为不破坏快照，**单 pattern 时不加该字段**。
- 与 `offset` 组合：先按 offset/limit 切片（现有语义），再在切片内 grep；文档串写明。

## 4. O1b：非 200 响应缓存（次收益）

- 缓存条件放宽为 `method == "GET" and status in (200, 403, 404)`，且 `len(payload) ≤ 64KB`。
- 键不变（状态码在值里）；`_render_cache_stub` 已带 `status` 字段，无需改渲染。
- 目的：重复探测同一不存在路径/被拒端点时直接命中缓存桩，不再下载、不再耗网络。
- 风险控制：403/404 变化快（登录后 403→200），故 **命中后仍需在桩里提示** `note` 增加
  「若刚做过写操作/登录，此结果可能过期，可加 `?fresh=1` 强制重取」；实现 `fresh=1` 参数（`fresh: bool = False`）跳过缓存读。
- 上限：403/404 条目单独限 ≤64 条，防刷接口把 200 条目挤出去。

## 5. 验收（`tests/smoke_http_cache.py`，无网络，用 `_render_http`/`_cache_put` 单测 + 假 opener）

原 12 条全部覆盖，另加 2 条账本交互（13/14），共 **24 条断言，全过**：

1. 多 pattern OR 命中、偏移升序、去重、每 pattern ≤8 条；
2. 全局 30 条上限 + `grep_truncated` 报告正确；
3. 非法正则只影响自身；
4. 单 pattern 输出与旧实现逐字节一致（快照）；
5. 7 个 pattern 被截断到 6 并在输出提示；
6. GET 404 二次请求走缓存桩（opener 计数=1）；
7. `fresh=1` 强制重取（opener 计数=2）；
8. POST 不缓存（两次 POST 都发网络）；
9. 403/404 刷屏不挤占 200 条目（超容优先逐非 200）；
10. `solver._solve_once` 开头 `_http_cache.clear()`（跨题隔离回归）；
11. 多角色共享缓存（同题两个 LocalTools 实例 → 计数=1）；
12. `grep + offset` 组合语义（窗口内命中报绝对偏移；窗口外不返回）；
13. `greps` 数组进账本去重键、同参撞键；
14. `fresh=1` 与普通 GET 键不同（账本不会误拦 O1b 逃生口）。

## 6. 预期收益（基于审计实测，给出区间不吹）

| 项 | 计算 | 收益 |
|---|---|---|
| O1a 多 grep 合并 | v0：33 个 URL 有 2–6 个 grep；每个省 (n−1) 次调用 ≈ 12k tok | 约 **0.4–0.6M tok / 30 题**（≈3–4% 预算）|
| O1b 非 200 缓存 | P0 后残留无信息重复 14 次/16 题 | ≈ **0.17M tok / 16 题**（小）|
| 合计 | — | **≈4% 预算**，等价于每题多 1.6 次探索调用 |

> 收益不大但是**确定性**的（不依赖模型行为，工具层直接省），且 O1a 顺带减少网络失败面。

## 7. 风险与回滚

| 风险 | 处置 |
|---|---|
| 缓存脏数据（403→200 场景） | `fresh=1` 逃生口 + 桩提示；`_invalidate_host` 现有失效逻辑保留 |
| 内存 | 容量上限沿用；非 200 单列 64 条上限 |
| 并发（cluster-A 线程共享 Solver？） | 实现前确认：若多线程共享同一 cache dict，`_cache_put` 的 evict 循环需加 `threading.Lock`（约 5 行） |
| 输出变大 | 单 pattern 路径输出不变；多 pattern 才多 `grep_matched`（<100 tok） |
| 回滚 | 全部改动集中在 `tools_local.py` 两个函数 + schema，`git revert` 单提交即可 |