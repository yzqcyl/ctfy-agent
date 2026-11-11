#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每道题的状态机 + 落盘。

设计目标：进程重启后能接着跑；提交过的东西绝不重复提交。
状态：new → parsed → instance_up → probing → candidate_flag → submitted → done
                                      ↘ stalled（超时/超预算，放弃但留记录）
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

try:
    import msvcrt
except ImportError:  # pragma: no cover - 非 Windows
    msvcrt = None
try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None


@contextlib.contextmanager
def _lock_exclusive(path: Path):
    """进程间排他文件锁（Windows msvcrt / POSIX fcntl），保护提交幂等的读-改-写。"""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        # [fix 2026-11-03] Windows msvcrt.locking 按「当前 fd 偏移」锁定 1 字节。
        # 若本进程是第一个创建锁文件的（st_size==0 → 刚 write 过），fd 偏移停在 1，
        # 而后到进程（st_size!=0 → 不写）偏移为 0，两进程锁的是不同字节 → 不互斥。
        # 统一把偏移归零，让所有进程都锁 byte[0]。
        if msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        elif fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if msvcrt is not None:
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            elif fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

STATUSES = (
    "new",
    "parsed",
    "instance_up",
    "probing",
    "candidate_flag",
    "submitted",
    "done",
    "stalled",
)

# 允许的状态迁移（宽松：只挡明显倒退和重复提交）
ALLOWED = {
    "new": {"new", "parsed", "instance_up", "stalled"},
    "parsed": {"parsed", "instance_up", "probing", "stalled"},
    "instance_up": {"instance_up", "probing", "candidate_flag", "stalled"},
    "probing": {"probing", "candidate_flag", "instance_up", "stalled"},
    "candidate_flag": {"candidate_flag", "submitted", "probing", "stalled"},
    "submitted": {"submitted", "done", "probing"},
    "done": {"done"},
    "stalled": {"stalled", "probing", "parsed", "instance_up"},
}


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def flag_hash(flag: str) -> str:
    return hashlib.sha256(flag.strip().encode("utf-8")).hexdigest()[:16]


_TAG_SPLIT = re.compile(r"[,;/|\s]+")


def normalize_tags(value) -> list[str]:
    """把平台各种形态的 tags（list / 逗号分隔字符串 / 单值 / dict）归一成去重保序的 list[str]。

    平台免费给的题型先验（tags/difficulty/solves_count）是解题最重要的线索，
    入库时宁可宽容解析，也不要静默丢掉。
    """
    if value is None or value == "":
        return []
    if isinstance(value, str):
        raw: list = _TAG_SPLIT.split(value)
    elif isinstance(value, dict):
        for key in ("tags", "tag", "labels", "topics", "name"):
            if value.get(key):
                return normalize_tags(value[key])
        raw = list(value.keys())
    elif isinstance(value, (list, tuple, set)):
        raw = []
        for item in value:
            if isinstance(item, str):
                raw.extend(_TAG_SPLIT.split(item))
            elif isinstance(item, (list, tuple, set, dict)):
                raw.extend(normalize_tags(item))
            elif item is not None:
                raw.append(str(item))
    else:
        raw = [str(value)]
    out: list[str] = []
    for tag in raw:
        tag = str(tag).strip()
        if tag and tag not in out:
            out.append(tag)
    return out[:24]


def normalize_difficulty(value) -> str:
    """难度可能是 'easy' / 'hard' / 数字；统一成短字符串。"""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()[:32]


def normalize_solves(value) -> int | None:
    """已解队伍数；拿不到/解析不了就是 None（不写 0 以免伪造「无人解出」）。"""
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _merge_append(mem: list, disk: list) -> list:
    """保序去重合并两个「append 语义」的列表（solve_attempts）。

    盘上内容在前、内存内容在后，按 JSON 序列化后的指纹去重——不同 lane 可能登记同一条
    尝试记录，重复的只保留一份，且不改变先后顺序。
    """
    seen: set[str] = set()
    out: list = []
    for rec in list(disk) + list(mem):
        try:
            key = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            key = repr(rec)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


@dataclass
class Challenge:
    id: str
    name: str = ""
    category: str = ""
    points: int | None = None
    description: str = ""
    # 题型先验：平台 get_challenge / list_challenges 免费给，必须入库并送达给模型
    tags: list[str] = field(default_factory=list)   # 如 weak_password / osint / authentication_failures
    difficulty: str = ""                            # easy / medium / hard，也可能是数字
    solves_count: int | None = None                 # 已解队伍数（难度与方向的客观参照）
    is_qa_only: bool = False                        # 纯知识问答/MCQ：提交走 submit_qa_answer，不需要实例
    status: str = "new"
    attempts: int = 0
    solve_attempts: list = field(default_factory=list)  # 集群-B 解题尝试记录（跨进程持久化，每项一次尝试的摘要）
    submitted_ok: bool = False
    instance: dict = field(default_factory=dict)
    instance_id: str = ""         # 平台实例 UUID（真实平台按它提交/停止）
    oracle_calls: int = 0         # verify_answer 探针次数（不记账的免费试答案）
    tokens: dict = field(default_factory=dict)  # 累计用量（跨进程继承，费用刹车用）
    flags_tried: list[str] = field(default_factory=list)  # flag 的 sha256 前缀
    # P2-4：平台判定为错的**明文**答案（只留最近几条，用于回显「别再交这些」）。
    # 与 flags_tried 的分工：那个是幂等去重（哈希、永久），这个是给模型的负样本提示。
    rejected_answers: list[str] = field(default_factory=list)
    # [v7.2 A4] 按题号记平台判错的提交次数（per-question wrong-attempt cap，对齐 CTFY-0001 v3 §3.3）。
    # 与 attempts 的分工：attempts 是整题提交总计数（绝对安全上限，防失控），wrong_by_qid 是
    # 「每个题号各自错了几次」——v3 多轮里平台按每题号判 wrong 后重启我们以允许换值重交，
    # 换新题号不该被别的题号已烧掉的额度锁死。
    wrong_by_qid: dict = field(default_factory=dict)
    flag_value: str = ""          # 只在校验通过后记录
    # 多问题题（slug+flag / flag+root 这类）：question_id → 已通过答案。
    # 单值 flag_value 在第二问通过时会被覆盖，导致 result.json 的 answers 张冠李戴
    # （实测 SAMPLE-001：slug 通过后 flag_value=slug，最终 answers 把 slug 当 flag 交上去）。
    answers_by_qid: dict = field(default_factory=dict)
    # [fix 2026-11-05 P6b] verify_answer 通过但尚未 submit 的题号（verify 只是校验，平台不记分）
    verified_qids: dict = field(default_factory=dict)
    next_question_id: str = ""    # 多问题题：下一道待提交的 question_id（per-challenge 隔离，不写 config.extra）
    notes: list[str] = field(default_factory=list)
    tool_counts: dict = field(default_factory=dict)  # 写操作频控计数（start/stop 实例等）
    steps: int = 0
    updated_at: str = field(default_factory=now)

    def touch(self) -> None:
        self.updated_at = now()

    def append_solve_attempt(self, record: dict) -> None:
        """登记一次解题尝试（集群-B 编排器的 AttemptRecord 落盘形态）。"""
        self.solve_attempts.append(record)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Challenge":
        known = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        item = cls(**known)
        # 老 state.json 兼容 + 手写脏数据的宽容修复
        item.tags = normalize_tags(item.tags)
        item.difficulty = normalize_difficulty(item.difficulty)
        item.solves_count = normalize_solves(item.solves_count)
        return item

    def fingerprint(self) -> str:
        """题目输入的指纹（P1-1）：题号 + 描述的 sha256。

        用途：断点续跑前比对。附件/题面被平台改过（换包、补说明、复用了同名题号）时，
        旧上下文里的结论已经不可信，继续续跑等于拿错前提往下推——宁可重跑。

        刻意**不含**实例附件：每题实例都是新生成的（slug/flag 随实例变），
        把它算进去会让每次续跑都判不一致，指纹就失去意义了。
        """
        import hashlib  # noqa: PLC0415 - 只在这一处用

        material = f"{self.id}\n{self.description or ''}".encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:32]

    def facets(self) -> str:
        """题型线索单行文本：`难度 easy | 已解 10 队 | tags: weak_password, osint`。

        brief 与专家任务头共用，保证「平台给了什么」在提示里一字不丢。
        """
        bits: list[str] = []
        if self.difficulty:
            bits.append(f"难度 {self.difficulty}")
        if self.solves_count is not None:
            bits.append(f"已解 {self.solves_count} 队")
        if self.tags:
            bits.append("tags: " + ", ".join(self.tags))
        return " | ".join(bits)


class Store:
    """一个 run 一个 state.json，原子写（tmp + replace），崩溃不留半截文件。"""

    # 转录单行 append 的跨实例锁：集群-A 多路独立 Store 实例并发写同一 transcript 文件，
    # 串行化「打开-写-关」防单行交错（诊断数据，非关键路径但顺手收紧）。
    _transcript_lock = threading.Lock()

    # [fix 2026-11-03 B-5] save() 的进程内互斥锁，按 state.json 路径分片。集群-A 同进程多 lane
    # 各自持有独立 Store 实例但指向同一 run_dir/state.json：tmp 名唯一只能保证「写 tmp」不冲突，
    # 但最终 os.replace(tmp, path) 并发替换同一目标在 Windows 上会抛 PermissionError(13)。
    # 这里串行化同一目标文件的整个「写 tmp + replace」过程。
    _save_locks: dict[str, threading.Lock] = {}
    _save_locks_guard = threading.Lock()

    @classmethod
    def _save_lock(cls, path: str) -> threading.Lock:
        with cls._save_locks_guard:
            return cls._save_locks.setdefault(path, threading.Lock())

    # [fix 2026-11-11 本地 plat 重跑] 写操作频控计数改成「按运行（进程）作用域」：
    # `tool_counts` 是持久化审计账，跨轮继承；但 instance_ops_limit 的闸门如果也读持久化
    # 计数，同一个 run 目录重跑时第一次 start 就会被上一轮用掉的配额拦死（实测 17 题里 12 题
    # 计数已到 5，第二轮 52 次 start 被本地拦下、8 题无法起实例）。平台自己的硬上限仍然存在，
    # 本地闸门只需拦住「同一次运行内反复启停」，因此这里按 state.json 路径在进程内记一份
    # 本次运行的调用次数：同一进程内的多个 lane 共享（并发安全），新进程/新运行从 0 开始。
    _run_call_counts: dict[str, dict[tuple[str, str], int]] = {}
    _run_call_guard = threading.Lock()

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.path = self.run_dir / "state.json"
        self.challenges: dict[str, Challenge] = {}
        self.meta: dict = {}

    def _run_calls(self) -> dict[tuple[str, str], int]:
        """本次运行（进程）内的写操作计数，按 state.json 路径隔离。"""
        key = str(self.path.resolve())
        with Store._run_call_guard:
            return Store._run_call_counts.setdefault(key, {})

    def call_count(self, challenge_id: str, name: str) -> int:
        """本次运行内该题该写操作已经发生的次数（不读持久化审计账）。"""
        key = str(challenge_id)
        with Store._run_call_guard:
            return int(Store._run_call_counts.get(str(self.path.resolve()), {}).get((key, str(name)), 0))

    def reset_run_calls(self, challenge_id: str, names: tuple[str, ...] | None = None) -> None:
        """把该题「本次运行」的写操作计数清零（= 新一轮尝试重新获得配额）。

        [fix 2026-11-11 本地 plat 重跑] 驱动可能是一个**长驻进程**（GUI/批量跑题库：进程不退出，
        连着跑好几轮）。只把闸门改成进程作用域还不够——长驻进程里上一轮的计数会一直带进下一轮。
        因此每一轮解题入口（`Crew.solve` / `Solver.solve`）显式重置本题计数：
        一次尝试最多 5 次 start_instance（保护不变），但下一轮尝试重新从 5 开始。
        持久化 `tool_counts` 审计账不动（跨轮累计核对用）。
        """
        key = str(challenge_id)
        with Store._run_call_guard:
            counts = Store._run_call_counts.setdefault(str(self.path.resolve()), {})
            if names is None:
                for k in [k for k in counts if k[0] == key]:
                    counts.pop(k, None)
            else:
                for name in names:
                    counts.pop((key, str(name)), None)

    # ---------- 读写 ----------

    def load(self) -> "Store":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
            self.meta = data.get("meta") or {}
            self.challenges = {
                key: Challenge.from_dict(value) for key, value in (data.get("challenges") or {}).items()
            }
        return self

    def save(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {**self.meta, "saved_at": now()},
            "challenges": {key: value.to_dict() for key, value in self.challenges.items()},
        }
        # [fix 2026-11-03 B-5] 进程内按路径串行化「写 tmp + replace」：并发 replace 同一目标
        # 在 Windows 上会 PermissionError。锁内重读磁盘做一次 merge 兜底（见下）。
        with self._save_lock(str(self.path)):
            # [fix 2026-11-03 B-5] tmp 名必须进程内唯一：集群-A 同进程多 lane 并发 save，
            # 若只用 pid，各 lane 抢同一 tmp 文件，可能互相覆盖/写出半截。pid + 线程 id 唯一化。
            tmp = self.path.with_suffix(f".json.tmp.{os.getpid()}.{threading.get_ident()}")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)

    # ---------- 题目 ----------

    def get(self, challenge_id: str) -> Challenge | None:
        return self.challenges.get(challenge_id)

    def require(self, challenge_id: str) -> Challenge:
        item = self.get(challenge_id)
        if item is None:
            raise KeyError(f"state 里没有题目 {challenge_id}，先跑：python -m ctfy_agent challenges --sync")
        return item

    def upsert(self, data: dict) -> Challenge:
        challenge_id = str(data.get("id") or data.get("challenge_id") or data.get("name"))
        item = self.challenges.get(challenge_id) or Challenge(id=challenge_id)
        for field_name in ("name", "category", "points", "description"):
            value = data.get(field_name)
            if value not in (None, ""):
                setattr(item, field_name, value)
        # [fix 2026-11-03 B-4b] is_qa_only 是 bool，False 也是合法值，不能用 `not in (None,"")` 判空，
        # 单独处理：cli.py 已归一化该字段，此处落地即丢会致 solver 只能靠 tags 兜底识别 QA 题。
        qa = data.get("is_qa_only")
        if qa is not None:
            item.is_qa_only = bool(qa)
        # 题型先验：tags 是列表，用并集合并（不同来源可能各给一部分，一次给不全也不丢）
        tags = normalize_tags(data.get("tags"))
        if tags:
            item.tags = list(dict.fromkeys(normalize_tags(item.tags) + tags))
        difficulty = normalize_difficulty(data.get("difficulty"))
        if difficulty:
            item.difficulty = difficulty
        solves = normalize_solves(data.get("solves_count"))
        if solves is not None:
            item.solves_count = solves
        item.touch()
        self.challenges[challenge_id] = item
        return item

    def set_status(self, challenge_id: str, status: str) -> Challenge:
        if status not in STATUSES:
            raise ValueError(f"未知状态 {status}，可选：{', '.join(STATUSES)}")
        item = self.require(challenge_id)
        if status not in ALLOWED.get(item.status, set()):
            raise ValueError(f"{challenge_id}: 不允许从 {item.status} 直接跳到 {status}")
        item.status = status
        item.touch()
        return item

    # ---------- flag 幂等（进程间原子） ----------

    def _lock_path(self) -> Path:
        return self.run_dir / "state.lock"

    def _refresh_flags(self, challenge_id: str) -> None:
        """锁内调用：把磁盘上其他进程/lane 写入的累加字段合并进内存。

        [fix 2026-11-03 B-5 第三层] 原先只合并 flags_tried/attempts/submitted_ok 三字段，
        但 save() 是全量覆写（tmp+replace），并发下后写者会把先写者的 tool_counts / oracle_calls /
        tokens / rejected_answers / solve_attempts / notes / next_question_id / flag_value 全部回滚。
        尤其 tokens 被回滚会让费用刹车低估已花金额 → 可能超预算。这里把「跨 lane 单调累加」的
        字段全部按各自语义合并，其余非累加字段（status/instance/description 等）仍以磁盘为准。
        """
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except Exception:
            return
        disk = (data.get("challenges") or {}).get(challenge_id)
        if not isinstance(disk, dict):
            return
        item = self.challenges.get(challenge_id)
        if item is None:
            self.challenges[challenge_id] = Challenge.from_dict(disk)
            return
        # 并集（去重保序）
        item.flags_tried = list(dict.fromkeys(list(item.flags_tried) + list(disk.get("flags_tried") or [])))
        item.rejected_answers = list(dict.fromkeys(list(item.rejected_answers) + list(disk.get("rejected_answers") or [])))
        # wrong_by_qid：每 key 取 max（错交次数单调递增，跨 lane 不回退）
        disk_wrong = disk.get("wrong_by_qid") or {}
        if isinstance(disk_wrong, dict):
            for qid, cnt in disk_wrong.items():
                try:
                    item.wrong_by_qid[str(qid)] = max(int(item.wrong_by_qid.get(str(qid)) or 0), int(cnt))
                except (TypeError, ValueError):
                    continue
        # 单调取 max
        item.attempts = max(item.attempts, int(disk.get("attempts") or 0))
        item.oracle_calls = max(item.oracle_calls, int(disk.get("oracle_calls") or 0))
        # tool_counts：每 key 取 max（不同 lane 各自计数，不该互相覆盖）
        disk_tc = disk.get("tool_counts") or {}
        for name, cnt in disk_tc.items():
            try:
                item.tool_counts[name] = max(item.tool_counts.get(name, 0), int(cnt))
            except (TypeError, ValueError):
                continue
        # tokens：每 key 取 max（token 计数单调递增，跨 lane 累计）
        disk_tokens = disk.get("tokens") or {}
        if isinstance(disk_tokens, dict):
            for k, v in disk_tokens.items():
                try:
                    item.tokens[k] = max(int(item.tokens.get(k) or 0), int(v))
                except (TypeError, ValueError):
                    continue
        # solve_attempts：append 语义，按「盘上有、内存没有」补齐（保序去重，避免重跑重复登记）
        if item.solve_attempts and disk.get("solve_attempts"):
            item.solve_attempts = _merge_append(item.solve_attempts, disk.get("solve_attempts"))
        elif not item.solve_attempts and disk.get("solve_attempts"):
            item.solve_attempts = list(disk.get("solve_attempts"))
        # notes：append 语义，并集去重
        if item.notes and disk.get("notes"):
            item.notes = list(dict.fromkeys(list(disk.get("notes")) + list(item.notes)))
        elif not item.notes and disk.get("notes"):
            item.notes = list(disk.get("notes"))
        # 布尔/单值：磁盘有真值则采纳（全解标志、已解出的 flag、下一题号不回退）
        if disk.get("submitted_ok"):
            item.submitted_ok = True
        if disk.get("flag_value") and not item.flag_value:
            item.flag_value = disk.get("flag_value")
        # answers_by_qid：按题号并集（每个题号的答案单调，先到先得，跨 lane 不回退）
        disk_answers = disk.get("answers_by_qid") or {}
        if isinstance(disk_answers, dict):
            for qid, val in disk_answers.items():
                if val and not item.answers_by_qid.get(str(qid)):
                    item.answers_by_qid[str(qid)] = str(val)
        if disk.get("next_question_id") and not item.next_question_id:
            item.next_question_id = disk.get("next_question_id")

    def reserve_submit(self, challenge_id: str, flag: str, limit: int, *, question_id: str = "") -> str:
        """原子地预留一次提交（并发下同一条 flag 只会成功预留一次）。

        返回 'ok' / 'duplicate' / 'limit'。预留成功后必须调用 mark_submitted 回写结果。

        [v7.2 A4] 额度闸改为 per-question：`limit` 对应「每个题号的 wrong-attempt cap」
        （CTFY-0001 v3 的 per-question wrong-attempt cap），`attempts` 退为整题绝对安全上限
        （`limit * 16`，防失控，正常题号数远达不到）。v3 多轮里平台判某题 wrong 后会
        重启我们以允许换值重交，换一个题号不该被别的题号已烧掉的额度锁死。
        """
        with _lock_exclusive(self._lock_path()):
            self._refresh_flags(challenge_id)
            item = self.require(challenge_id)
            digest = flag_hash(flag)
            if digest in item.flags_tried:
                return "duplicate"
            # 整题绝对安全上限（防失控，正常题号数远达不到）：题号由 task.json 的
            # questions+gated 限定，且 _submit 会经 platform 权威题号集校验、不会无限增多，
            # 所以这里只需一个宽松兜底，真正的额度控制交给下面的 per-question 闸。
            if item.attempts >= limit * 16:
                return "limit"
            # per-question 错交闸：该题号已经判错 limit 次，不能再交同一个题号（换新值也一样，
            # 平台已用尽该题的 wrong-attempt cap）；换别的题号不受影响。
            qid = str(question_id or "flag")
            if int(item.wrong_by_qid.get(qid) or 0) >= limit:
                return "limit"
            item.flags_tried.append(digest)
            item.attempts += 1
            if item.status == "probing":
                item.status = "candidate_flag"
            item.touch()
            self.save()
        return "ok"

    def mark_submitted(self, challenge_id: str, flag: str, *, accepted: bool, fully_solved: bool = True,
                       question_id: str = "") -> None:
        """提交结果出来后回写通过状态（不回退 attempts / flags_tried）。

        [fix 2026-11-03 B-3] fully_solved=False 表示多问题题只过了当前题号（平台回执
        challenge_fully_solved=false）：只记 flag 不置 status="submitted"，让 precheck_fn
        不误判「已全解」而漏掉剩余题号的提交。submitted_ok 也只在全解时置 True。

        [fix 2026-11-03 v6.3] question_id 记录到 answers_by_qid：多问题题每个题号各自留档，
        最终 result.json 的 answers 才不会把第一问的答案写给第二问（SAMPLE-001 实测）。

        [v7.2 A4] accepted=False 时按题号记错交次数（wrong_by_qid），供 reserve_submit 的
        per-question 额度闸用——v3 多轮里平台判 wrong 后重启，换题号重交不被别题锁死。
        """
        with _lock_exclusive(self._lock_path()):
            self._refresh_flags(challenge_id)
            item = self.require(challenge_id)
            if not accepted:
                qid = str(question_id or "flag")
                item.wrong_by_qid[qid] = int(item.wrong_by_qid.get(qid) or 0) + 1
                item.touch()
                self.save()
                return
            if fully_solved:
                item.submitted_ok = True
            if question_id:
                item.answers_by_qid[str(question_id)] = flag
            if not item.flag_value:
                item.flag_value = flag
            if fully_solved:
                item.status = "submitted"
            item.touch()
            self.save()

    def release_submit(self, challenge_id: str, flag: str) -> None:
        """回滚一次预留（提交在网络层失败、未送达平台时调用）。

        背景（审计修复）：reserve_submit 在 call_raw 之前落账，网络异常会让 attempts 已扣、
        flag 已入 flags_tried，重跑提交同一 flag 会被判 "duplicate"——正确答案被幂等永久锁死。
        这里回滚预留，宁可让模型重试一次，也不锁死可能正确的答案。
        """
        with _lock_exclusive(self._lock_path()):
            self._refresh_flags(challenge_id)
            item = self.require(challenge_id)
            digest = flag_hash(flag)
            if digest in item.flags_tried:
                item.flags_tried.remove(digest)
                item.attempts = max(0, item.attempts - 1)
            item.touch()
            self.save()

    def can_submit(self, challenge_id: str, limit: int, *, question_id: str = "") -> bool:
        """per-question 额度预判（与 reserve_submit 的闸一致，非原子，仅用于提前给友好提示）。

        [v7.2 A4] 不再只看整题 attempts：该题号已判错 limit 次即返回 False，换题号不受影响。
        """
        item = self.require(challenge_id)
        if item.attempts >= limit * 16:
            return False
        qid = str(question_id or "flag")
        return int(item.wrong_by_qid.get(qid) or 0) < limit

    def note_rejected(self, challenge_id: str, answer: str, cap: int = 8) -> None:
        """记下平台判错的答案（P2-4）。只保留最近 cap 条，去重保序。

        提交次数只有 3 次，一次错交就是 1/3 的预算。把"已经交过的错答案"回显给模型，
        比事后在报告里解释便宜得多——它至少不会把同一个猜测再包装一遍交上来。
        """
        text = str(answer or "").strip()
        if not text:
            return
        with _lock_exclusive(self._lock_path()):
            item = self.require(challenge_id)
            rows = [x for x in item.rejected_answers if x != text]
            rows.append(text)
            item.rejected_answers = rows[-max(1, int(cap)):]
            item.touch()
            self.save()

    # ---------- 写操作频控 ----------

    def record_call(self, challenge_id: str, name: str, limit: int) -> bool:
        """登记一次写操作；返回 False 表示本次运行已达上限。

        [fix 2026-11-11 本地 plat 重跑] 闸门读「本次运行」计数（`_run_calls()`），持久化的
        `tool_counts` 只当审计账继续累加。旧实现读持久化计数，于是同一 run 目录重跑时配额
        已被上一轮用光 → 重跑第一次 start_instance 就被本地拦死（实测 52 次拦截、8 题无法起
        实例）。平台自己的每题硬上限仍在，被平台拒时会走 refund_call 回滚。
        """
        item = self.require(challenge_id)
        counts = self._run_calls()
        key = (str(challenge_id), str(name))
        used = int(counts.get(key, 0))
        if used >= int(limit):
            return False
        counts[key] = used + 1
        # 审计账：跨轮累计，只增不减（供人工核对，不参与闸门）
        item.tool_counts[str(name)] = int(item.tool_counts.get(str(name), 0) or 0) + 1
        item.touch()
        return True

    def refund_call(self, challenge_id: str, name: str) -> None:
        """回滚一次写操作登记：被配额拒绝/上游限流拒绝的调用不该占用上限。

        [fix 2026-11-05 P3] CTFARENA-001 实证：start_instance 前两次被 429 team_capacity_exceeded
        拒掉，却照样计入 instance_ops_limit=5，随后真正可用的调用被本地上限封死。
        [fix 2026-11-11] 本次运行计数与持久化审计账一起回滚（审计账不回滚会越记越高）。
        """
        item = self.require(challenge_id)
        counts = self._run_calls()
        key = (str(challenge_id), str(name))
        used = int(counts.get(key, 0))
        if used > 0:
            counts[key] = used - 1
        count = int(item.tool_counts.get(str(name)) or 0)
        if count > 0:
            item.tool_counts[str(name)] = count - 1
            item.touch()

    def bump_oracle(self, challenge_id: str, limit: int) -> bool:
        """登记一次 oracle 探针；返回 False 表示已达上限。"""
        item = self.require(challenge_id)
        if item.oracle_calls >= limit:
            return False
        item.oracle_calls += 1
        item.touch()
        return True

    def set_tokens(self, challenge_id: str, tokens: dict) -> None:
        """落盘累计用量（费用刹车要跨进程累计，不能只存在内存里）。"""
        item = self.require(challenge_id)
        item.tokens = dict(tokens or {})
        item.touch()

    # ---------- 笔记 / 转录 ----------

    def add_note(self, challenge_id: str, text: str) -> None:
        item = self.require(challenge_id)
        item.notes.append(f"[{now()}] {text[:2000]}")
        del item.notes[:-50]  # 只保留最近 50 条，防无限膨胀
        item.touch()

    def transcript_path(self, challenge_id: str) -> Path:
        target = self.run_dir / "transcripts"
        target.mkdir(parents=True, exist_ok=True)
        safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in challenge_id)
        return target / f"{safe}.jsonl"

    def append_transcript(self, challenge_id: str, record: dict) -> None:
        path = self.transcript_path(challenge_id)
        line = json.dumps({"at": now(), **record}, ensure_ascii=False) + "\n"
        with Store._transcript_lock:  # 多路并行 append 同一文件时串行化，防单行交错
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
