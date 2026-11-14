# -*- coding: utf-8 -*-
"""P2-19 附件多源 fallback 测试（纯离线，本地 HTTP 假镜像）。

覆盖：
  1) cache_put/cache_get：命中、内容/大小校验、大小不符视为未命中；
  2) mirror_urls：模板展开 + 环境变量 + 去重；
  3) fallback_fetch：主通道失败且缓存未命中 → 镜像成功（503 后重试/第二个 URL），并写缓存；
     再次 fallback 直接命中缓存（source=cache）；
  4) 全部失败 → ok=False，attempts 记录每路错误；
  5) 本地文件落盘校验（大小/sha256）。

运行：PYTHONPATH=src python tests/attachment_fallback_test.py
"""
from __future__ import annotations

import hashlib
import http.server
import os
import pathlib
import shutil
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ctfy_agent.attachments as att  # noqa: E402

CHECKS: list[tuple[str, bool]] = []
PAYLOAD = b"P2-19 fallback payload \x00\x01binary" * 100


def ok(name: str, cond: bool, detail: object = "") -> None:
    CHECKS.append((name, bool(cond)))
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" —— {detail}"))


class MirrorHandler(http.server.BaseHTTPRequestHandler):
    hits = 0
    fail_first = 1

    def do_GET(self):  # noqa: N802
        MirrorHandler.hits += 1
        if MirrorHandler.fail_first > 0:
            MirrorHandler.fail_first -= 1
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"busy")
            return
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *_args):  # 安静
        return


def start_server() -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), MirrorHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_cache(tmp: pathlib.Path) -> None:
    entry = {"file_id": "f1", "challenge_id": "c1", "name": "a.bin"}
    src = tmp / "src.bin"
    src.write_bytes(PAYLOAD)
    put = att.cache_put(entry, src)
    ok("cache_put 成功", put.get("ok") and put.get("bytes") == len(PAYLOAD), put)
    target = tmp / "hit.bin"
    got = att.cache_get(entry, target)
    ok("cache_get 命中并复制", got.get("ok") and target.read_bytes() == PAYLOAD, got)
    ok("cache_get 带 sha/source", got.get("sha256") == hashlib.sha256(PAYLOAD).hexdigest()
       and got.get("source") == "cache", got)

    # 大小不符 → 未命中
    data_path, _meta = att._cache_paths(entry)
    data_path.write_bytes(b"x")
    ok("缓存大小不符 → 未命中", att.cache_get(entry, tmp / "bad.bin").get("ok") is False)
    ok("缓存缺失 → 未命中", att.cache_get({"file_id": "nope"}, tmp / "n.bin").get("ok") is False)


def test_mirrors(tmp: pathlib.Path, base: str) -> None:
    entry = {"file_id": "f1", "challenge_id": "c1", "name": "a b.bin"}
    urls = att.mirror_urls(entry, [f"{base}/../x", "https://example.invalid/{file_id}",
                                   "https://example.invalid/{file_id}"])
    ok("mirror_urls 展开 file_id", "https://example.invalid/f1" in urls, urls)
    ok("mirror_urls 去重", len(urls) == len(set(urls)), urls)
    old = os.environ.get(att._MIRROR_ENV)
    os.environ[att._MIRROR_ENV] = "https://m/{file_id}"
    try:
        env_urls = att.mirror_urls(entry)
        ok("mirror_urls 读环境变量", env_urls == ["https://m/f1"], env_urls)
    finally:
        if old is None:
            os.environ.pop(att._MIRROR_ENV, None)
        else:
            os.environ[att._MIRROR_ENV] = old


def test_fallback(tmp: pathlib.Path, base: str) -> None:
    entry = {"file_id": "f2", "challenge_id": "c1", "name": "payload.bin"}
    target = tmp / "fb.bin"
    # 镜像先 503 一次，再成功；控制器里第一个 URL 404、第二个成功
    res = att.fallback_fetch(entry, target, primary_error="HTTP 503 platform_busy",
                             mirrors=[f"{base}/missing?file={entry['file_id']}",
                                      f"{base}/ok?file={entry['file_id']}"],
                             timeout=10)
    ok("fallback 镜像成功", res.get("ok") and target.read_bytes() == PAYLOAD, res)
    ok("fallback 记录 source=mirror:1", res.get("source") == "mirror:1", res.get("source"))
    ok("fallback attempts 含主通道错误",
       any("primary" in item for item in (res.get("attempts") or [])), res.get("attempts"))

    # 第二次同 entry：缓存命中
    target2 = tmp / "fb2.bin"
    res2 = att.fallback_fetch(entry, target2, primary_error="HTTP 503 again",
                              mirrors=[f"{base}/ok?file={entry['file_id']}"], timeout=10)
    ok("再次 fallback 命中缓存", res2.get("ok") and res2.get("source") == "cache", res2)
    ok("缓存命中内容一致", target2.read_bytes() == PAYLOAD)

    # 全部失败
    bad = att.fallback_fetch({"file_id": "f3", "challenge_id": "c1", "name": "x.bin"},
                             tmp / "x.bin", primary_error="boom",
                             mirrors=["https://127.0.0.1:1/none"], timeout=2)
    ok("全部失败 ok=False", bad.get("ok") is False, bad)
    ok("全部失败 attempts 三条", len(bad.get("attempts") or []) == 3, bad.get("attempts"))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    tmp = ROOT / "runs" / "_tmp" / "attach_fallback_test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    old_cache = os.environ.get(att._CACHE_ENV)
    os.environ[att._CACHE_ENV] = str(tmp / "cache")
    server = None
    try:
        MirrorHandler.hits = 0
        MirrorHandler.fail_first = 1
        server, base = start_server()
        test_cache(tmp)
        test_mirrors(tmp, base)
        test_fallback(tmp, base)
        ok("镜像确实被访问", MirrorHandler.hits >= 2, MirrorHandler.hits)
    finally:
        if server is not None:
            server.shutdown()
        if old_cache is None:
            os.environ.pop(att._CACHE_ENV, None)
        else:
            os.environ[att._CACHE_ENV] = old_cache
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [name for name, passed in CHECKS if not passed]
    print()
    if failed:
        print(f"attachment_fallback_test 失败 {len(failed)} 项：{'；'.join(failed)}")
        return 1
    print(f"attachment_fallback_test 全部通过（{len(CHECKS)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())