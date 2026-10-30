# -*- coding: utf-8 -*-
"""集群-A × Crew（无状态题同题并行）离线验收：不联网、不真机并发，桩掉 Crew.solve 验编排层。

审查修正（2026-11 二轮）后的覆盖点：
  1. 触发与分发：crypto 并行 3 路、温度 0.2/0.5/0.8、每路预分配切片均摊；
  2. 先到先赢（solved 路作 base）+ 黑板合并去重；
  3. 用量合并记账：state.tokens = Σ 各路用量（不再「最后写者胜」低计），summary.usage 同口径；
  4. 提交状态回传主 store：winning lane 的 flag_value/submitted_ok 并入主 store，
     且慢路无锁 save 洗掉磁盘 flag 后，收口权威 save 能治愈（answers 不丢）；
  5. solve_attempts 归因落盘（每路一条，按 lane 序）；
  6. 并发钳制 ≤3（平台 QA#26）；非法配置值兜底串行不炸；
  7. 预检短路：已解出不烧任何路；未解出 → 主 store status=stalled；
  8. 零回归：web 题 / 未配置 / 非法值 → 串行单次 solve、summary 无 cluster_a 段。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path


def _ok(label: str) -> None:
    """复用 smoke.py 的 ok()，让本用例计入 PASSED 总数；单独运行时退化成 print。"""
    main = sys.modules.get("__main__")
    reporter = getattr(main, "ok", None)
    if callable(reporter):
        reporter(label)
    else:
        print(f"PASS {label}")


def _mk(tmp: Path, **extra):
    from ctfy_agent.config import load as load_config
    from ctfy_agent.crew import Crew
    from ctfy_agent.state import Store

    config = load_config()
    config.mcp_url = "http://127.0.0.1:9/mcp"
    config.token = "pf_test_token"
    config.runs_dir = str(tmp / "clustera")
    config.max_tokens_per_challenge = 511_000
    config.max_llm_calls = 80
    config.extra.update(extra)

    store = Store(tmp / "clustera").load()
    store.challenges.clear()
    store.upsert({"id": "crypto-01", "name": "RSA Sign", "category": "crypto"})
    store.upsert({"id": "web-01", "name": "Login Bypass", "category": "web"})
    store.save()

    class StubPlatform:
        @staticmethod
        def my_solved_ids():
            return []

        @staticmethod
        def tool_names():
            return []

        @staticmethod
        def openai_tools_for(names):
            return []

    crew = Crew(config, StubPlatform(), store, lambda model=None: None)
    return crew, store, config


def case_cluster_a(tmp: Path) -> None:
    from ctfy_agent.crew import Crew
    from ctfy_agent.state import Store

    calls: list[dict] = []
    lock = threading.Lock()

    def _fake(solve_idx: int, clobber: bool = False, usage=(100, 50, 10), lanes: int = 3):
        """solve 桩：solve_idx=第几个到达的路解出；clobber=慢路整体 save 洗磁盘。

        并发确定性：lane() 入口有 `if cancel.is_set(): 判 superseded 零用量` 的软取消检查，
        若解出路先返回并置位 cancel，**迟到的 lane 线程会在入口被挡掉**，用量少并一路
        （实测全套跑偶发 300/20 vs 期望 450/30，单跑必过 —— 竞态在测试对线程调度的依赖）。
        这里加一道栅栏：每路进桩即计数，解出路等所有 `lanes` 路都进桩后才返回，
        确保没有路在入口被 cancel 挡掉；顺序（谁先解出/clobber 更慢）仍由原有 sleep 控制。
        """
        gate = {"n": 0}
        gate_cv = threading.Condition()

        def fake_solve(self, challenge_id, *, attempt_caps=None, prior_text="", temperature=None,
                       keep_instance=None, cancel_event=None):
            init_calls = int(self.store.require(challenge_id).tokens.get("calls") or 0)
            with lock:
                i = len(calls)
                calls.append({"caps": dict(attempt_caps or {}), "temperature": temperature,
                              "cancel_event": cancel_event})
            with gate_cv:
                gate["n"] += 1
                gate_cv.notify_all()
                if not gate_cv.wait_for(lambda: gate["n"] >= lanes, timeout=15):
                    raise RuntimeError("lane 栅栏超时：%d/%d 路未进桩" % (gate["n"], lanes))
            time.sleep(0.05)  # 栅栏后留一点错开，保证「先到先赢」顺序稳定
            pt, ct, ncalls = usage
            if i == solve_idx:
                ch = self.store.require(challenge_id)  # 本路内存视图：模拟 mark_submitted 落状态
                ch.flag_value = "flag{won}"
                ch.submitted_ok = True
                ch.status = "submitted"
                self.store.save()
                return {"challenge_id": challenge_id, "submitted": True, "reason": "flag_accepted",
                        "steps": 5, "tokens_spent": pt + ct,
                        "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
                        "budget": {"calls": init_calls + ncalls, "total_tokens": pt + ct},
                        "facts": ["flag 在 output.txt"], "dead_ends": [], "candidates": ["flag{a1b2}"]}
            if clobber:
                time.sleep(0.6)  # 比解出路慢：整体无锁 save，把磁盘上刚写的 flag 洗掉
                try:
                    self.store.save()  # 并发下偶发锁/权限异常：洗盘是测试意图，失败不该让该路 usage 归零
                except Exception:  # noqa: BLE001
                    pass
            return {"challenge_id": challenge_id, "submitted": False, "reason": "step_limit",
                    "steps": 3, "tokens_spent": pt + ct,
                    "usage": {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct},
                    "budget": {"calls": init_calls + ncalls, "total_tokens": pt + ct},
                    "facts": ["RSA e=65537"], "dead_ends": ["盲试小素数无效"], "candidates": ["flag{a1b2}"]}

        return fake_solve

    def _run(crew, cid, fake):
        orig = Crew.solve  # 存引用再还原（禁止 del Crew.solve：会把被替换的方法整个删掉）
        Crew.solve = fake
        try:
            return crew.solve_clusterb(cid)
        finally:
            Crew.solve = orig

    # ---------- 场景 1：3 路并行主流程（含磁盘脏写治愈） ----------
    crew, store, _cfg = _mk(tmp / "main", cluster_a_concurrency=3)
    summary = _run(crew, "crypto-01", _fake(solve_idx=1, clobber=True))
    ch = store.require("crypto-01")

    assert len(calls) == 3, calls
    assert sorted(c["temperature"] for c in calls) == [0.2, 0.5, 0.8], calls
    _ok("clusterA: 3 路分发 + 温度差异化 0.2/0.5/0.8")
    assert all(c.get("cancel_event") is not None for c in calls), calls
    _ok("clusterA: cancel_event 已传入各路 solve（真取消信号在途）")
    assert all(c["caps"] == {"tokens": 170_333, "calls": 26} for c in calls), calls
    _ok("clusterA: 每路预分配切片 170333/26 均摊")
    assert summary.get("submitted") is True and summary.get("reason") == "flag_accepted", summary
    assert summary["cluster_a"]["n"] == 3 and summary["cluster_a"]["solved"] is True, summary
    _ok("clusterA: 先到先赢选 solved 路 + cluster_a 段")
    assert "RSA e=65537" in summary["facts"] and "flag 在 output.txt" in summary["facts"], summary
    assert summary["candidates"] == ["flag{a1b2}"], summary
    _ok("clusterA: 黑板合并去重")
    assert ch.tokens.get("total_tokens") == 450 and ch.tokens.get("calls") == 30, ch.tokens
    assert summary["usage"]["prompt_tokens"] == 300 and summary["tokens_spent"] == 450, summary
    _ok("clusterA: 用量合并记账（Σ 各路，非最后写者胜）")
    assert ch.flag_value == "flag{won}" and ch.submitted_ok is True and ch.status == "submitted", ch
    _ok("clusterA: 提交状态回传主 store（answers 不丢）")
    disk = Store(store.run_dir).load().get("crypto-01")
    assert disk is not None and disk.flag_value == "flag{won}", disk
    _ok("clusterA: 磁盘脏写被收口权威 save 治愈")
    recs = ch.solve_attempts
    assert len(recs) == 3, recs
    assert [r.get("index") for r in recs] == [0, 1, 2], recs
    assert [r.get("temperature") for r in recs] == [0.2, 0.5, 0.8], recs
    assert recs[1].get("tokens_spent") == 150 and recs[1].get("calls_spent") == 10, recs[1]
    _ok("clusterA: solve_attempts 归因 3 条按 lane 序")

    # ---------- 场景 2：并发钳制 ≤3（QA#26） ----------
    calls.clear()
    crew2, _s2, _c2 = _mk(tmp / "clamp", cluster_a_concurrency=7)
    _run(crew2, "crypto-01", _fake(solve_idx=-1))
    assert len(calls) == 3, calls
    _ok("clusterA: 并发钳制 ≤3（配置 7 → 3 路）")

    # ---------- 场景 3：预检短路（已解出不烧路） ----------
    calls.clear()
    crew3, store3, _c3 = _mk(tmp / "pre", cluster_a_concurrency=3)
    ch3 = store3.require("crypto-01")
    ch3.submitted_ok = True
    ch3.flag_value = "flag{pre}"
    summary3 = _run(crew3, "crypto-01", _fake(solve_idx=-1))
    assert len(calls) == 0, calls
    assert summary3.get("reason") == "already_solved" and summary3.get("submitted") is True, summary3
    assert summary3["cluster_a"].get("precheck") is True, summary3
    _ok("clusterA: 预检短路不烧任何路")

    # ---------- 场景 4：未解出 → 主 store stalled ----------
    calls.clear()
    crew4, store4, _c4 = _mk(tmp / "stall", cluster_a_concurrency=3)
    summary4 = _run(crew4, "crypto-01", _fake(solve_idx=-1))
    assert store4.require("crypto-01").status == "stalled"
    assert summary4["cluster_a"]["solved"] is False and summary4.get("reason") == "step_limit", summary4
    _ok("clusterA: 未解出 → 主 store stalled")

    # ---------- 场景 6：续跑（init>0）合并口径 — usage=净增量 / budget=累计 ----------
    calls.clear()
    crew8, store8, _c8 = _mk(tmp / "resume", cluster_a_concurrency=3)
    seed = store8.require("crypto-01")
    seed.tokens = {"prompt_tokens": 50, "completion_tokens": 30, "total_tokens": 80, "calls": 7}
    store8.save()
    _run(crew8, "crypto-01", _fake(solve_idx=-1))
    ch8 = store8.require("crypto-01")
    assert ch8.tokens.get("total_tokens") == 530, ch8.tokens  # 80 + 3×150 净增量
    assert ch8.tokens.get("prompt_tokens") == 350 and ch8.tokens.get("completion_tokens") == 180, ch8.tokens
    assert ch8.tokens.get("calls") == 37, ch8.tokens  # 7 + 3×10 净增量
    _ok("clusterA: 续跑（init>0）用量合并口径正确（usage 净增量 / budget 累计）")

    # ---------- 场景 5：零回归（web / 未配置 / 非法值 → 串行） ----------
    calls.clear()
    crew5, _s5, _c5 = _mk(tmp / "reg", cluster_a_concurrency=3)
    summary5 = _run(crew5, "web-01", _fake(solve_idx=-1, lanes=1))
    assert len(calls) == 1 and calls[0]["caps"] == {} and calls[0]["temperature"] is None, calls
    assert "cluster_a" not in summary5 and "clusterb" not in summary5, summary5
    _ok("clusterA: 零回归 web 题走串行（1 次、无切片、无 cluster_a 段）")

    calls.clear()
    crew6, _s6, _c6 = _mk(tmp / "reg2")
    summary6 = _run(crew6, "crypto-01", _fake(solve_idx=-1, lanes=1))
    assert len(calls) == 1 and "cluster_a" not in summary6, (calls, summary6)
    crew7, _s7, _c7 = _mk(tmp / "reg3", cluster_a_concurrency="auto")
    # "auto" 不是有效并发值 → n_a=0 走串行；2 次调用来自集群-B 的两次串行 attempt，
    # 因此栅栏按 1 路（串行）计，不能等第 2 次（第 1 次不返回它就不会开始）。
    summary7 = _run(crew7, "crypto-01", _fake(solve_idx=-1, lanes=1))
    assert len(calls) == 2 and "cluster_a" not in summary7, (calls, summary7)
    _ok("clusterA: 未配置/非法值兜底串行不炸")
