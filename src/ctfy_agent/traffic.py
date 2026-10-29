#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量流量库：把每道题的 HTTP 交换落盘，跨轮次可检索（P1）。

补的缺口：
  - http_request 读满 2MB 却只回 5000 字符窗口，过后全文无从查起；
  - diff 只能和「同 host+path 最近一条」比，任意两条比不了，进程重启即丢；
  - http_burst / http_map 的响应正文基本被丢弃。

设计约束：
  - 纯 stdlib（sqlite3），镜像体积零变化；
  - 写入 best-effort：任何异常吞掉，绝不弄挂解题主流程；
  - 检索有预算保护（候选条数 + 正文扫描字节数都有上限），单次查询有界、可预期。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

# ---------- 容量护栏 ----------
TRAFFIC_MAX_ROWS = 5000                    # 每题最多保留多少条交换
TRAFFIC_MAX_BLOB_BYTES = 50 * 1024 * 1024  # 每题正文 blob 总字节上限
TRAFFIC_MIN_ROWS = 100                     # 剪枝保底：绝不删到最近 N 行以内（正文体积剪枝的硬地板）
BODY_CAP = 2 * 1024 * 1024                 # 单条正文存储上限（对齐 http_request 的 2MB 读取上限）
SEARCH_CANDIDATE_CAP = 800                 # search 一次最多翻多少条候选（仅在有关键词时用）
SEARCH_SCAN_BUDGET = 2 * 1024 * 1024       # search 正文扫描字节预算（保护 + 可预期时延）
PRUNE_BATCH = 500                          # 每次剪枝的批大小（规避 SQLite 变量数上限）
PRUNE_CHECK_EVERY = 100                    # 每 N 次写入才查一次剪枝（COUNT+glob 的开销摊销）
REGISTRY_MAX = 32                          # 进程内最多缓存的库句柄数（LRU）
GET_WINDOW = 5000                          # get 单次正文窗口（对齐 http_request 的 HTTP_WINDOW）
DIFF_MAX_CHARS = 4000                      # diff 文本上限（对齐 tools_local 的 DIFF_MAX_CHARS）
DIFF_INPUT_CAP = 256 * 1024                # diff 输入截断：difflib 对 2MB 正文是 O(n²)，预截防卡一步

_SCHEMA = """
CREATE TABLE IF NOT EXISTS exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    method TEXT,
    url TEXT,
    netloc TEXT,
    path TEXT,
    query TEXT,
    status INTEGER,
    mime TEXT,
    ms REAL,
    req_headers TEXT,
    req_body_blob TEXT,
    req_bytes INTEGER,
    resp_headers TEXT,
    resp_body_blob TEXT,
    resp_bytes INTEGER,
    truncated INTEGER DEFAULT 0,
    cache_hit INTEGER DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_path ON exchanges(netloc, path);
CREATE INDEX IF NOT EXISTS idx_status ON exchanges(status);
CREATE INDEX IF NOT EXISTS idx_ts ON exchanges(ts);
"""

_registry: dict[str, "TrafficStore"] = {}
_registry_lock = threading.Lock()


def _sanitize(challenge_id: str) -> str:
    """与 state.transcript_path 同款消毒：只留字母数字与 - _ .。"""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in challenge_id)


def resolve(run_dir, challenge_id: str) -> "TrafficStore":
    """取该题的库句柄（按 db 路径缓存，LRU 上限 REGISTRY_MAX）。

    run_dir 是 Path；challenge_id 合法与否由上层保证。绝不抛异常——TrafficStore
    构造内部失败时把自身标记为 disabled，调用方看 .enabled 即可。
    """
    db_path = Path(run_dir) / "traffic" / f"{_sanitize(challenge_id)}.sqlite"
    key = str(db_path)
    with _registry_lock:
        inst = _registry.get(key)
        if inst is None:
            if len(_registry) >= REGISTRY_MAX:
                # 真 LRU 驱逐：从 dict 移除但不 close——可能仍有 LocalTools 持有旧句柄，
                # close 会让它在解题中途静默停录（审计 A4）。无人持有则由 GC 收连接。
                oldest = next(iter(_registry))
                _registry.pop(oldest)
            inst = TrafficStore(db_path)
            _registry[key] = inst
        else:
            # 命中重排到队尾，才是名副其实的 LRU
            _registry[key] = _registry.pop(key)
        return inst


class TrafficStore:
    """单题流量库。写入/查询都用内部锁串行化（http_burst 主线程写，锁只是保险）。"""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.blobs_dir = db_path.parent / "blobs"
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._disabled = False
        self._writes_since_prune = 0
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self.blobs_dir.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), timeout=5.0, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_SCHEMA)
            conn.commit()
            self._conn = conn
        except Exception:
            self._disabled = True
            self._conn = None

    @property
    def enabled(self) -> bool:
        return self._conn is not None and not self._disabled

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
            self._disabled = True

    # ---------- 正文 blob（内容寻址去重） ----------

    def _put_blob(self, text: str | None) -> str | None:
        if not text:
            return None
        data = text.encode("utf-8", "replace")
        if len(data) > BODY_CAP:
            data = data[:BODY_CAP]
        digest = hashlib.sha256(data).hexdigest()
        path = self.blobs_dir / digest
        if not path.is_file():
            # 原子写：先落临时名再 rename，进程崩溃不会留下半截 blob（审计 B2）
            tmp = path.with_name(f"{digest}.tmp-{os.getpid()}-{threading.get_ident()}")
            try:
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except Exception:
                try:
                    tmp.unlink()
                except Exception:
                    pass
        return digest

    def _read_blob(self, digest: str | None) -> str:
        if not digest:
            return ""
        try:
            return (self.blobs_dir / digest).read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""

    def _blob_bytes_on_disk(self) -> int:
        try:
            return sum(p.stat().st_size for p in self.blobs_dir.glob("*") if p.is_file())
        except Exception:
            return 0

    # ---------- 写入 ----------

    def record(self, *, source: str, method: str = "", url: str = "", status=None,
               req_headers: dict | None = None, req_body: str = "",
               resp_headers: dict | None = None, resp_body: str = "",
               mime: str = "", ms=None, truncated: int = 0, cache_hit: int = 0,
               error: str = "") -> int:
        """落一条交换。best-effort：任何异常都吞掉并返回 0（0 = 没记上）。"""
        if not self.enabled:
            return 0
        try:
            with self._lock:
                netloc = path = query = ""
                if url:
                    p = urlparse(url)
                    netloc = p.netloc
                    path = p.path or "/"
                    query = p.query
                req_blob = self._put_blob(req_body) if req_body else None
                resp_blob = self._put_blob(resp_body) if resp_body else None
                cur = self._conn.execute(
                    "INSERT INTO exchanges(ts, source, method, url, netloc, path, query, "
                    "status, mime, ms, req_headers, req_body_blob, req_bytes, resp_headers, "
                    "resp_body_blob, resp_bytes, truncated, cache_hit, error) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        time.time(), source, method, url, netloc, path, query,
                        status, mime, ms,
                        json.dumps(req_headers, ensure_ascii=False) if req_headers else None,
                        req_blob, len(req_body.encode("utf-8", "replace")) if req_body else 0,
                        json.dumps(resp_headers, ensure_ascii=False) if resp_headers else None,
                        resp_blob, len(resp_body.encode("utf-8", "replace")) if resp_body else 0,
                        truncated, cache_hit, error,
                    ),
                )
                self._conn.commit()
                self._writes_since_prune += 1
                if self._writes_since_prune >= PRUNE_CHECK_EVERY:
                    self._writes_since_prune = 0
                    self._maybe_prune()
                return cur.lastrowid or 0
        except Exception:
            return 0

    def _delete_oldest(self, n: int) -> None:
        """分批删最老的 n 行（PRUNE_BATCH 一批，规避 SQLite 变量数上限，审计 A3）。"""
        n = max(0, int(n))
        while n > 0:
            batch = min(n, PRUNE_BATCH)
            ids = [r[0] for r in self._conn.execute(
                "SELECT id FROM exchanges ORDER BY id ASC LIMIT ?", (batch,)
            ).fetchall()]
            if not ids:
                break
            self._conn.execute("DELETE FROM exchanges WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
            self._conn.commit()
            n -= len(ids)

    def _referenced_blobs(self) -> set[str]:
        referenced: set[str] = set()
        for (blob,) in self._conn.execute("SELECT req_body_blob FROM exchanges WHERE req_body_blob IS NOT NULL"):
            referenced.add(blob)
        for (blob,) in self._conn.execute("SELECT resp_body_blob FROM exchanges WHERE resp_body_blob IS NOT NULL"):
            referenced.add(blob)
        return referenced

    def _gc_orphans(self, total: int) -> None:
        """删未被任何行引用的 blob（最老优先），直到低于上限或没有孤儿。"""
        if total <= TRAFFIC_MAX_BLOB_BYTES:
            return
        referenced = self._referenced_blobs()
        files = sorted(self.blobs_dir.glob("*"), key=lambda p: p.stat().st_mtime)
        for p in files:
            if total <= TRAFFIC_MAX_BLOB_BYTES:
                break
            if p.is_file() and p.name not in referenced:
                try:
                    total -= p.stat().st_size
                    p.unlink()
                except Exception:
                    pass

    def _delete_heavy_batch(self) -> bool:
        """删一批「非 2xx + 正文最大 + 最老」的行（保底保留最近 TRAFFIC_MIN_ROWS 行）。

        返回是否删到。当没有孤儿可 GC 时，这是把正文体积压回上限内唯一的手段（审计 A2）。
        """
        floor_row = self._conn.execute(
            "SELECT id FROM exchanges ORDER BY id DESC LIMIT 1 OFFSET ?", (TRAFFIC_MIN_ROWS - 1,)
        ).fetchone()
        floor = floor_row[0] if floor_row else None
        sql = "SELECT id FROM exchanges WHERE resp_body_blob IS NOT NULL"
        params: list = []
        if floor is not None:
            sql += " AND id < ?"
            params.append(floor)
        sql += (" ORDER BY (CASE WHEN status IS NULL OR status < 200 OR status >= 300 THEN 0 ELSE 1 END) ASC, "
                "resp_bytes DESC, id ASC LIMIT ?")
        params.append(PRUNE_BATCH)
        ids = [r[0] for r in self._conn.execute(sql, params).fetchall()]
        if not ids:
            return False
        self._conn.execute("DELETE FROM exchanges WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)
        self._conn.commit()
        return True

    def _maybe_prune(self) -> None:
        """容量护栏：行数超限删最老；正文体积超限先 GC 孤儿、再删最不重要的行。best-effort。"""
        try:
            cnt = self._conn.execute("SELECT COUNT(*) FROM exchanges").fetchone()[0]
            if cnt > TRAFFIC_MAX_ROWS:
                self._delete_oldest(cnt - max(1, TRAFFIC_MAX_ROWS // 2))
        except Exception:
            pass
        try:
            total = self._blob_bytes_on_disk()
            if total > TRAFFIC_MAX_BLOB_BYTES:
                self._gc_orphans(total)
                for _ in range(10):  # 删行 → GC → 重测，上限防死循环
                    total = self._blob_bytes_on_disk()
                    if total <= TRAFFIC_MAX_BLOB_BYTES:
                        break
                    if not self._delete_heavy_batch():
                        break
                    self._gc_orphans(total)
        except Exception:
            pass

    # ---------- 查询 ----------

    def _snippet(self, text: str, q: str, span: int = 120) -> str | None:
        """在 text 里大小写不敏感地找 q，返回围绕命中的片段；找不到返回 None。"""
        if not text:
            return None
        i = text.lower().find(q)
        if i < 0:
            return None
        lo = max(0, i - 40)
        return text[lo:lo + span]

    def search(self, query: str = "", status: str = "", method: str = "", source: str = "",
               limit: int = 10) -> dict:
        if not self.enabled:
            return {"total": 0, "shown": [], "hint": "流量库不可用"}
        limit = max(1, min(int(limit or 10), 20))
        q = (query or "").strip().lower()
        # 请求头/响应头也在检索范围（审计 A1：SPEC 承诺「query 匹配头」，原来没实现）
        sql = ("SELECT id, method, url, path, query, status, resp_bytes, ms, error, "
               "resp_body_blob, req_headers, resp_headers FROM exchanges")
        clauses: list[str] = []
        params: list = []
        if method:
            clauses.append("method = ?")
            params.append(method.upper())
        if source:
            clauses.append("source = ?")
            params.append(source)
        ignored_status: list[str] = []
        if status:
            tokens = [s.strip() for s in status.split(",") if s.strip()]
            vals = [t for t in tokens if t.isdigit()]
            ignored_status = [t for t in tokens if not t.isdigit()]
            if vals:
                clauses.append("status IN (%s)" % ",".join("?" * len(vals)))
                params.extend(int(v) for v in vals)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        # 无关键词时直接 LIMIT 到展示条数即可（审计 R7：不必拉满 800 候选）
        params.append(SEARCH_CANDIDATE_CAP if q else limit)
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except Exception:
            return {"total": 0, "shown": [], "hint": "流量库查询失败"}

        shown: list[dict] = []
        scanned = 0
        for rid, mth, url, path, query_, status_, resp_bytes, ms, err, resp_blob, req_h, resp_h in rows:
            if q:
                snippet = (self._snippet(err or "", q)
                           or self._snippet(req_h or "", q)
                           or self._snippet(resp_h or "", q)
                           or self._snippet(url or path or "", q))
                if snippet is None:
                    if scanned >= SEARCH_SCAN_BUDGET:
                        continue
                    body = self._read_blob(resp_blob)
                    scanned += len(body)
                    idx = body.lower().find(q)
                    if idx < 0:
                        continue
                    lo = max(0, idx - 60)
                    snippet = ("…" if lo > 0 else "") + body[lo:idx + 80] + ("…" if idx + 80 < len(body) else "")
            else:
                snippet = (url or path or "")[:120]
            shown.append({
                "id": rid, "method": mth, "path": path, "query": query_,
                "status": status_, "bytes": resp_bytes, "ms": ms, "snippet": snippet,
            })
            if len(shown) >= limit:
                break
        out: dict = {"total": len(shown), "shown": shown}
        hints: list[str] = []
        if q and scanned >= SEARCH_SCAN_BUDGET:
            hints.append("正文扫描达到预算上限，可能还有未扫到的命中——用 status/method/source 收窄后重查。")
        if ignored_status:
            hints.append("忽略非法状态码（只支持数字逗号分隔）：" + ", ".join(ignored_status))
        if hints:
            out["hint"] = "；".join(hints)
        return out

    def get(self, exchange_id: int, offset: int = 0, limit: int = 0) -> dict:
        if not self.enabled:
            return {"error": "流量库不可用"}
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT id, source, method, url, status, mime, ms, req_headers, "
                    "resp_headers, resp_body_blob, resp_bytes, error, cache_hit "
                    "FROM exchanges WHERE id=?", (int(exchange_id),)
                ).fetchone()
        except Exception:
            return {"error": "查询失败"}
        if row is None:
            return {"error": f"没有 id={exchange_id} 的交换"}
        (rid, source, method, url, status, mime, ms, req_headers, resp_headers,
         resp_blob, resp_bytes, err, cache_hit) = row
        body = self._read_blob(resp_blob)
        offset = max(0, int(offset or 0))
        wsize = GET_WINDOW
        if limit:
            wsize = max(200, min(int(limit), GET_WINDOW))
        window = body[offset:offset + wsize]
        out: dict = {
            "id": rid, "source": source, "method": method, "url": url,
            "status": status, "mime": mime, "elapsed_ms": ms, "cache_hit": bool(cache_hit),
            "req_headers": json.loads(req_headers) if req_headers else {},
            "resp_headers": json.loads(resp_headers) if resp_headers else {},
            "resp_bytes": resp_bytes, "offset": offset, "body": window,
        }
        if err:
            out["error"] = err
        if offset + len(window) < len(body):
            out["truncated"] = True
            out["next_offset"] = offset + len(window)
        return out

    def diff(self, a: int, b: int) -> dict:
        if not self.enabled:
            return {"error": "流量库不可用"}

        def fetch(x: int):
            try:
                with self._lock:
                    row = self._conn.execute(
                        "SELECT resp_body_blob, status, url FROM exchanges WHERE id=?", (int(x),)
                    ).fetchone()
            except Exception:
                return None, None, None
            if row is None:
                return None, None, None
            return self._read_blob(row[0]), row[1], row[2]

        body_a, st_a, url_a = fetch(a)
        body_b, st_b, url_b = fetch(b)
        if body_a is None:
            return {"error": f"没有 id={a} 的交换"}
        if body_b is None:
            return {"error": f"没有 id={b} 的交换"}
        # 输入截断：difflib 对 2MB 正文是 O(n²)，预截到 256KB 防一步卡死（审计 A5）
        input_truncated = len(body_a) > DIFF_INPUT_CAP or len(body_b) > DIFF_INPUT_CAP
        if len(body_a) > DIFF_INPUT_CAP:
            body_a = body_a[:DIFF_INPUT_CAP]
        if len(body_b) > DIFF_INPUT_CAP:
            body_b = body_b[:DIFF_INPUT_CAP]
        added = removed = 0
        shown: list[str] = []
        used = 0
        truncated = False
        for index, line in enumerate(difflib.unified_diff(
            body_a.splitlines(), body_b.splitlines(),
            fromfile=f"id={a}", tofile=f"id={b}", lineterm="", n=1,
        )):
            if index < 2:
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
            size = len(line) + 1
            if used + size <= DIFF_MAX_CHARS:
                shown.append(line)
                used += size
            else:
                truncated = True
        out: dict = {
            "a": {"id": a, "status": st_a, "url": url_a},
            "b": {"id": b, "status": st_b, "url": url_b},
            "added_lines": added, "removed_lines": removed, "truncated": truncated,
            "diff": "\n".join(shown),
        }
        if input_truncated:
            out["input_truncated"] = True
        # 审计 A6：行都超长被截断时 shown 为空，但差异真实存在——不能误报「逐行相同」
        if not shown and not truncated:
            out["note"] = "两份响应逐行相同（没有差异）。"
        return out
