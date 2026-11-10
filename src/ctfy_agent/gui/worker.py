# -*- coding: utf-8 -*-
"""GUI 工作线程：后台跑离线解题引擎，事件经 queue 推给 Tk 主线程（主线程绝不阻塞）。"""
from __future__ import annotations

import queue
import threading

from ..hunt import resume_run, run_hunt


def start_hunt(config, spec: dict, q: queue.Queue, cancel: threading.Event) -> None:
    """spec = {urls, category, difficulty, name, brief, run_id}。以 ("done", result) 入队。"""
    def on_event(kind, payload=None):
        q.put(("event", kind, payload or {}))

    def on_answer(cid, answer):
        q.put(("answer", cid, answer))

    try:
        result = run_hunt(config, on_event=on_event, on_answer=on_answer,
                          cancel_event=cancel, **spec)
        q.put(("done", result))
    except Exception as exc:  # noqa: BLE001 - 任何异常都要回到主线程显示
        q.put(("error", type(exc).__name__, str(exc)))


def resume(config, run_id: str, q: queue.Queue, cancel: threading.Event) -> None:
    def on_event(kind, payload=None):
        q.put(("event", kind, payload or {}))

    def on_answer(cid, answer):
        q.put(("answer", cid, answer))

    try:
        result = resume_run(config, run_id=run_id, on_event=on_event,
                            on_answer=on_answer, cancel_event=cancel)
        q.put(("done", result))
    except Exception as exc:  # noqa: BLE001
        q.put(("error", type(exc).__name__, str(exc)))