# -*- coding: utf-8 -*-
"""跨 run 的平台提交账本（P17 / P0-5）。

为什么需要：平台按「用户+题」全局累计提交次数，而本地 `state.json` 是 per-run 的 —— 每换一个
run 目录（批次/快照重启）额度记忆就清零。实测后果：
  * `cuteRE` 被交到平台 `attempt=6`（本地每轮都以为还剩 5 次）；
  * `菜刀流量` 的 `NPF_{...}` 已被平台判错，下一个 run 又原样交了一次；
  * `vigenere` / `Code_Interpreter` 已经被打空（attempt=5/6），仍在批次里继续跑。

本模块把「已用次数 + 已判错值 + 已收值」落到一个**跨 run 的 JSON 账本**里，提交前查、提交后记。

路径：环境变量 `CTFY_SUBMIT_LEDGER` 优先（批量跑时指到项目级账本，所有快照共享）；
      否则用 `run_dir` 父目录下的 `submit_ledger.json`（镜像里各 run 共享同一 runs/）。

键：`challenge_id`（题级兜底）或 `challenge_id::question_id`（P0-5 审计：CTFY 的额度是
**按题号**的，多问题题不能被某个题号烧掉的额度锁死）。带题号时读「题号条目 ∪ 题级条目」，
写题号条目；不带题号时只读写题级条目（老数据兼容）。

并发：多 worker 同时写同一文件 → 唯一 tmp + os.replace 原子替换。note_* 写前重读并做
字段级合并（attempts 取 max、集合取并集），避免「晚写覆盖早写」把 attempts=10 盖回 3。
修复（repair）：只修「值归属」（accepted 里的值从 rejected 去掉、schema/去重），**不动
attempts**——attempts 是平台口径，本地猜不得。
"""
from __future__ import annotations

import datetime as dt
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


def _empty() -> dict:
    return {"schema": SCHEMA, "challenges": {}}


def load(path: Path) -> dict:
    """读账本。文件不存在 → 空账本；**损坏** → 先把原文件改名 `.corrupt.<ts>` 保住证据再返回空账本。"""
    path = Path(path)
    if not path.is_file():
        return _empty()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("challenges"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    # 损坏：不静默覆盖历史。把坏文件改名留证，后续写入不会丢原始内容。
    try:
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        path.replace(path.with_name(f"{path.name}.corrupt.{stamp}"))
    except OSError:
        pass
    return _empty()


def _save(path: Path, data: dict, *, merge: bool = False) -> None:
    """原子写。merge=True 时先重读磁盘，做字段级合并（防并发晚写覆盖）。"""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if merge:
            data = _merge(load(path), data)
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


def _merge(base: dict, new: dict) -> dict:
    """把 new 的字段并进 base：attempts 取 max，rejected/accepted 取并集（保序）。"""
    if not isinstance(base, dict) or not isinstance(base.get("challenges"), dict):
        return new
    merged = json.loads(json.dumps(base, ensure_ascii=False))
    for key, entry in (new.get("challenges") or {}).items():
        if not isinstance(entry, dict):
            continue
        current = merged["challenges"].get(key)
        if not isinstance(current, dict):
            merged["challenges"][key] = entry
            continue
        try:
            current["attempts"] = max(int(current.get("attempts") or 0),
                                      int(entry.get("attempts") or 0))
        except (TypeError, ValueError):
            current["attempts"] = int(entry.get("attempts") or 0)
        for field in ("rejected", "accepted"):
            values = [str(v) for v in (current.get(field) or [])]
            for value in entry.get(field) or []:
                if str(value) not in values:
                    values.append(str(value))
            current[field] = values
    return merged


def _key(challenge_id: str, question_id: str = "") -> str:
    cid = str(challenge_id or "").strip()
    qid = str(question_id or "").strip()
    return f"{cid}::{qid}" if cid and qid else cid


def _entry(data: dict, challenge_id: str, question_id: str = "") -> dict:
    items = data.setdefault("challenges", {})
    key = _key(challenge_id, question_id)
    entry = items.get(key)
    if not isinstance(entry, dict):
        entry = {}
        items[key] = entry
    entry.setdefault("attempts", 0)
    entry.setdefault("rejected", [])
    entry.setdefault("accepted", [])
    return entry


def attempts(data: dict, challenge_id: str, question_id: str = "") -> int:
    """已用提交次数：带题号时取「题号条目 ∪ 题级条目」的最大值（老数据兜底）。"""
    best = 0
    for key in (_key(challenge_id, question_id), _key(challenge_id)):
        try:
            best = max(best, int((data.get("challenges", {}).get(key) or {}).get("attempts") or 0))
        except (TypeError, ValueError):
            continue
    return best


def note_attempt(path: Path, challenge_id: str, attempt: int, question_id: str = "") -> int:
    """记录平台回执里的 attempt（单调取 max），返回账本里的已用次数。"""
    if not challenge_id:
        return 0
    try:
        value = max(0, int(attempt))
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return attempts(load(path), challenge_id, question_id)
    data = load(path)
    entry = _entry(data, challenge_id, question_id)
    before = int(entry.get("attempts") or 0)
    if value > before:
        entry["attempts"] = value
        _save(path, data, merge=True)
    return max(before, value)


def _values(data: dict, challenge_id: str, field: str, question_id: str = "") -> list[str]:
    values: list[str] = []
    for key in (_key(challenge_id, question_id), _key(challenge_id)):
        for value in (data.get("challenges", {}).get(key) or {}).get(field) or []:
            text = str(value or "").strip()
            if text and text not in values:
                values.append(text)
    return values


def is_rejected(data: dict, challenge_id: str, answer: str, question_id: str = "") -> bool:
    want = (answer or "").strip()
    if not want:
        return False
    return want in _values(data, challenge_id, "rejected", question_id)


def is_accepted(data: dict, challenge_id: str, answer: str, question_id: str = "") -> bool:
    """这个值平台是否已经收过（已收就别重复交，省次数也防平台判重复）。"""
    want = (answer or "").strip()
    if not want:
        return False
    return want in _values(data, challenge_id, "accepted", question_id)


def _note_value(path: Path, challenge_id: str, answer: str, field: str,
                question_id: str = "") -> None:
    want = (answer or "").strip()
    if not want or not challenge_id:
        return
    data = load(path)
    entry = _entry(data, challenge_id, question_id)
    values = [str(v) for v in (entry.get(field) or [])]
    changed = False
    if want not in values:
        values.append(want)
        entry[field] = values
        changed = True
    if field == "accepted" and want in [str(v).strip() for v in entry.get("rejected") or []]:
        # 平台已收的值不再算「判错」（赛时事故：同一值同时进两个集合）
        entry["rejected"] = [v for v in entry.get("rejected") or [] if str(v).strip() != want]
        changed = True
    if changed:
        _save(path, data, merge=True)


def note_rejected(path: Path, challenge_id: str, answer: str, question_id: str = "") -> None:
    _note_value(path, challenge_id, answer, "rejected", question_id)


def note_accepted(path: Path, challenge_id: str, answer: str, question_id: str = "") -> None:
    _note_value(path, challenge_id, answer, "accepted", question_id)


def note_exhausted(path: Path, challenge_id: str, cap: int, question_id: str = "") -> int:
    """平台明确说本题提交次数已用尽（question_attempt_cap_exceeded / remaining=0 且 cap>0）。

    把账本 attempts 抬到 cap，之后本地拦；**cap<=0（平台不限/未知）不写**，避免把不限额题
    凭空写成 10/10 永久拦死（P0-5 审计 Q2-3）。
    """
    if not challenge_id:
        return 0
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return attempts(load(path), challenge_id, question_id)
    data = load(path)
    entry = _entry(data, challenge_id, question_id)
    before = int(entry.get("attempts") or 0)
    if cap > before:
        entry["attempts"] = cap
        _save(path, data, merge=True)
    return max(before, cap)


def remaining(data: dict, challenge_id: str, cap: int, question_id: str = "") -> int:
    """还剩几次提交额度（cap<=0 表示平台不限/未知 → -1）。"""
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        cap = 0
    if cap <= 0:
        return -1
    return max(0, cap - attempts(data, challenge_id, question_id))


def repair(path: Path, *, backup: bool = True) -> dict:
    """修复账本（幂等）：只修「值归属」，**不动 attempts**（attempts 是平台口径，本地猜不得）。

    规则：
      1. 同一值既在 accepted 又在 rejected → 从 rejected 删掉（平台实际已收）；
      2. accepted/rejected 去重、去空、去非字符串；
      3. 修 schema（缺 challenges/rejected/accepted/attempts → 补齐）；
      4. 损坏到无法解析 → load() 已把原文件改名 `.corrupt.<ts>`，这里重建成空账本（changed=True）。
    返回 {"changed": bool, "fixed": {...}, "corrupt": bool}；
    备份写 `<path>.bak.<时间戳>`（backup=True 且确有改动时；多次 repair 不互相覆盖证据）。
    """
    path = Path(path)
    corrupt = False
    if not path.is_file():
        return {"changed": False, "fixed": {}, "corrupt": False}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        data = raw if isinstance(raw, dict) and isinstance(raw.get("challenges"), dict) else None
    except (OSError, json.JSONDecodeError):
        data = None
    if data is None:
        corrupt = True
        # load() 会把坏文件改名为 .corrupt.<ts>；若改不动就保留原文件并放弃写入
        load(path)
        if path.is_file():
            return {"changed": False, "fixed": {}, "corrupt": True}
        data = _empty()
    fixed: dict[str, dict] = {}
    changed = corrupt
    challenges = data.get("challenges")
    for cid, entry in list(challenges.items()):
        if not isinstance(entry, dict):
            challenges[cid] = {"attempts": 0, "rejected": [], "accepted": []}
            fixed[str(cid)] = {"recreated": True}
            changed = True
            continue
        before_json = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        accepted = [str(v).strip() for v in (entry.get("accepted") or [])
                    if str(v or "").strip()]
        accepted = list(dict.fromkeys(accepted))
        accepted_set = set(accepted)
        rejected = [str(v).strip() for v in (entry.get("rejected") or [])
                    if str(v or "").strip()]
        clean_rejected: list[str] = []
        dropped: list[str] = []
        for value in rejected:
            # 大小写敏感（flag 本身大小写敏感）：与 is_rejected 保持同一套比较口径
            if value in accepted_set:
                dropped.append(value)
                continue
            if value not in clean_rejected:
                clean_rejected.append(value)
        try:
            attempts_value = int(entry.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts_value = 0
        entry.update({"attempts": attempts_value, "rejected": clean_rejected,
                      "accepted": accepted})
        if json.dumps(entry, ensure_ascii=False, sort_keys=True) != before_json:
            changed = True
            fixed[str(cid)] = {"dropped_rejected": dropped, "attempts": attempts_value}
    if changed:
        if backup:
            try:
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                target = path.with_name(f"{path.name}.bak.{stamp}")
                if not target.exists():
                    target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass
        _save(path, data)
    return {"changed": changed, "fixed": fixed, "corrupt": corrupt}


def blocked_reason(data: dict, challenge_id: str, answer: str, cap: int,
                   question_id: str = "") -> str:
    """提交前的确定性拦截理由（空串=放行）。"""
    used = attempts(data, challenge_id, question_id)
    if cap > 0 and used >= cap:
        scope = f"题号 `{question_id}`" if question_id else "本题"
        return (
            f"[提交被拦截] 跨 run 账本显示{scope}平台已累计 {used} 次提交（上限 {cap}，"
            "按题号全局计数、跨 run 不重置）。再交不会被受理——不要再换值试探，"
            "直接复盘收口（把候选与依据写进 report）。"
        )
    if is_rejected(data, challenge_id, answer, question_id):
        return (
            "[提交被拦截] 这个答案平台判错（跨 run 账本在案，已判错过同一个值），"
            "再交同一个值结果不会变。换一个**有可复现执行证据**的新候选，或直接交付卡点；"
            "不要只改一两个字符。"
        )
    return ""