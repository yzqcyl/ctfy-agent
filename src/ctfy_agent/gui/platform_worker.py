# -*- coding: utf-8 -*-
"""平台接口模式的 GUI 工作线程：连接测试 / 拉题单 / 跑 Crew 解题。

全部复用现有 CLI 与 Crew 的公开入口（make_platform / open_store / ensure_challenge_meta /
Crew / solve），不改动这些模块；结果经 queue 回主线程。
"""
from __future__ import annotations

import copy
import queue
import threading
import time
from dataclasses import replace

from ..access_facts import playability_blockers
from ..taskplan import plan as plan_tasks

# 「按难度动态分配时间预算」（能力评估 §4.1/§6-②）：122 题里 43 题因墙钟被打断，
# 固定 15 分钟对 Easy 太松、对 Hard 太紧。默认 Easy 10 / Medium 25 / Hard 40 分钟；
# 经 config.extra.{time_budget, time_budget_minutes, time_budget_fallback} 可关/可调。
_BUDGET_MINUTES = {"easy": 15, "medium": 25, "hard": 40}


def _budget_minutes(config, difficulty: str) -> int | None:
    """返回某难度的时间预算（分钟）；返回 None 表示关闭（沿用旧的全局取消行为）。"""
    if not config.extra.get("time_budget", True):
        return None
    diff = str(difficulty or "").strip().lower()
    override = config.extra.get("time_budget_minutes")
    if isinstance(override, dict) and diff in override:
        try:
            return int(override[diff])
        except (TypeError, ValueError):
            pass
    if diff in _BUDGET_MINUTES:
        return int(_BUDGET_MINUTES[diff])
    try:
        return int(config.extra.get("time_budget_fallback", 20))
    except (TypeError, ValueError):
        return None


def _arm_budget(global_cancel: threading.Event, minutes: int | None,
                deadline: float | None = None) -> tuple[threading.Event, bool]:
    """给单题装一个软停止信号：预算时间到 / deadline 到 / 全局取消时置位；不影响其它题。

    ``deadline`` 是**绝对时间戳**（epoch 秒，赛程结束或 supervisor 给的单题 deadline）；
    与相对 ``minutes`` 取先到者。返回 (event, engaged)；engaged=False 表示没装计时器。
    """
    ev = threading.Event()
    if not minutes and not deadline:
        return ev, False

    def watch() -> None:
        stop_at = None
        if minutes and minutes > 0:
            stop_at = time.monotonic() + minutes * 60
        if deadline:
            left = float(deadline) - time.time()
            if left <= 0:
                ev.set()
                return
            deadline_mono = time.monotonic() + left
            stop_at = deadline_mono if stop_at is None else min(stop_at, deadline_mono)
        while not ev.is_set():
            if global_cancel is not None and global_cancel.is_set():
                ev.set()
                return
            if stop_at is not None and time.monotonic() >= stop_at:
                ev.set()
                return
            time.sleep(1.0)

    threading.Thread(target=watch, name=f"budget-{minutes or 'deadline'}m", daemon=True).start()
    return ev, True


def _is_transient_net(message: str) -> bool:
    """平台/网关瞬断判定（供单题重试用）。确定性错误（401/404/参数错）不重试。"""
    msg = str(message).lower()
    return any(key in msg for key in (
        "remote end closed", "connection reset", "connection aborted", "eof occurred",
        "incompleteread", "broken pipe", "timed out", "timeout", "502", "503", "504",
        "bad gateway", "service unavailable", "temporarily unavailable",
    ))


def _platform(config, verbose: bool = False, cancel=None):
    from ..cli import make_platform  # noqa: PLC0415
    from .platform_adapter import install  # noqa: PLC0415

    # 装上「参数名桥接」：异构平台（如 DASCTF 练习场）参数名与约定不同，
    # 这层按目标工具的 inputSchema 自动翻译，对 CTFY 这类平台是纯 no-op。
    platform = install(make_platform(config, verbose=verbose))
    if cancel is not None:
        bridge = getattr(platform, "_arg_bridge", None)
        if bridge is not None:
            # 让「立即停止」能打断实例就绪轮询（否则最多空等 120s）
            bridge.cancel_event = cancel
    return platform


def _seed_bridge(platform, competition_id: str | None) -> None:
    """把已知的比赛/练习场 id 预置给参数桥接层。

    异构平台（DASCTF 练习场）的 get_challenge / start_environment / submit_flag
    都要求 practice_ground_id，而 ensure_challenge_meta 只传 challenge_id，
    不预置就会「缺少必填参数」。
    """
    bridge = getattr(platform, "_arg_bridge", None)
    if bridge is not None and competition_id:
        bridge.seed(competition_id=competition_id)


# 比赛级只读信息（play_open / 下一局时间）按 competition_id 缓存一次，避免并发线程重复拉。
_COMP_CACHE: dict[str, dict] = {}
_COMP_LOCK = threading.Lock()


def _competition_info(platform, competition_id: str | None) -> dict:
    """只读拉一次比赛信息；失败返回 {}，永不抛（拿不到就当没有阻塞）。"""
    cid = str(competition_id or "").strip()
    if not cid:
        return {}
    with _COMP_LOCK:
        cached = _COMP_CACHE.get(cid)
    if cached is not None:
        return cached
    data: dict = {}
    try:
        raw = platform.get_competition(cid)
        if isinstance(raw, dict):
            data = raw
    except Exception:  # noqa: BLE001 - 只读增强，失败不拦解题
        data = {}
    with _COMP_LOCK:
        _COMP_CACHE[cid] = data
    return data


def preflight_playable(platform, competition_id: str | None, challenge_id: str) -> list[str]:
    """开跑前问平台「这题现在能不能玩」，返回阻塞原因（空列表=可以跑）。

    2026-11-16 实测：练习场关闭后（play_open=false / 题目 closed=true / scoring=closed）
    批量解题仍按难度给每题 20 分钟预算并烧掉 41~79 万 token，最后把「题不可玩」显示成
    「未解出 / 时间预算到」。这里在起预算、派专家**之前**判定，命中即 0 token 跳过。
    """
    comp = _competition_info(platform, competition_id)
    info: dict = {}
    try:
        raw = platform.get_challenge(challenge_id)
        if isinstance(raw, dict):
            info = raw
    except Exception:  # noqa: BLE001 - 读不到题目信息就当没阻塞，交给后续流程报错
        info = {}
    return playability_blockers(comp, info)


# ---------- 赛程级排序 / 按题预算（见 ctfy_agent.taskplan） ----------

def _parse_ts(value) -> float | None:
    """平台时间字段 → epoch 秒；支持 数字 / 数字字符串 / ISO8601。认不出返回 None。"""
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        return raw / 1000.0 if raw > 1e12 else raw
    text = str(value).strip()
    try:
        raw = float(text)
        return raw / 1000.0 if raw > 1e12 else raw
    except ValueError:
        pass
    try:
        from datetime import datetime  # noqa: PLC0415 - 只在解析 ISO 时用

        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _contest_window(comp: dict | None) -> tuple[float | None, float]:
    """比赛级 (截止时间戳, 已进行比例 0~1)。缺字段时 deadline=None、比例=0（不降权）。"""
    if not isinstance(comp, dict):
        return None, 0.0
    deadline = None
    for key in ("ends_at", "end_at", "closed_at", "deadline_at", "end_time"):
        deadline = _parse_ts(comp.get(key))
        if deadline:
            break
    start = None
    for key in ("starts_at", "start_at", "started_at", "start_time"):
        start = _parse_ts(comp.get(key))
        if start:
            break
    elapsed = 0.0
    if start and deadline and deadline > start:
        elapsed = max(0.0, min(1.0, (time.time() - start) / (deadline - start)))
    return deadline, elapsed


def _policy_pool(config) -> float | None:
    """配置里的 token 池；None/0 = 不限池（2026-11-16 用户决定：用自己的 key，不设总池）。

    只有**显式**给了 `config.policy.pool_tokens` 或 `config.extra.contest_pool_tokens`
    才做全局分摊。supervisor 的 `policy.total_token_budget` 是它自己的池，GUI 批量
    不能拿它重复分摊（否则 unlimited 决策被旧配置悄悄改回 4M）。
    """
    extra = getattr(config, "extra", {}) or {}
    policy = getattr(config, "policy", {}) or {}
    raw = extra.get("contest_pool_tokens") or policy.get("pool_tokens")
    try:
        return float(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _with_token_cap(config, quota: int):
    """克隆一份 config，把单题 token 上限换成 quota（不改共享实例，避免并发串味）。

    Crew 从 `config.max_tokens_per_challenge` 取单题累计上限（crew.py 的 Budget），
    所以这里换掉就等价于"这题最多花多少"。非 dataclass（测试/包装对象）时浅拷贝兜底。
    """
    if quota <= 0:
        return config
    try:
        return replace(config, max_tokens_per_challenge=int(quota))
    except TypeError:
        clone = copy.copy(config)
        try:
            setattr(clone, "max_tokens_per_challenge", int(quota))
        except Exception:  # noqa: BLE001 - 只读配置对象：退回原对象
            return config
        return clone


def _plan_row(ch, item: dict) -> dict:
    """把 store 里的 Challenge（+GUI 行兜底）折成 taskplan 需要的纯 dict。"""
    row: dict = {"id": str(item.get("id") or getattr(ch, "id", "") or "")}
    for key in ("name", "points", "difficulty", "solves_count", "questions"):
        value = getattr(ch, key, None) if ch is not None else None
        if value in (None, "", [], {}):
            value = item.get(key)
        row[key] = value
    solved: set[str] = set()
    if ch is not None:
        for attr in ("answers_by_qid", "verified_qids"):
            value = getattr(ch, attr, None)
            if isinstance(value, dict):
                solved.update(str(k) for k in value)
    row["my_solved_question_ids"] = sorted(solved)
    row["my_fully_solved"] = bool(getattr(ch, "submitted_ok", False)) if ch is not None else False
    # 已拿到部分题号 = near-miss：剩余题号通常更好打，quota 加注 50%（taskplan 里处理）
    row["near_miss"] = bool(solved) and not row["my_fully_solved"]
    return row


def _hydrate_attachments(platform, store, challenge_id: str, competition_id: str,
                         emit) -> str:
    """把题目附件下载进本题沙盒，并把清单写进 store 笔记（会进协调者 brief）。

    2026-11-10 实测：13 道未解题里 7 道卡在「沙盒目录为空」，而平台本来下发附件。
    这一步必须在 Crew 开跑前做完；任何失败都只提示，不拦解题。
    """
    try:
        from ..attachments import hydrate  # noqa: PLC0415

        note = hydrate(platform, store, challenge_id, competition_id=competition_id,
                       on_event=lambda kind, payload=None: emit(kind, payload or {}))
        if note:
            emit("attachments", {"challenge_id": challenge_id, "note": note[:600]})
        return note
    except Exception as exc:  # noqa: BLE001 - 附件是加分项，绝不拦解题
        emit("attachments_error", {"challenge_id": challenge_id,
                                   "error": f"{type(exc).__name__}: {exc}"})
        return ""


def _preflight_instances(platform, challenge_id: str, protect: tuple[str, ...], emit,
                         enabled: bool = True) -> None:
    """开跑前清一次残留实例（P0 配额治理）。

    平台每队的运行实例配额只有 1：上一题异常退出没释放，本题 start_instance 直接报
    `agent_environment_limit`，而模型往往把它当成「靶场坏了」白耗几轮。
    instance_guard.reap 的安全边界（见模块注释）：本题永不碰、protect 里的兄弟题不碰、
    拿不到创建时间或存活不到 grace(120s) 的一律不动。任何失败都只提示，绝不拦解题。
    """
    if not enabled:
        return
    try:
        from ..instance_guard import reap, summarize, supports_reap  # noqa: PLC0415

        if not supports_reap(platform):
            # [接手 2026-11-11 实测] DASCTF ctf2 没有 list_instances：reap 在这条平台上是空转，
            # 别给用户「清掉了 0 个残留」的假象；它靠按 challenge_id 查/删环境（solver 自愈路径）。
            emit("instance_guard", {
                "challenge_id": challenge_id,
                "note": "平台未暴露 list_instances，跳过开跑前全量清扫"
                        "（同题残留由 solver 的「按题查环境 → 复用/回收」自愈处理）",
            })
            return
        result = reap(platform, keep=challenge_id, protect=protect, grace_seconds=120.0)
    except Exception as exc:  # noqa: BLE001 - 清扫是加分项
        emit("instance_guard", {"challenge_id": challenge_id,
                                "note": f"开跑前清扫失败（忽略）：{type(exc).__name__}: {exc}"})
        return
    note = summarize(result)
    stopped = len(result.get("stopped") or []) if isinstance(result, dict) else 0
    emit("instance_guard", {
        "challenge_id": challenge_id,
        "note": (f"开跑前清掉 {stopped} 个残留实例：" if stopped else "开跑前实例检查：") + note,
        "detail": result if isinstance(result, dict) else {},
    })


def _teams_of(me) -> list[dict]:
    """从 whoami 结果里取参赛队伍列表。"""
    if not isinstance(me, dict):
        return []
    teams = me.get("competition_teams") or me.get("teams") or []
    return [t for t in teams if isinstance(t, dict)]


def _competitions_of(platform, me) -> list[dict]:
    """比赛/练习场清单：优先 whoami 带的队伍，其次 list_competitions 语义动作。

    异构平台（如 DASCTF 练习场）的 whoami 里没有 competition_teams，
    真正的清单在 list_practice_grounds 这类列表工具里 —— 必须回退，否则界面永远「无比赛信息」。
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(cid, name, phase=None) -> None:
        cid = str(cid or "")
        if not cid or cid in seen:
            return
        seen.add(cid)
        out.append({"id": cid, "name": str(name or cid), "phase": phase})

    for team in _teams_of(me):
        add(team.get("competition_id"),
            team.get("competition_title") or team.get("competition_id"),
            team.get("competition_phase"))
    if out:
        return out
    try:
        data = platform.list_competitions()
    except Exception:  # noqa: BLE001 - 取不到清单不算连接失败
        return []
    if isinstance(data, dict):
        items = data.get("items") or data.get("data") or data.get("list") or []
    else:
        items = data if isinstance(data, list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        add(item.get("id") or item.get("competition_id") or item.get("ground_id")
            or item.get("practice_ground_id"),
            item.get("name") or item.get("title"))
    return out


def _items_of(page) -> list[dict]:
    """平台返回可能是列表，也可能是 {items:[...]} 包装；统一取出条目。"""
    if isinstance(page, list):
        return [x for x in page if isinstance(x, dict)]
    if isinstance(page, dict):
        for key in ("items", "data", "list", "challenges", "results", "rows"):
            value = page.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def test_connection(config, q: queue.Queue) -> None:
    """连接平台并取身份/比赛列表，供界面选比赛。"""
    try:
        platform = _platform(config)
        platform.connect()
        me = None
        try:
            me = platform.whoami()
        except Exception:  # noqa: BLE001 - 身份接口失败不影响连接成功
            me = None
        q.put(("conn_ok", {
            "teams": _teams_of(me),
            "competitions": _competitions_of(platform, me),
            "me": me if isinstance(me, dict) else {},
        }))
    except Exception as exc:  # noqa: BLE001
        q.put(("conn_err", str(exc)))


def fetch_challenges(config, competition_id: str, q: queue.Queue) -> None:
    """拉取指定比赛/练习场的全量题单。

    异构平台差异全部在这里吃掉：
      * 返回可能是列表，也可能是 {items:[...]} 包装 → 统一取条目
      * limit 上限按目标工具 schema 的 maximum 夹紧（DASCTF 是 100，原来写死 200 会被拒）
      * 分页字段可能是 page 或 offset，未提供则只取一页
    """
    try:
        from ..cli import normalize_challenge  # noqa: PLC0415

        platform = _platform(config)
        platform.connect()
        tool = platform.resolve("list_challenges")
        props = platform.prop_names(tool)
        schema = platform.schema_of(tool) or {}
        limit_prop = (schema.get("properties") or {}).get("limit") or {}
        page_size = 100
        if isinstance(limit_prop.get("maximum"), int):
            page_size = min(page_size, limit_prop["maximum"])
        if isinstance(limit_prop.get("minimum"), int):
            page_size = max(page_size, limit_prop["minimum"])

        items: list[dict] = []
        seen_ids: set[str] = set()
        page_no = 1
        while True:
            kwargs: dict = {"limit": page_size}
            if "page" in props:
                kwargs["page"] = page_no
            elif "offset" in props:
                kwargs["offset"] = (page_no - 1) * page_size
            page = platform.list_challenges(competition_id, **kwargs)
            if isinstance(page, str):
                raise RuntimeError(page)
            batch = _items_of(page)
            before = len(items)
            for raw in batch:
                key = str(raw.get("id") or raw.get("challenge_id") or "")
                if key and key in seen_ids:
                    continue
                if key:
                    seen_ids.add(key)
                items.append(raw)
            added = len(items) - before
            total = page.get("total") if isinstance(page, dict) else None
            if len(batch) < page_size or len(items) >= 20000:
                break
            if isinstance(total, int) and len(items) >= total:
                break
            if "page" not in props and "offset" not in props:
                break
            if added == 0:
                # 平台忽略了 page/offset（每页都返回同一批）→ 必须停，否则死循环
                break
            page_no += 1

        rows = []
        for raw in items:
            norm = normalize_challenge(raw)
            if norm.get("id"):
                rows.append(norm)
        q.put(("challenges", {"competition_id": competition_id, "items": rows}))
    except Exception as exc:  # noqa: BLE001
        q.put(("challenges_err", str(exc)))


def solve(config, challenge_id: str, run_id: str, competition_id: str,
          q: queue.Queue, cancel: threading.Event) -> None:
    """平台模式解题：连接 → 补题面 → Crew → 结果。事件流与离线模式同格式。"""
    try:
        from ..cli import ensure_challenge_meta, make_llm, open_store  # noqa: PLC0415
        from ..crew import Crew  # noqa: PLC0415

        platform = _platform(config, cancel=cancel)
        platform.connect()
        _seed_bridge(platform, competition_id)
        store = open_store(config, run_id)
        # 多队参赛时 start_instance 不带 competition_id 会报 competition_required，
        # 先把所选比赛写进 store.meta（与 cli cmd_challenges 同口径）。
        if competition_id:
            store.meta["competition_id"] = competition_id
            store.save()
        problem = ensure_challenge_meta(platform, store, challenge_id)
        if problem:
            q.put(("error", "题目信息", problem))
            return

        # 不可玩就立刻收口：别起预算、别派专家（关闭/暂停场次里跑只会白烧 token）
        blocked = preflight_playable(platform, competition_id, challenge_id)
        if blocked:
            reason = "不可玩：" + "；".join(blocked)
            q.put(("event", "preflight", {"challenge_id": challenge_id, "note": reason}))
            q.put(("done", {
                "run_id": run_id,
                "challenge_id": challenge_id,
                "summary": {"reason": "not_playable", "submitted": False, "note": reason},
                "flag_value": "",
                "candidates": [],
                "submitted": False,
                "note": reason,
            }))
            return

        def on_event(kind, payload=None):
            q.put(("event", kind, payload or {}))

        # 实例配额只有 1：开跑前先清残留（上一题崩了没释放 → 这题报 agent_environment_limit）
        _preflight_instances(platform, challenge_id, (), on_event,
                             enabled=bool(config.extra.get("instance_reap_preflight", True)))

        # 附件先落沙盒：题面 files[] 不进模型工具面时，模型只能对着空目录猜
        _hydrate_attachments(platform, store, challenge_id, competition_id or "",
                             lambda k, p=None: on_event(k, p or {}))

        crew = Crew(config, platform, store,
                    lambda model=None: make_llm(config, model=model), on_event=on_event)
        summary = crew.solve(challenge_id, cancel_event=cancel)

        ch = store.get(challenge_id)
        flag = getattr(ch, "flag_value", "") if ch else ""
        answers = [str(v) for v in (getattr(ch, "answers_by_qid", {}) or {}).values()] if ch else []
        cands: list[str] = []
        for v in [flag] + answers:
            v = str(v).strip()
            if v and v not in cands:
                cands.append(v)
        q.put(("done", {
            "run_id": run_id,
            "challenge_id": challenge_id,
            "summary": summary,
            "flag_value": flag,
            "candidates": cands,
            "submitted": bool(summary.get("submitted")) or bool(flag),
        }))
    except Exception as exc:  # noqa: BLE001 - 任何异常都回到主线程显示
        q.put(("error", type(exc).__name__, str(exc)))


def solve_many(config, challenges: list, run_id: str, competition_id: str,
               q: queue.Queue, cancel: threading.Event, concurrency: int = 1,
               stop_after: threading.Event | None = None,
               force_ids: frozenset[str] | set[str] | None = None,
               budget_tokens: int | None = None,
               deadline: float | None = None) -> None:
    """按勾选批量解题，可并发。连接/Store 共享，每个任务自建 Platform 连接与 Crew。

    challenges: [{"id": ..., "name": ...}]，顺序即执行顺序（开了 contest_policy 时会按 EV 重排）。
    concurrency: 并发度；<=1 串行。
    cancel: 硬停止——立即掐断正在解的题。
    stop_after: 优雅停止——置位后不再开新题，等正在跑的题跑完。
    force_ids: 「重跑已解出」选中的题号——跳过 Crew 的两层已解出预检，真正重跑。
    budget_tokens: 单题 token 上限（supervisor 传自己算好的 quota；给了就不再做内部 EV 分摊）。
    deadline: 绝对时间戳（epoch 秒）——赛程结束或 supervisor 给的单题 deadline；
             与难度墙钟取先到者，到点软停本题。
    事件：("task_start") / ("task_done") / ("batch_note") / ("done", {batch:True})
    """
    try:
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait  # noqa: PLC0415

        from ..cli import ensure_challenge_meta, make_llm, open_store  # noqa: PLC0415
        from ..crew import Crew  # noqa: PLC0415

        platform0 = _platform(config)
        platform0.connect()  # 先探活一次，连不上直接报错，不起线程
        store = open_store(config, run_id)
        if competition_id:
            store.meta["competition_id"] = competition_id
            store.save()

        forced = frozenset(str(x) for x in (force_ids or ()))
        total = len(challenges)
        conc = max(1, min(int(concurrency or 1), max(1, total)))
        # 整批级预检：比赛暂停/关闭时连题都不用拉，直接全部标记「不可玩」（0 token）
        batch_block = playability_blockers(_competition_info(platform0, competition_id), None)
        if batch_block:
            reason = "不可玩：" + "；".join(batch_block)
            q.put(("batch_note", f"整批判为不可玩，直接跳过：{reason}"))
            blocked_results: list[dict] = []
            for i, item in enumerate(challenges, 1):
                cid = str(item.get("id") or "")
                q.put(("task_start", {"index": i, "total": total, "id": cid,
                                      "name": item.get("name") or ""}))
                row = {"id": cid, "ok": False, "flag": "", "note": reason, "skipped": True}
                blocked_results.append(row)
                q.put(("task_done", row))
            q.put(("done", {
                "run_id": run_id, "batch": True, "results": blocked_results,
                "stopped": False, "flag_value": "", "candidates": [], "submitted": False,
            }))
            return

        # 赛程级排序 + 按题 token 预算（taskplan）：默认只受难度 cap 约束（用户决定不限总池）。
        # supervisor 传了 budget_tokens（它自己按 EV 分摊的 quota）时跳过内部分摊，尊重上游。
        budgets: dict[str, dict] = {}
        contest_deadline = deadline
        if config.extra.get("contest_policy", True) and not budget_tokens:
            comp = _competition_info(platform0, competition_id)
            deadline_from_comp, elapsed_ratio = _contest_window(comp)
            contest_deadline = contest_deadline or deadline_from_comp
            rows: list[dict] = []
            for item in challenges:
                cid = str(item.get("id") or "")
                if not cid:
                    continue
                try:
                    # 补 points/questions/solves_count（EV 排序要用；单题最多一次读）
                    ensure_challenge_meta(platform0, store, cid)
                except Exception:  # noqa: BLE001 - 元数据缺失只影响排序，不拦解题
                    pass
                rows.append(_plan_row(store.get(cid), item))
            plans = plan_tasks(rows, pool=_policy_pool(config), elapsed_ratio=elapsed_ratio,
                               near_miss_ids=[str(r.get("id") or "") for r in rows
                                              if r.get("near_miss")])
            if plans:
                budgets = {p["id"]: p for p in plans}
                order = {p["id"]: p["order"] for p in plans}
                ranked = sorted([c for c in challenges if str(c.get("id") or "") in order],
                                key=lambda c: order[str(c.get("id"))])
                ranked += [c for c in challenges if str(c.get("id") or "") not in order]
                challenges = ranked
                brief = "；".join(f"{p['id']} {int(p['quota']) // 1000}k" for p in plans[:8])
                q.put(("batch_note", f"按 EV 排序 + 单题 token 上限：{brief}"))
                if contest_deadline:
                    left_min = max(0, int((contest_deadline - time.time()) / 60))
                    q.put(("batch_note", f"赛程剩余 {left_min} 分钟，到点自动软停"))

        # ---- 中途统计（正式赛/GUI 都能看；0=关闭，比赛场建议 300s）----
        stats_interval = float(config.extra.get("stats_interval_s") or 0)
        stats_stop = threading.Event()
        stats_log: list[dict] = []
        phase_by_cid: dict[str, str] = {}
        skipped_ids: set[str] = set()
        started_at = time.time()

        def _stats_snapshot() -> dict:
            items: list[dict] = []
            total_tokens = 0
            solved = 0
            for item in challenges:
                cid = str(item.get("id") or "")
                ch = store.get(cid)
                tokens = dict(getattr(ch, "tokens", None) or {}) if ch is not None else {}
                used = int(tokens.get("total_tokens") or 0)
                total_tokens += used
                flag = getattr(ch, "flag_value", "") if ch is not None else ""
                if flag:
                    solved += 1
                quota = int((budgets.get(cid) or {}).get("quota") or 0) or int(budget_tokens or 0)
                items.append({
                    "id": cid,
                    "tokens": used,
                    "calls": int(tokens.get("calls") or 0),
                    "quota": quota,
                    "used_ratio": round(used / quota, 3) if quota else None,
                    "phase": phase_by_cid.get(cid, ""),
                    "skipped": cid in skipped_ids,
                })
            return {
                "at": time.strftime("%H:%M:%S"),
                "elapsed_s": int(time.time() - started_at),
                "remaining_s": (max(0, int(contest_deadline - time.time()))
                                if contest_deadline else None),
                "total_tokens": total_tokens,
                "solved": solved,
                "items": items,
            }

        def _stats_loop() -> None:
            while not stats_stop.wait(stats_interval):
                snap = _stats_snapshot()
                stats_log.append(snap)
                q.put(("stats", snap))

        if stats_interval > 0:
            threading.Thread(target=_stats_loop, name="solve-stats", daemon=True).start()

        def work(item: dict, idx: int) -> dict:
            cid = str(item.get("id") or "")
            if not cid or cancel.is_set():
                return {"id": cid, "ok": False, "flag": "", "note": "已取消"}
            q.put(("task_start", {"index": idx, "total": total, "id": cid,
                                  "name": item.get("name") or ""}))
            try:
                platform = _platform(config, cancel=cancel)  # 每任务独立 MCP 连接，避免线程共享会话
                platform.connect()
                _seed_bridge(platform, competition_id)
                problem = ensure_challenge_meta(platform, store, cid)
                if problem:
                    return {"id": cid, "ok": False, "flag": "", "note": problem}

                # 单题级预检：关闭/暂停/平台已解的题不进入预算与派活（0 token 跳过）
                blocked = preflight_playable(platform, competition_id, cid)
                if blocked:
                    skipped_ids.add(cid)
                    phase_by_cid[cid] = "不可玩"
                    return {"id": cid, "ok": False, "flag": "",
                            "note": "不可玩：" + "；".join(blocked), "skipped": True}

                transient_retried: set[str] = set()

                def on_event(kind, payload=None):  # noqa: ANN001
                    data = dict(payload or {})
                    data["_cid"] = cid  # 并发时让界面能标出属于哪一题
                    phase_by_cid[cid] = str(kind)[:16]   # 中途统计用：最近事件类型=粗阶段
                    q.put(("event", kind, data))

                # 附件先落沙盒（同 solve()）：批量跑时每题各自拉自己的附件
                # 开跑前清残留实例；并发时把同批其它题全列进保护名单，绝不互相误杀
                _preflight_instances(
                    platform, cid,
                    tuple(str(x.get("id")) for x in challenges
                          if x.get("id") and str(x.get("id")) != cid),
                    on_event,
                    enabled=bool(config.extra.get("instance_reap_preflight", True)),
                )
                _hydrate_attachments(platform, store, cid, competition_id or "", on_event)

                # 按难度分配时间预算 + 按题 token 上限：软停止只作用于本题，不波及并发中的其它题。
                ch0 = store.get(cid)
                diff = str(getattr(ch0, "difficulty", "") or "")
                budget_mins = _budget_minutes(config, diff)
                quota = int((budgets.get(cid) or {}).get("quota") or 0) or int(budget_tokens or 0)
                task_config = _with_token_cap(config, quota)
                ev, budget_engaged = _arm_budget(cancel, budget_mins, deadline=contest_deadline)
                task_cancel = ev if budget_engaged else cancel
                if budget_engaged:
                    extra = f" / token 上限 {quota // 1000}k" if quota > 0 else ""
                    q.put(("batch_note", f"[{cid}] 难度 {diff or '?'} → 时间预算 "
                                         f"{budget_mins} 分钟{extra}"))

                crew = Crew(task_config, platform, store,
                            lambda model=None: make_llm(task_config, model=model), on_event=on_event)
                # supervisor 已建好本地隧道：把「照抄本地端口」作为**平台事实**放进简报可信区，
                # 否则只留在 challenge dict 里没人读（模型会把 *.internal 连不上当成题挂了）。
                tunnel_note = str(item.get("tunnel_note") or "").strip()
                if tunnel_note:
                    crew.pin_access_lines([tunnel_note])
                summary = crew.solve(cid, cancel_event=task_cancel, force_rerun=cid in forced)

                # [抄 verialabs/ctf-agent 2026-11-18] 多模型接力：首选模型未解出 → 换模型再战一轮。
                # 不配置 extra.swarm_models 时这段完全跳过（零额外成本）。
                _swarm = [str(m).strip() for m in (config.extra.get("swarm_models") or [])
                          if str(m).strip()]
                if _swarm:
                    _used_models = {str(getattr(task_config, "model", "") or "")}
                    _ok_now = bool((summary or {}).get("submitted")) or bool(
                        getattr(store.get(cid), "flag_value", ""))
                    for _model in _swarm:
                        if _ok_now or task_cancel.is_set() or not _model:
                            break
                        if _model in _used_models:
                            continue
                        _used_models.add(_model)
                        q.put(("batch_note",
                               f"[{cid}] 首选模型未解出 → 换 `{_model}` 再战（接力，共享预算）"))
                        try:
                            _cfg2 = replace(task_config, model=_model)
                            crew2 = Crew(_cfg2, platform, store,
                                         lambda model=None, _c=_cfg2: make_llm(_c, model=model),
                                         on_event=on_event)
                            summary = crew2.solve(cid, cancel_event=task_cancel, force_rerun=True)
                        except Exception as exc:  # noqa: BLE001 - 接力失败不毁主流程
                            q.put(("batch_note", f"[{cid}] 换模型 {_model} 失败：{exc}"))
                            continue
                        _ok_now = bool((summary or {}).get("submitted")) or bool(
                            getattr(store.get(cid), "flag_value", ""))
                        if _ok_now:
                            break
                ch = store.get(cid)
                flag = getattr(ch, "flag_value", "") if ch else ""
                ok = bool((summary or {}).get("submitted")) or bool(flag)
                # 「已解出」是平台/缓存给的现成结论（0 token、0 轮次），界面要能区分出「跳过」
                presolved = str((summary or {}).get("reason") or "") == "already_solved"
                note = ""
                if budget_engaged and task_cancel.is_set() and not cancel.is_set():
                    if contest_deadline and time.time() >= contest_deadline:
                        note = f"赛程时间到（{budget_mins} 分钟预算，难度 {diff or '?'}）"
                    else:
                        note = f"时间预算到（{budget_mins} 分钟，难度 {diff or '?'}）"
                if ok and not flag:
                    # 平台说已解、本地没有 flag（常见于平台侧已解但本 agent 没记到 flag）：
                    # 不能既显示「已解出」又在详情写「未解出」，要说明真实原因。
                    note = note or "平台已解出（本地无 flag 记录）"
                return {"id": cid, "ok": ok, "flag": flag, "note": note,
                        "presolved": presolved}
            except Exception as exc:  # noqa: BLE001 - 单题失败不中断批量
                # [P1 2026-11-18] 平台瞬断（Remote end closed / 连接重置）不该让整题报废：
                # 同一题隔 10s 重试一次（Linux 批次实测 baby_rop 32s 被瞬断判死，白丢一题）。
                msg = str(exc)
                if _is_transient_net(msg) and cid not in transient_retried:
                    transient_retried.add(cid)
                    q.put(("batch_note", f"[{cid}] 平台瞬断（{msg[:60]}），10s 后整题重试一次"))
                    time.sleep(10)
                    return work(item, i)
                return {"id": cid, "ok": False, "flag": "", "note": msg[:200]}

        results: list[dict] = []
        stop_after = stop_after or threading.Event()

        def stopping() -> bool:
            return cancel.is_set() or stop_after.is_set()

        if conc <= 1:
            for i, item in enumerate(challenges, 1):
                if stopping():
                    q.put(("batch_note", f"已停止：剩余 {total - i + 1} 题未跑"))
                    break
                r = work(item, i)
                results.append(r)
                q.put(("task_done", r))
        else:
            # 增量提交：只有还有空闲并发位时才开新题，优雅停止后不再补位。
            with ThreadPoolExecutor(max_workers=conc) as ex:
                pending = iter(list(enumerate(challenges, 1)))
                running: dict = {}
                while len(running) < conc:
                    try:
                        i, item = next(pending)
                    except StopIteration:
                        break
                    running[ex.submit(work, item, i)] = i
                while running:
                    finished, _ = wait(running, return_when=FIRST_COMPLETED)
                    for fut in finished:
                        running.pop(fut, None)
                        r = fut.result()
                        results.append(r)
                        q.put(("task_done", r))
                    if stopping():
                        continue
                    while len(running) < conc:
                        try:
                            i, item = next(pending)
                        except StopIteration:
                            break
                        running[ex.submit(work, item, i)] = i
            left = total - len(results)
            if left > 0:
                q.put(("batch_note", f"已停止：剩余 {left} 题未跑"))
            order = {str(c.get("id")): n for n, c in enumerate(challenges)}
            results.sort(key=lambda r: order.get(str(r.get("id")), 10**6))

        stats_stop.set()
        if stats_interval > 0:
            final_snap = _stats_snapshot()
            stats_log.append(final_snap)
            q.put(("stats", final_snap))

        flags = [r["flag"] for r in results if r.get("flag")]
        q.put(("done", {
            "run_id": run_id,
            "batch": True,
            "results": results,
            "stats": stats_log,
            "stopped": cancel.is_set() or stop_after.is_set(),
            "flag_value": flags[0] if flags else "",
            "candidates": list(dict.fromkeys(flags)),
            "submitted": any(r.get("ok") for r in results),
        }))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", type(exc).__name__, str(exc)))