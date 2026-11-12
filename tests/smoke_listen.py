#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_listen.py — 回调监听工具（listen）回归。

背景（2026-11-12 实测）：SAMPLE-024 是「靶场主动回连我们」的题型（beacon 把 flag
投递到 ctfy-callback:4444）。agent 的工具面只有主动查询（tcp/http_request），
没有 bind+accept 能力，本地只能靠人手起 `nc -l` 才解出来。正式比赛容器在靶场网内、
回调名会解析到本容器，但 agent 依旧需要能监听，否则这类题（exfil beacon / SSRF 回调 /
reverse connection）直接 0 分。

本工具还内置「先 bind → 自动触发一次 → 再 accept」的顺序：单线程 agent 做不到
「先开后台监听再去触发」，把触发塞进工具里才能覆盖「靶场要等回调完成才回包」的题。

覆盖 9 条：
  1) TCP 收包：对端连进来发 FLAG → 输出含原文
  2) TCP 回送：reply 在收到数据后回送，客户端确实收到
  3) send_first：连上立刻发 banner（服务端先要指令的场景）
  4) UDP 收数据报
  5) 内置触发顺序：trigger_url → 对端回连 → 收到 flag（真实调用链）
  6) 无人回连：超时返回排查提示而不是抛错，且耗时受控
  7) 参数校验：端口 0 / 特权端口（非 root 绑不上）/ 非法 bind 地址
  8) 白名单：trigger_url 指向未登记主机 → ToolError（防「监听」变成任意 SSRF 出口）
  9) 接线：DOMAIN_SPECS / MISC_EXT_SPECS 含 listen，config 角色 local 名单已放行

运行: python tests/smoke_listen.py
"""
from __future__ import annotations

import http.server
import json
import pathlib
import shutil
import socket
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ctfy_agent import Store  # noqa: E402
from ctfy_agent import tools_misc_ext  # noqa: E402
from ctfy_agent.tools_domain_ext import (  # noqa: E402
    DOMAIN_NAMES, DomainToolError, listen,
)
from ctfy_agent.tools_local import LocalTools, ToolError  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


def free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def run_listener(box: dict, **kwargs) -> threading.Thread:
    def target() -> None:
        try:
            box["out"] = listen(**kwargs)
        except BaseException as exc:  # noqa: BLE001 - 测试要把异常带回来
            box["err"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread


def _wait_listening(port: int, tries: int = 40) -> bool:
    """等监听端口真的可连（避免测试自己的 sleep 竞态）。"""
    for _ in range(tries):
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.2)
            sock.close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


def _local(tmp: pathlib.Path) -> LocalTools:
    store = Store(tmp / "store").load()
    store.challenges.clear()
    return LocalTools(store, "web-01", traffic_enabled=False)


def main() -> int:
    tmp = ROOT / "runs" / "_tmp_listen"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)

    # ---- 1) TCP 收包 ----
    # 注意：_wait_listening() 的就绪探针本身会占一个 accept 名额（探针连上即断），
    # 所以这里 max_conns=2：名额 #1 给探针、#2 才是真正发数据的客户端。
    port = free_port()
    box: dict = {}
    thread = run_listener(box, port=port, timeout=5.0, max_conns=2)
    if _wait_listening(port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        sock.sendall(b"FLAG{listen_tcp_ok}\n")
        sock.close()
    thread.join(8)
    out = str(box.get("out", ""))
    check("TCP 收到回调数据", "FLAG{listen_tcp_ok}" in out and "连接 #" in out, out[:160])

    # ---- 2) TCP 回送（收到后回）----
    port = free_port()
    box = {}
    thread = run_listener(box, port=port, timeout=5.0, max_conns=2, reply="PONG")
    got = b""
    if _wait_listening(port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        sock.sendall(b"PING")
        sock.settimeout(3)
        try:
            got = sock.recv(64)
        except OSError:
            got = b"<timeout>"
        sock.close()
    thread.join(8)
    out = str(box.get("out", ""))
    check("TCP 收到后回送 reply", got == b"PONG" and "已回送 4 字节" in out, f"got={got!r} out={out[:120]}")

    # ---- 3) send_first：连上立刻发 banner ----
    port = free_port()
    box = {}
    thread = run_listener(box, port=port, timeout=5.0, max_conns=2, reply="BANNER\n", send_first=True)
    got = b""
    if _wait_listening(port):
        sock = socket.create_connection(("127.0.0.1", port), timeout=3)
        sock.settimeout(3)
        try:
            got = sock.recv(64)
        except OSError:
            got = b"<timeout>"
        sock.sendall(b"hi")
        sock.close()
    thread.join(8)
    check("send_first 连上即发 banner", got.startswith(b"BANNER"), f"got={got!r}")

    # ---- 4) UDP 收数据报 ----
    port = free_port()
    box = {}
    thread = run_listener(box, port=port, proto="udp", timeout=5.0)
    time.sleep(0.2)
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.sendto(b"FLAG{listen_udp_ok}", ("127.0.0.1", port))
    finally:
        udp.close()
    thread.join(8)
    out = str(box.get("out", ""))
    check("UDP 收到数据报", "FLAG{listen_udp_ok}" in out and "数据报 #1" in out, out[:160])

    # ---- 5) 内置触发顺序：trigger_url → 对端回连 → 收包 ----
    cb_port = free_port()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - 标准库接口
            # 模拟靶场行为：收到触发请求后，把 flag 投递到我们的回调端口
            try:
                back = socket.create_connection(("127.0.0.1", cb_port), timeout=3)
                back.sendall(b"FLAG{triggered_cb_ok}")
                back.close()
            except OSError:
                pass
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args) -> None:  # 静音
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    http_port = int(server.server_address[1])
    threading.Thread(target=server.handle_request, daemon=True).start()
    box = {}
    thread = run_listener(box, port=cb_port, timeout=5.0,
                          trigger_url=f"http://127.0.0.1:{http_port}/go")
    thread.join(10)
    server.server_close()
    out = str(box.get("out", ""))
    check("内置触发顺序（先监听后触发）能收回连数据",
          "[触发]" in out and "FLAG{triggered_cb_ok}" in out, out[:200])

    # ---- 6) 无人回连：给排查提示、不抛错、耗时受控 ----
    port = free_port()
    started = time.time()
    out = listen(port=port, timeout=1.0)
    elapsed = time.time() - started
    check("无人回连给排查提示", "没有任何" in out and "排查" in out, out[:160])
    check("超时受控（<4s）", elapsed < 4.0, f"{elapsed:.1f}s")

    # ---- 7) 参数校验 ----
    try:
        listen(port=0)
        check("端口 0 被拒绝", False, "没抛错")
    except DomainToolError as exc:
        check("端口 0 被拒绝", "端口非法" in str(exc), str(exc)[:100])
    try:
        listen(port=80)
        check("特权端口被拒绝并解释非 root", False, "没抛错")
    except DomainToolError as exc:
        check("特权端口被拒绝并解释非 root", "1024" in str(exc) and "root" in str(exc), str(exc)[:120])
    try:
        listen(port=free_port(), bind="10.99.99.99")
        check("非法 bind 地址被拒绝", False, "没抛错")
    except DomainToolError as exc:
        check("非法 bind 地址被拒绝", "bind" in str(exc), str(exc)[:120])

    # ---- 8) 白名单：trigger_url 不能指向未登记主机 ----
    local = _local(tmp)
    try:
        local.call("listen", {"port": free_port(), "timeout": 1, "trigger_url": "http://evil.example/steal"})
        check("trigger_url 白名单拦截", False, "没抛错")
    except ToolError as exc:
        msg = str(exc)
        check("trigger_url 白名单拦截", "还没有登记任何可访问实例" in msg or "合规拦截" in msg, msg[:140])
    # 登记了一个实例后，指向别的主机仍然要拦（防「监听」变任意 SSRF 出口）
    local.allow_instance("cb.example", 4444)
    try:
        local.call("listen", {"port": free_port(), "timeout": 1, "trigger_url": "http://evil.example/steal"})
        check("已登记端点后仍拦截陌生主机", False, "没抛错")
    except ToolError as exc:
        check("已登记端点后仍拦截陌生主机", "合规拦截" in str(exc), str(exc)[:140])

    # ---- 9) 接线：spec 表与角色名单 ----
    check("DOMAIN_NAMES 含 listen", "listen" in DOMAIN_NAMES)
    check("MISC_EXT_SPECS 含 listen", "listen" in tools_misc_ext.NAMES,
          f"names={sorted(tools_misc_ext.NAMES)[:6]}…")
    config = json.loads((ROOT / "config" / "agent.json").read_text(encoding="utf-8"))
    roles = config.get("roles") or {}
    missing = [r for r in ("web", "rev", "crypto", "pwn", "misc") if "listen" not in roles.get(r, {}).get("local", [])]
    check("五个网络型角色都放行 listen", not missing, f"缺: {missing}")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print(f"失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("smoke_listen 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())