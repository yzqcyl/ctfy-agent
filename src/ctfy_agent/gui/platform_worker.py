# -*- coding: utf-8 -*-
"""平台接口模式的 GUI 工作线程：连接测试 / 拉题单 / 跑 Crew 解题。

全部复用现有 CLI 与 Crew 的公开入口（make_platform / open_store / ensure_challenge_meta /
Crew / solve），不改动这些模块；结果经 queue 回主线程。
"""
from __future__ import annotations

import queue
import threading


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

        def on_event(kind, payload=None):
            q.put(("event", kind, payload or {}))

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
               stop_after: threading.Event | None = None) -> None:
    """按勾选批量解题，可并发。连接/Store 共享，每个任务自建 Platform 连接与 Crew。

    challenges: [{"id": ..., "name": ...}]，顺序即执行顺序。
    concurrency: 并发度；<=1 串行。
    cancel: 硬停止——立即掐断正在解的题。
    stop_after: 优雅停止——置位后不再开新题，等正在跑的题跑完。
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

        total = len(challenges)
        conc = max(1, min(int(concurrency or 1), max(1, total)))

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

                def on_event(kind, payload=None):  # noqa: ANN001
                    data = dict(payload or {})
                    data["_cid"] = cid  # 并发时让界面能标出属于哪一题
                    q.put(("event", kind, data))

                crew = Crew(config, platform, store,
                            lambda model=None: make_llm(config, model=model), on_event=on_event)
                summary = crew.solve(cid, cancel_event=cancel)
                ch = store.get(cid)
                flag = getattr(ch, "flag_value", "") if ch else ""
                ok = bool((summary or {}).get("submitted")) or bool(flag)
                return {"id": cid, "ok": ok, "flag": flag, "note": ""}
            except Exception as exc:  # noqa: BLE001 - 单题失败不中断批量
                return {"id": cid, "ok": False, "flag": "", "note": str(exc)[:200]}

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

        flags = [r["flag"] for r in results if r.get("flag")]
        q.put(("done", {
            "run_id": run_id,
            "batch": True,
            "results": results,
            "stopped": cancel.is_set() or stop_after.is_set(),
            "flag_value": flags[0] if flags else "",
            "candidates": list(dict.fromkeys(flags)),
            "submitted": any(r.get("ok") for r in results),
        }))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", type(exc).__name__, str(exc)))