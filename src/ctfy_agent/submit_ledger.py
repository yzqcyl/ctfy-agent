# -*- coding: utf-8 -*-
"""跨 run 的平台提交账本（P17）。

为什么需要：平台按「用户+题」全局累计提交次数（上限 5，跨 run 不重置），而本地 `state.json`
是 per-run 的 —— 每换一个 run 目录（批次/快照重启）额度记忆就清零。实测后果：
  * `cuteRE` 被交到平台 `attempt=6`（本地每轮都以为还剩 5 次）；
  * `菜刀流量` 的 `NPF_{...}` 已被平台判错，下一个 run 又原样交了一次；
  * `vigenere` / `Code_Interpreter` 已经被打空（attempt=5/6），仍在批次里继续跑。
本模块把「已用次数 + 已判错值」落到一个**跨 run 的 JSON 账本**里，提交前查、提交后记。

路径：环境变量 `CTFY_SUBMIT_LEDGER` 优先（批量跑时指到项目级账本，所有快照共享）；
      否则用 `run_dir` 父目录下的 `submit_ledger.json`（镜像里各 run 共享同一 runs/）。
并发：多 worker 同时写同一文件 → 唯一 tmp + os.replace 原子替换，字段单调取 max / 集合取并集，
      不需要文件锁（最坏情况丢一次并发更新，下一轮会补上）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ENV_PATH = "CTFY_SUBMIT_LEDGER"
FILE_NAME = "submit_ledger.json"
SCHEMA = 1


def ledger_path(run_dir: Path | str | None) -> Path:
    env = (os.environ.get(ENV_PATH) or "").strip()
    if env:
        return Path(env)
    if run_dir is None:
        return Path.cwd() / FILE_NAME
    return Path(run_dir).parent / FILE_NAME


def load(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("challenges"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"schema": SCHEMA, "challenges": {}}


def _save(path: Path, data: dict) -> None:
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".submit_ledger.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        # 账本是「省钱项」：写失败不能让提交主流程崩掉
        pass


def _entry(data: dict, challenge_id: str) -> dict:
    items = data.setdefault("challenges", {})
    entry = items.get(challenge_id)
    if not isinstance(entry, dict):
        entry = {}
        items[challenge_id] = entry
    entry.setdefault("attempts", 0)
    entry.setdefault("rejected", [])
    entry.setdefault("accepted", [])
    return entry


def attempts(data: dict, challenge_id: str) -> int:
    try:
        return int((data.get("challenges", {}).get(challenge_id) or {}).get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


def note_attempt(path: Path, challenge_id: str, attempt: int) -> int:
    """记录平台回执里的 attempt（单调取 max），返回账本里的已用次数。"""
    if not challenge_id:
        return 0
    data = load(path)
    entry = _entry(data, challenge_id)
    try:
        value = int(attempt)
    except (TypeError, ValueError):
        value = 0
    before = int(entry.get("attempts") or 0)
    if value > before:
        entry["attempts"] = value
        _save(path, data)
    return max(before, value)


def is_rejected(data: dict, challenge_id: str, answer: str) -> bool:
    want = (answer or "").strip()
    if not want:
        return False
    values = (data.get("challenges", {}).get(challenge_id) or {}).get("rejected") or []
    return any(str(v).strip() == want for v in values)


def note_rejected(path: Path, challenge_id: str, answer: str) -> None:
    want = (answer or "").strip()
    if not want or not challenge_id:
        return
    data = load(path)
    entry = _entry(data, challenge_id)
    values = [str(v) for v in (entry.get("rejected") or [])]
    if want not in values:
        values.append(want)
        entry["rejected"] = values
        _save(path, data)


def note_accepted(path: Path, challenge_id: str, answer: str) -> None:
    want = (answer or "").strip()
    if not want or not challenge_id:
        return
    data = load(path)
    entry = _entry(data, challenge_id)
    values = [str(v) for v in (entry.get("accepted") or [])]
    if want not in values:
        values.append(want)
        entry["accepted"] = values
        _save(path, data)


def blocked_reason(data: dict, challenge_id: str, answer: str, cap: int) -> str:
    """提交前的确定性拦截理由（空串=放行）。"""
    used = attempts(data, challenge_id)
    if cap > 0 and used >= cap:
        return (
            f"[提交被拦截] 跨 run 账本显示本题平台已累计 {used} 次提交（上限 {cap}，"
            "「用户+题」全局计数、跨 run 不重置）。再交不会被受理——不要再换值试探，"
            "直接复盘收口（把候选与依据写进 report）。"
        )
    if is_rejected(data, challenge_id, answer):
        return (
            "[提交被拦截] 这个答案平台判错（跨 run 账本在案，已判错过同一个值），"
            "再交同一个值结果不会变。换一个**有可复现执行证据**的新候选，或直接交付卡点；"
            "不要只改一两个字符。"
        )
    return ""