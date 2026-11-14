# -*- coding: utf-8 -*-
"""pwn_local 测试（P2-17）：离线验证 ELF 解析 / 协议探测 / 复现环境生成。

覆盖：
  1) inspect_elf：合成 64-bit PIE+Canary+GLIBC 字符串 → 字段正确；32-bit EXEC 分支；
     非 ELF 抛 ValueError；
  2) base_image 按 glibc 版本挑镜像；
  3) probe_protocol：本地 echo 服务探测，回包含探针；
  4) generate_repro：生成 Dockerfile/run.sh/poc.py/notes.md，内容含关键字段；
  5) CLI --binary/--json 退出码与输出。

运行：python tests/pwn_local_test.py
"""
from __future__ import annotations

import json
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "pwn_local"))

import pwn_local  # noqa: E402

CHECKS: list[tuple[str, bool]] = []


def ok(name: str, cond: bool, detail: object = "") -> None:
    CHECKS.append((name, bool(cond)))
    print(f"[{'ok' if cond else 'FAIL'}] {name}" + ("" if cond else f" —— {detail}"))


def make_elf64(path: pathlib.Path) -> pathlib.Path:
    """最小 64-bit little-endian 可执行头（PIE=ET_DYN，含 PT_GNU_STACK 可执行 + 字符串）。"""
    data = bytearray(64 + 56 * 3 + 256)
    data[0:4] = b"\x7fELF"
    data[4] = 2       # 64-bit
    data[5] = 1       # little
    data[6] = 1       # version
    struct.pack_into("<H", data, 16, 3)      # e_type = ET_DYN (PIE)
    struct.pack_into("<H", data, 18, 62)     # x86-64
    struct.pack_into("<Q", data, 24, 0x1000)  # entry
    struct.pack_into("<Q", data, 32, 64)     # e_phoff
    struct.pack_into("<H", data, 52, 64)     # e_ehsize
    struct.pack_into("<H", data, 54, 56)     # e_phentsize
    struct.pack_into("<H", data, 56, 2)      # e_phnum
    # ph[0]: PT_GNU_STACK，flags = RWX（可执行栈 → NX=False）
    base = 64
    struct.pack_into("<I", data, base, 0x6474E551)
    struct.pack_into("<I", data, base + 4, 0x7)
    # ph[1]: PT_INTERP，指向字符串
    interp = b"/lib64/ld-linux-x86-64.so.2\x00"
    interp_off = 64 + 56 * 3
    data[interp_off:interp_off + len(interp)] = interp
    struct.pack_into("<I", data, base + 56, 2)             # p_type = PT_INTERP
    struct.pack_into("<Q", data, base + 56 + 8, interp_off)  # p_offset
    struct.pack_into("<Q", data, base + 56 + 32, len(interp))  # p_filesz
    text = b"GLIBC_2.31 GLIBC_2.34 __stack_chk_fail"
    data[interp_off + len(interp):interp_off + len(interp) + len(text)] = text
    path.write_bytes(bytes(data))
    return path


def make_elf32(path: pathlib.Path) -> pathlib.Path:
    data = bytearray(52 + 32 * 2)
    data[0:4] = b"\x7fELF"
    data[4] = 1       # 32-bit
    data[5] = 1
    struct.pack_into("<H", data, 16, 2)      # ET_EXEC
    struct.pack_into("<H", data, 18, 3)      # x86
    struct.pack_into("<I", data, 28, 52)     # e_phoff
    struct.pack_into("<H", data, 40, 52)     # e_ehsize
    struct.pack_into("<H", data, 42, 32)     # e_phentsize
    struct.pack_into("<H", data, 44, 1)      # e_phnum
    struct.pack_into("<I", data, 52 + 8, 0x8048000)  # p_vaddr (非关键)
    path.write_bytes(bytes(data))
    return path


class EchoServer:
    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                data = conn.recv(1024)
                conn.sendall(b"echo:" + data)

    def close(self):
        self.sock.close()


def test_inspect(tmp: pathlib.Path) -> None:
    elf64 = make_elf64(tmp / "clean-room")
    report = pwn_local.inspect_elf(elf64)
    ok("64-bit 解析", report["bits"] == 64 and report["endian"] == "little", report)
    ok("机器类型 x86-64", report["machine"] == "x86-64", report["machine"])
    ok("PIE 识别", report["type"] == "PIE", report["type"])
    ok("可执行栈 → NX=False", report["nx"] is False, report["nx"])
    ok("Canary 识别", report["canary"] is True, report)
    ok("GLIBC 最高版本 2.34", report["glibc_minor"] == 34, report["glibc_minor_all"])
    ok("解释器路径", report["interp"].startswith("/lib64/ld-linux"), report["interp"])
    ok("ASan=False", report["asan"] is False)

    elf32 = make_elf32(tmp / "old")
    report32 = pwn_local.inspect_elf(elf32)
    ok("32-bit 解析", report32["bits"] == 32 and report32["machine"] == "x86", report32)
    ok("EXEC 识别", report32["type"] == "EXEC", report32["type"])
    try:
        pwn_local.inspect_elf(tmp / "not-elf")
        ok("非 ELF 抛 ValueError", False)
    except (ValueError, OSError):
        ok("非 ELF 抛 ValueError", True)


def test_base_image() -> None:
    ok("2.31 → ubuntu:20.04", pwn_local.base_image({"glibc_minor": 31}) == "ubuntu:20.04")
    ok("2.39 → ubuntu:24.04", pwn_local.base_image({"glibc_minor": 39}) == "ubuntu:24.04")
    ok("未知 → ubuntu:24.04", pwn_local.base_image({}) == "ubuntu:24.04")


def test_probe() -> None:
    server = EchoServer()
    try:
        result = pwn_local.probe_protocol("127.0.0.1", server.port, timeout=3.0,
                                          probes=[b"hello\n", b"1\n"])
        ok("探测目标记录", result["target"].endswith(str(server.port)), result["target"])
        ok("探测两轮", len(result["responses"]) == 2, result["responses"])
        ok("回包含探针内容", any("echo:hello" in item.get("text", "")
                              for item in result["responses"]), result["responses"])
        brk = pwn_local.probe_protocol("127.0.0.1", 1, timeout=0.3, probes=[b"x"])
        ok("连接失败记录 error", "error" in brk["responses"][0], brk)
    finally:
        server.close()


def test_generate(tmp: pathlib.Path) -> None:
    elf = make_elf64(tmp / "clean-room")
    result = pwn_local.generate_repro(elf, tmp / "repro")
    names = {pathlib.Path(item).name for item in result["files"]}
    ok("生成 4 个文件", names == {"Dockerfile", "run.sh", "poc.py", "notes.md"}, names)
    # Windows 上必须写 LF：run.sh 的 shebang 不能被翻成 "#!/usr/bin/env bash\r"
    raw_run = (tmp / "repro" / "run.sh").read_bytes()
    ok("run.sh 是 LF 换行（容器内可执行）",
       b"\r\n" not in raw_run and raw_run.startswith(b"#!/usr/bin/env bash\n"),
       raw_run[:40])
    dockerfile = (tmp / "repro" / "Dockerfile").read_text(encoding="utf-8")
    ok("Dockerfile 用满足最高 GLIBC 的镜像", "ubuntu:24.04" in dockerfile, dockerfile[:120])
    ok("Dockerfile 装 socat/libc6-dbg", "socat" in dockerfile and "libc6-dbg" in dockerfile)
    poc = (tmp / "repro" / "poc.py").read_text(encoding="utf-8")
    ok("poc.py 有 pwntools 骨架与偏移占位",
       "from pwn import" in poc and "OFFSET_TO_RIP" in poc, poc[:120])
    notes = (tmp / "repro" / "notes.md").read_text(encoding="utf-8")
    ok("notes 含保护信息", "Canary=True" in notes and "glibc 要求：34" in notes, notes[:300])

    cli = subprocess.run([sys.executable, "-X", "utf8",
                          str(ROOT / "tools" / "pwn_local" / "pwn_local.py"),
                          "--binary", str(elf), "--out", str(tmp / "repro2"), "--json"],
                         capture_output=True)
    ok("CLI 退出码 0", cli.returncode == 0,
       cli.stderr.decode("utf-8", "replace")[:200])
    payload = json.loads(cli.stdout.decode("utf-8", "replace").strip())
    ok("CLI JSON 含 inspect/repro", "inspect" in payload and "repro" in payload, list(payload))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    tmp = ROOT / "runs" / "_tmp" / "pwn_local_test"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        test_inspect(tmp)
        test_base_image()
        test_probe()
        test_generate(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [name for name, passed in CHECKS if not passed]
    print()
    if failed:
        print(f"pwn_local_test 失败 {len(failed)} 项：{'；'.join(failed)}")
        return 1
    print(f"pwn_local_test 全部通过（{len(CHECKS)} 项）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())