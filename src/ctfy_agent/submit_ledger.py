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
import contextlib
import json
import os
import tempfile
import time
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


@contextlib.contextmanager
def _file_lock(path: Path):
    """跨进程排他锁（Windows msvcrt / POSIX fcntl），保护 read-modify-replace。

    没有它时「读旧账本 → 合并 → 写回」在并发下会互相覆盖：实测 20 线程写
    attempts 1..20，15/15 轮只落到 1..16（P0-5 三审 P1-3）。锁文件与账本同目录。
    """
    lock_path = Path(str(path) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
    except OSError:
        yield                      # 实在建不了锁文件：退化为无锁（不能因锁炸主流程）
        return
    try:
        if os.name == "nt":
            import msvcrt  # noqa: PLC0415

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl  # noqa: PLC0415

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt  # noqa: PLC0415

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl  # noqa: PLC0415

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
    finally:
        try:
            handle.close()
        except OSError:
            pass


def load_strict(path: Path) -> dict | None:
    """读账本；**读不到（OSError）返回 None**，与「文件真不存在」区分开。

    `_save(merge=True)` 必须用它：读失败时返回空账本再去 merge 会把好账本整个覆盖掉
    （P0-5 三审 P2-8 实测老条目全丢）。
    """
    path = Path(path)
    if not path.is_file():
        return _empty()
    for attempt in range(5):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            time.sleep(0.01 * (attempt + 1))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict) and isinstance(data.get("challenges"), dict):
            return data
        return None
    return None


def load(path: Path) -> dict:
    """读账本。

    * 文件不存在 → 空账本；
    * OSError（Windows 下读写同文件/杀软扫描的共享冲突）→ 退避重试，**绝不**当损坏；
      重试仍失败才返回空账本（但不改名，避免把好账本误标成 .corrupt）；
    * JSON 解析失败/结构不对 → 才把原文件改名 `.corrupt.<ts>` 保证据，返回空账本。
    """
    path = Path(path)
    if not path.is_file():
        return _empty()
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:      # 共享冲突：重试，别当损坏
            last_error = exc
            time.sleep(0.01 * (attempt + 1))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            break                   # 真损坏：走下面的留证逻辑
        if isinstance(data, dict) and isinstance(data.get("challenges"), dict):
            return data
        break                       # 结构不对：按损坏处理
    else:
        # 连续 OSError：返回空账本但**不改名**（下次读可能就好了）
        return _empty()
    if last_error is not None:
        return _empty()
    # 损坏：不静默覆盖历史。把坏文件改名留证，后续写入不会丢原始内容。
    for attempt in range(5):
        try:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            path.replace(path.with_name(f"{path.name}.corrupt.{stamp}"))
            break
        except OSError:
            time.sleep(0.01 * (attempt + 1))
    return _empty()


def _save(path: Path, data: dict, *, merge: bool = False) -> bool:
    """原子写。merge=True 时在**文件锁内**重读磁盘做字段级合并（防并发晚写覆盖）。

    返回 False 表示「没能安全写入」（读失败/OSError 最终失败），调用方不应假设已落盘。
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _file_lock(path):
            return _save_locked(path, data, merge=merge)
    except OSError:
        return False


def _save_locked(path: Path, data: dict, *, merge: bool = False) -> bool:
    try:
        if merge:
            disk = load_strict(path)
            if disk is None:
                # 读不到就绝不 merge 覆盖（否则空账本会把好账本抹掉）
                return False
            data = _merge(disk, data)
        fd, tmp = tempfile.mkstemp(prefix=".submit_ledger.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
                fh.write("\n")
            # Windows 上目标被读者/杀软短暂持有 → os.replace PermissionError/共享冲突；
            # 退避重试几次，仍失败才放弃（账本是省钱项，不能炸主流程，但更不能默默丢写）。
            for attempt in range(6):
                try:
                    os.replace(tmp, path)
                    return True
                except OSError:
                    if attempt == 5:
                        raise
                    time.sleep(0.01 * (attempt + 1))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        return False


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
        # note_accepted 的「从 rejected 删掉」不能被并集又加回来
        accepted_set = {str(v) for v in (current.get("accepted") or [])}
        current["rejected"] = [v for v in (current.get("rejected") or [])
                               if str(v) not in accepted_set]
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
    """已用提交次数。

    带题号时**只认题号条目**（有的话）；没有题号条目才退回题级条目（老账本兜底）。
    不能用 max(题号, 题级)：老账本题级的 attempts=10 会把新题号一上来就拦死，且
    repair 不动 attempts，救不回来（P0-5 三审 P2）。
    """
    challenges = data.get("challenges", {}) or {}
    scoped = challenges.get(_key(challenge_id, question_id)) if question_id else None
    if isinstance(scoped, dict):
        try:
            return int(scoped.get("attempts") or 0)
        except (TypeError, ValueError):
            return 0
    if question_id and str(question_id).strip().lower() != "flag":
        # 题号作用域：老账本的题级 attempts 只代表 "flag" 那一问，不能拦新题号首交
        # （否则 q1 打满 → q2 永远拦、又永远不产生 q2 条目 → 死锁，P0-5 三审 P1-4）
        return 0
    try:
        return int((challenges.get(_key(challenge_id)) or {}).get("attempts") or 0)
    except (TypeError, ValueError):
        return 0


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


def _accepted_by_challenge(data: dict) -> dict[str, set[str]]:
    """按 challenge_id 前缀汇总所有键里的 accepted（含题号条目），用于跨键清理。"""
    out: dict[str, set[str]] = {}
    for key, entry in (data.get("challenges") or {}).items():
        if not isinstance(entry, dict):
            continue
        cid = str(key).split("::", 1)[0]
        bucket = out.setdefault(cid, set())
        for value in entry.get("accepted") or []:
            text = str(value or "").strip()
            if text:
                bucket.add(text)
    return out


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
    cross: dict[str, set[str]] = {}
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
        # 记下跨键要清的值（题级 accepted → 题号 rejected 也要删）
        for value in accepted:
            cross.setdefault(str(cid).split("::", 1)[0], set()).add(value)
        if json.dumps(entry, ensure_ascii=False, sort_keys=True) != before_json:
            changed = True
            fixed[str(cid)] = {"dropped_rejected": dropped, "attempts": attempts_value}
    # 跨键清理：同题不同题号条目里的 accepted 值，不应再出现在 rejected 里
    for cid, entry in list(challenges.items()):
        if not isinstance(entry, dict):
            continue
        bucket = cross.get(str(cid).split("::", 1)[0]) or set()
        if not bucket:
            continue
        before = entry.get("rejected") or []
        cleaned = [v for v in before if str(v).strip() not in bucket]
        if cleaned != before:
            entry["rejected"] = cleaned
            fixed.setdefault(str(cid), {"cross_key_dropped": len(before) - len(cleaned)})
            changed = True
    if changed:
        if backup:
            try:
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
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