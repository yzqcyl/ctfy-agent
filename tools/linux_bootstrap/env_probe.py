#!/usr/bin/env python3
"""env_probe.py —— 在「官方 VPS」上先跑这个，输出环境能力矩阵与推荐安装模式。

用法：
    python3 env_probe.py            # 打印 JSON + 建议
    python3 env_probe.py --json     # 只打印 JSON（给 supervisor 读）

它回答的是：这台机器能不能
  1) 跑 Linux ELF（题目二进制/checker）
  2) 编译 C（LD_PRELOAD 钩子、zig/cc 兜底）
  3) 调试（gdb / ptrace / /proc/self/mem）
  4) 直接跑 FreeType 依赖的 checker（libfreetype.so.6 是否在）
  5) 装东西（apt + root / 只能用户态解包 .deb / 只能纯 Python）
  6) 连网（PyPI / 平台地址）
并给出 INSTALL_MODE 推荐：apt | deb-extract | wheel-only | limited
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

PROBE_BINARIES = ["gcc", "cc", "clang", "zig", "gdb", "ltrace", "strace",
                  "objdump", "readelf", "nm", "patchelf", "qemu-x86_64",
                  "qemu-aarch64-static", "docker", "podman", "bwrap", "unshare",
                  "setarch", "apt-get", "dpkg", "dpkg-deb", "tar", "xz", "zstd",
                  "python3", "pip3", "curl", "wget", "socat", "nc"]


def run(cmd: list[str], timeout: float = 8.0) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", "replace").strip()
    except Exception as exc:  # noqa: BLE001
        return -1, f"{type(exc).__name__}: {exc}"


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def find_lib(name: str) -> list[str]:
    """在常见搜索路径里找 .so（不依赖 ldconfig）。"""
    roots = ["/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/local/lib",
             "/lib/x86_64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
             "/lib/aarch64-linux-gnu", "/usr/lib/aarch64-linux-gnu"]
    hits = []
    for root in roots:
        path = os.path.join(root, name)
        if os.path.exists(path):
            hits.append(path)
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    info: dict = {}
    info["python"] = {"version": sys.version.split()[0], "executable": sys.executable}
    info["platform"] = {
        "system": platform.system(),
        "machine": platform.machine(),
        "release": platform.release(),
        "libc": " ".join(platform.libc_ver()),
    }
    info["kernel"] = read_text("/proc/sys/kernel/osrelease")
    info["distro"] = " ".join((read_text("/etc/os-release") or "").splitlines()[:4])
    info["user"] = {"uid": os.geteuid() if hasattr(os, "geteuid") else -1,
                    "is_root": (os.geteuid() == 0) if hasattr(os, "geteuid") else False}
    status = read_text("/proc/self/status")
    for key in ("CapEff", "Seccomp", "NoNewPrivs"):
        for line in status.splitlines():
            if line.startswith(key + ":"):
                info.setdefault("proc_status", {})[key] = line.split(":", 1)[1].strip()
    info["yama_ptrace_scope"] = read_text("/proc/sys/kernel/yama/ptrace_scope")

    binaries = {}
    for name in PROBE_BINARIES:
        path = shutil.which(name)
        binaries[name] = path or ""
    info["binaries"] = binaries

    info["libs"] = {
        "libfreetype.so.6": find_lib("libfreetype.so.6"),
        "libfreetype.so.6.20": find_lib("libfreetype.so.6.20"),
        "libc.so.6": find_lib("libc.so.6"),
        "libasan.so": find_lib("libasan.so.8") + find_lib("libasan.so"),
        "libstdc++.so.6": find_lib("libstdc++.so.6"),
    }

    # 能否编译
    can_compile = bool(binaries.get("gcc") or binaries.get("cc") or
                       binaries.get("clang") or binaries.get("zig"))
    info["can_compile_c"] = can_compile

    # 能否执行 ELF（本机就是 Linux 才可能）
    info["is_linux"] = platform.system() == "Linux"

    # 能否调试
    info["can_ptrace"] = info["is_linux"] and bool(binaries.get("gdb") or
                                                   binaries.get("strace"))

    # 网络（可选，超时要短）
    net = {}
    for label, host in (("pypi", "pypi.org"), ("platform", "ctfy.tsinghua.edu.cn")):
        code, out = run([sys.executable, "-c",
                         f"import socket;socket.create_connection(('{host}',443),3);print('ok')"],
                        timeout=6)
        net[label] = out == "ok"
    info["network"] = net

    # 权限与可写目录
    writable = []
    for path in ("/tmp", "/dev/shm", os.path.expanduser("~"), "/work", "/app"):
        if path and os.path.isdir(path) and os.access(path, os.W_OK):
            writable.append(path)
    info["writable_dirs"] = writable

    # 推荐安装模式
    if info["is_linux"] and info["user"]["is_root"] and binaries.get("apt-get"):
        mode = "apt"
    elif info["is_linux"] and binaries.get("dpkg-deb") and binaries.get("tar"):
        mode = "deb-extract"       # 用户态解包 .deb 到 $HOME/sysroot
    elif info["is_linux"] and binaries.get("python3"):
        mode = "wheel-only"        # 只能解 manylinux wheel（freetype-py/capstone/unicorn/pwntools）
    else:
        mode = "limited"           # 非 Linux：只能做纯静态分析（本场 Windows 的处境）
    info["recommended_install_mode"] = mode

    caps = {
        "run_elf": info["is_linux"],
        "compile_c": can_compile,
        "debug": info["can_ptrace"],
        "run_freetype_checker": info["is_linux"] and (
            bool(info["libs"]["libfreetype.so.6"]) or mode in ("apt", "deb-extract", "wheel-only")),
        "cross_run_other_arch": bool(binaries.get("qemu-x86_64") or
                                     binaries.get("qemu-aarch64-static")),
        "install_from_network": info["network"].get("pypi", False),
    }
    info["capabilities"] = caps

    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
        return 0

    print(json.dumps(info, ensure_ascii=False, indent=1))
    print("\n=== 能力矩阵 ===")
    for key, value in caps.items():
        print(f"  {key:24} {'✅' if value else '❌'}")
    print(f"\n=== 推荐安装模式：{mode} ===")
    print("  apt         : 有 root + apt → 直接装 libfreetype6 / gdb / gcc / binutils")
    print("  deb-extract : 用户态把 .deb 解到 $HOME/sysroot，LD_LIBRARY_PATH 指过去（无需 root）")
    print("  wheel-only  : 只能解 manylinux wheel（freetype-py 自带 libfreetype.so、capstone/unicorn/pwntools）")
    print("  limited     : 跑不了 Linux ELF → 只能静态逆向（本场 Windows 的处境，应避免）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
