# 第二批修复方案（2026-11-03）

> 本文档是**动手前的方案存档**，与回档锚点配合使用。
> 回档锚点：git 分支 `audit-fix-backup`（第一批修复完成、160/0 通过的稳定态）。
> 每项改动都标注「文件:行号 + 改法 + 风险」，逐项可独立回滚。

---

## 回档方式

```bash
# 查看快照分支
git branch -a | grep audit-fix-backup

# 回档到第一批修复完成的稳定态（丢弃第二批之后的所有改动）
git stash            # 先暂存未提交改动（可选）
git checkout audit-fix-backup -- src/ tests/   # 只回滚代码，不影响其他
# 或整体回退
git reset --hard audit-fix-backup
```

---

## 修复项清单（按优先级 / 风险排序）

| 编号 | 问题 | 优先级 | 风险 | 涉及文件 |
|---|---|---|---|---|
| B-3 | 部分解状态机自相矛盾 | P0 | 中 | state.py / solver.py / crew.py |
| B-4a | QA 提交 competition_id 缺参 | P1 | 低 | solver.py |
| B-4b | upsert 白名单丢 is_qa_only | P1 | 低 | state.py |
| B-5 | 并发写竞态三连 | P1 | 高 | state.py / checkpoint.py / crew.py |
| A-2 | 凭据明文落盘 | P1 | 高 | solver.py / ledger.py / traffic.py / checkpoint.py |

> 建议**先做 B-4（低风险），再做 B-3（中风险），B-5 和 A-2 单独确认后再做**（涉及并发/脱敏，改动面广，需回归验证）。

---

## B-4a：QA 提交 competition_id 缺参（低风险）

**现状**（`solver.py:740-744`）：

```python
if "competition_id" in props:
    comp = str((getattr(self.store, "meta", {}) or {}).get("competition_id") or "")
    if comp:
        payload["competition_id"] = comp
```

`meta.competition_id` 只在 `_competition_id()`（L330）写入，而 `_competition_id` 仅在**实例路径**（`_start_with_backoff` → `start_instance`）触发。纯 QA 题从不走实例路径 → `meta.competition_id` 为空 → 缺参 400。

**改法**：把只读 meta 改成主动反查：

```python
if "competition_id" in props:
    comp = str(self._competition_id(challenge_id) or "")
    if comp:
        payload["competition_id"] = comp
```

**效果**：QA 题提交时若 meta 无缓存，会走 `_competition_id(challenge_id)` 的 membership 反查（list_competitions → list_challenges），命中后写回 meta 缓存，之后零开销。

**风险**：低。`_competition_id` 反查失败返回 None（已有 try/except 兜底），不影响主流程；只是多一次网络请求（反查已设计为缓存）。

---

## B-4b：upsert 白名单补 is_qa_only（低风险）

**现状**（`state.py:267-273`）：

```python
def upsert(self, data: dict) -> Challenge:
    ...
    for field_name in ("name", "category", "points", "description"):
        value = data.get(field_name)
        if value not in (None, ""):
            setattr(item, field_name, value)
```

白名单不含 `is_qa_only`。`cli.py:88` 已经归一化 `"is_qa_only": bool(pick("is_qa_only"))`，但 `upsert` 落地时丢弃，`solver.py:719` 的 `getattr(challenge, "is_qa_only", False)` 恒 False，只能靠 tags 兜底。

**改法**：白名单扩一个 bool 字段（注意 bool 的 None/空判断要单独处理，因为 `False` 也是合法值）：

```python
for field_name in ("name", "category", "points", "description"):
    value = data.get(field_name)
    if value not in (None, ""):
        setattr(item, field_name, value)
# bool 字段单独处理：False 也是合法值，不能用 `value not in (None, "")` 判空
qa = data.get("is_qa_only")
if qa is not None:
    item.is_qa_only = bool(qa)
```

**风险**：低。`is_qa_only` 字段已存在于 `Challenge` dataclass（state.py:152），`from_dict` 会自动识别；只是补上 `upsert` 的写入。

---

## B-3：部分解状态机自相矛盾（中风险）

### 问题链（三处自相矛盾）

1. **`solver.py:802`**：`_submit` 里 `accepted=True` 就 `mark_submitted(challenge_id, answer, accepted=True)`，无条件把 `status="submitted"` + `submitted_ok=True` 落盘。
2. **`crew.py:1015-1016`**：随后从回执解析 `challenge_fully_solved=false`，把**内存** `submitted` 改回 False、`reason="partial_solve"`，但 **store 已落盘成 submitted**。
3. **`crew.py:1297`**：`precheck_fn` 见 `status in ("submitted","done")` 直接 `return True` 短路 → 剩余题号永远不再提交。

### 改法

**核心思路**：给 `mark_submitted` 增加 `fully_solved` 参数，`fully_solved=False` 时只记 flag/题号、**不置 status="submitted"**，让状态机保持「未全解」可继续提交。

**改法 1**（`state.py:345-355`）——`mark_submitted` 加参数：

```python
def mark_submitted(self, challenge_id: str, flag: str, *, accepted: bool, fully_solved: bool = True) -> None:
    """提交结果出来后回写通过状态（不回退 attempts / flags_tried）。

    fully_solved=False 表示多问题题只过了当前题号（challenge_fully_solved=false），
    只记 flag 不置 submitted，避免 precheck_fn 误判已全解而漏掉剩余题号。
    """
    if not accepted:
        return
    with _lock_exclusive(self._lock_path()):
        item = self.require(challenge_id)
        item.submitted_ok = item.submitted_ok or fully_solved  # 全解才置 submitted_ok
        item.flag_value = flag
        if fully_solved:
            item.status = "submitted"
        item.touch()
        self.save()
```

**改法 2**（`solver.py:799-804`）——`_submit` 解析 `challenge_fully_solved` 传给 `mark_submitted`：

```python
text = render_call_result(result)
# 解析回执里的 challenge_fully_solved（多问题题是否全解），据此决定是否置 submitted
fully_solved = True
if isinstance(result, dict):
    fs = result.get("challenge_fully_solved")
    if fs is False:
        fully_solved = False
# 也兜底文本回执（平台可能返回 JSON 字符串而非 dict）
else:
    parsed = self.platform._as_json(text) if isinstance(text, str) else None
    if isinstance(parsed, dict) and parsed.get("challenge_fully_solved") is False:
        fully_solved = False

if accepted is True:
    self.store.mark_submitted(challenge_id, answer, accepted=True, fully_solved=fully_solved)
    self.on_event("flag_accepted", {"challenge_id": challenge_id})
    return f"{text}\n\n[判定] 平台确认该答案通过。", True
```

**改法 3**（`crew.py:1295-1298`）——`precheck_fn` 改为只认「全解」：

```python
def precheck_fn(cid: str) -> bool:
    ch = store.require(cid)
    # 只有「全解/已结束」才短路；partial_solve 时 status 已不再是 submitted，天然放行
    if ch.status in ("done",) or getattr(ch, "submitted_ok", False):
        return True
    try:
        return cid in self.platform.my_solved_ids()
    except Exception:
        return False
```

> 注意：`crew.py:1410-1411`（集群-B 预检）有同样逻辑，需同步改。

**风险**：中。`mark_submitted` 的 `submitted_ok` 语义从「交过且 accepted」变成「全解」，需确认没有其他地方依赖 `submitted_ok` 的旧语义。已 grep：`submitted_ok` 的使用点在 crew.py:1297/1411/1440/1514-1526，均用于「是否已解出」判定，新语义（全解）与之一致。

---

## B-5：并发写竞态三连（高风险，暂缓建议）

**触发条件**：集群-A（cluster_a 多路并发）或多进程开启时；默认关闭，暂不触发。改动涉及锁语义、文件命名、合并逻辑，回归面大。

| 位置 | 问题 | 改法 |
|---|---|---|
| `state.py:252` | `save()` tmp 名只含 pid，同进程多线程互相截断；无 fsync | tmp 名加 `threading.get_ident()` + uuid4；写后 `fh.flush()+os.fsync(fd)` |
| `checkpoint.py:124` | tmp 名固定 `*.tmp`，各 lane 共用 checkpoint | 每路 checkpoint 加 lane 后缀，或集群-A 禁用 checkpoint |
| `state.py:303-322` | `_refresh_flags` 只合并三字段，save 全量覆盖回滚他人计数 | `save()` 改锁内 merge-then-write |
| `state.py:37-42` | Windows `_lock_exclusive` 首锁不互斥（fd 偏移问题） | 加锁前 `os.lseek(fd,0,0)`；`LK_LOCK` 改自旋 |

**建议**：B-5 单独一个 PR 处理，先写单测复现竞态，再改。不在本批混入。

---

## A-2：凭据明文落盘（高风险，暂缓建议）

**现状**：transcript / ledger 标签 / traffic 库 / checkpoint 四处原样存 Authorization、Cookie、flag。

**改法方向**：
1. 统一一个 `redact(text)` 函数：对 `Authorization:`, `Cookie:`, `Set-Cookie:`, `token=`, `api_key=`, `password=` 等敏感键做掩码（保前缀 + `****`）。
2. 在 4 处写盘前调用 `redact`。
3. flag 值在 ledger/checkpoint 里用 hash（已有 `flag_hash`）替代明文。

**风险**：高。脱敏可能误伤正常回显（模型需要看到完整 header 才能解题），需精细控制「落盘脱敏」与「回显不脱敏」的边界。需单独确认脱敏粒度。

**建议**：A-2 单独处理，先明确「哪些字段必须脱敏、哪些保留明文给模型」，再动手。

---

## 执行顺序建议

1. ✅ B-4a + B-4b（低风险，一行级改动）→ 跑 smoke 确认 160/0
2. ✅ B-3（中风险，三处联动）→ 跑 smoke + 补一个 partial_solve 单测
3. ⏸ B-5、A-2（高风险）→ 单独确认后另开批次

---

## 验证口径

- 每批改动后：`NO_PROXY=127.0.0.1,localhost PYTHONIOENCODING=utf-8 python tests/smoke.py` 必须 160/0。
- B-3 需新增单测：构造「多问题题第一问答对但 challenge_fully_solved=false」场景，断言 store 未置 submitted、precheck_fn 不短路、next_question_id 指向剩余题。
