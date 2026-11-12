#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨题 / 跨进程的全局 token 总账（正式赛 1B 总量）。

为什么需要
----------
题级 `max_tokens_per_challenge` 只管**单题**。正式赛的硬约束是**整队 1B token 总量**
（规则 v2.5 §二(五)）——多题、多段、最多 3 次重启容器叠加起来算。只靠题级上限，
「重跑同一题」「连做多题」「进程重启后续跑」都可能把总量顶穿而没有任何刹车。

本模块提供一个落盘的总账：
- 每次模型调用前 `check()`：总量到顶就抛 `BudgetExceeded`（kind=budget_exhausted，
  现有编排/报告链条无需改动即可正确收口）；
- 每次调用后 `record(usage)`：按**平台计费口径的原始 token**（prompt+completion，
  不减缓存折扣）累加并原子落盘；
- 进程重启/换题都从磁盘重读，因此跨进程、跨题连续。

落盘位置（优先级从高到低）：
1. `config.global_ledger_path` / 环境变量 `CTFY_GLOBAL_LEDGER`（显式指定）；
2. `$CTFY_WORK_DIR/.ctfy-global-ledger.json`（平台容器：work 目录随 run 归档）；
3. `<runs_dir 的上一级>/.ctfy-global-ledger.json`（本地：项目根下，跨 run 共享）。

上限用 `config.max_total_tokens`（0 = 不设上限，本地调试用）。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from .budget import BudgetExceeded

# 规则 v2.5 §二(五)：正式赛智谱 token 总量 1B（团队、跨全部题目与分段）。
DEFAULT_MAX_TOTAL_TOKENS = 1_000_000_000

_LEDGER_FILENAME = ".ctfy-global-ledger.json"
_SCHEMA = 1
_LOCK = threading.RLock()


def ledger_path(config) -> Path:
    """解析总账落盘路径（见模块 docstring 的三级优先级）。"""
    explicit = ""
    try:
        explicit = str((config.extra or {}).get("global_ledger_path") or "").strip()
    except AttributeError:
        explicit = ""
    explicit = explicit or os.environ.get("CTFY_GLOBAL_LEDGER", "").strip()
    if explicit:
        return Path(explicit)
    work = os.environ.get("CTFY_WORK_DIR", "").strip()
    if work:
        return Path(work) / _LEDGER_FILENAME
    runs = getattr(config, "runs_path", None)
    if runs is not None:
        return Path(runs).parent / _LEDGER_FILENAME
    return Path(_LEDGER_FILENAME)


class GlobalTokenLedger:
    """落盘的全局 token 总账。线程内串行、进程间以原子写共享（单 agent 场景足够）。"""

    def __init__(self, path: Path | str, cap: int = 0, *, run_id: str = "", label: str = ""):
        self.path = Path(path)
        self.cap = max(0, int(cap or 0))
        self.run_id = str(run_id or "")
        self.label = str(label or "")

    # ---------- 构造 ----------

    @classmethod
    def from_config(cls, config, *, run_id: str = "") -> "GlobalTokenLedger | None":
        """按配置建账；`max_total_tokens<=0` 返回 None（不启用，零开销）。"""
        cap = int(getattr(config, "max_total_tokens", 0) or 0)
        if cap <= 0:
            return None
        return cls(ledger_path(config), cap, run_id=run_id)

    # ---------- 读写 ----------

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            return data
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data["schema"] = _SCHEMA
        data["updated_at"] = int(time.time())
        data["updated_by"] = f"{os.name}/{os.getpid()}"
        # 原子写：tmp + os.replace，避免进程被杀时留下半截 JSON 把总账读成 0。
        fd, tmp = tempfile.mkstemp(prefix=".ctfy-ledger.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ---------- 查询 ----------

    def snapshot(self) -> dict:
        with _LOCK:
            data = self._read()
        return {
            "path": str(self.path),
            "cap": self.cap,
            "used": int(data.get("used_tokens") or 0),
            "calls": int(data.get("calls") or 0),
            "runs": dict(data.get("runs") or {}),
            "updated_at": int(data.get("updated_at") or 0),
        }

    def used(self) -> int:
        return self.snapshot()["used"]

    def remaining(self) -> int | None:
        """剩余总量；未设上限返回 None。"""
        if not self.cap:
            return None
        return max(0, self.cap - self.used())

    def describe(self) -> str:
        used = self.used()
        if not self.cap:
            return f"全局 token {used:,}（未设总量上限）"
        pct = min(100.0, used * 100.0 / self.cap) if self.cap else 0.0
        return f"全局 token {used:,} / {self.cap:,}（{pct:.2f}%，跨题累计）"

    # ---------- 闸门 / 记账 ----------

    def check(self) -> None:
        """总账到顶就抛 BudgetExceeded（在发请求之前，不产生费用）。"""
        if not self.cap:
            return
        snap = self.snapshot()
        if snap["used"] >= self.cap:
            raise BudgetExceeded(
                (f"{self.label + '：' if self.label else ''}"
                 f"全局 token 总账已耗尽：{snap['used']:,} / {self.cap:,}"
                 f"（跨题累计；规则 v2.5 §二(五) 1B 总量。账本 {self.path}）"),
                {"global": snap},
                kind="budget_exhausted",
            )

    def record(self, usage: dict | None) -> dict:
        """按平台计费口径累加一次调用的原始 token（prompt+completion，不减折扣）。"""
        usage = usage or {}
        prompt = max(0, int(usage.get("prompt_tokens") or 0))
        completion = max(0, int(usage.get("completion_tokens") or 0))
        delta = prompt + completion
        with _LOCK:
            data = self._read()
            data["used_tokens"] = int(data.get("used_tokens") or 0) + delta
            data["calls"] = int(data.get("calls") or 0) + 1
            data["prompt_tokens"] = int(data.get("prompt_tokens") or 0) + prompt
            data["completion_tokens"] = int(data.get("completion_tokens") or 0) + completion
            runs = data.get("runs")
            if not isinstance(runs, dict):
                runs = {}
            if self.run_id:
                runs[self.run_id] = int(runs.get(self.run_id) or 0) + delta
            data["runs"] = runs
            data["cap_seen"] = max(int(data.get("cap_seen") or 0), self.cap)
            self._write(data)
            return {
                "used": data["used_tokens"],
                "calls": data["calls"],
                "tok_delta": delta,
                "cap": self.cap,
            }


def build_global_ledger(config, *, run_id: str = "") -> GlobalTokenLedger | None:
    """给 LLM 客户端/报告用的薄封装：未配置上限时返回 None（零开销）。"""
    try:
        return GlobalTokenLedger.from_config(config, run_id=run_id)
    except Exception:  # noqa: BLE001 - 总账异常绝不能阻断解题（宁可不记账也不炸 run）
        return None