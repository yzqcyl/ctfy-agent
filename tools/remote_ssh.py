#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""remote_ssh.py — 用 paramiko 连远端 Linux（密码或私钥），做只读盘点 / 传文件 / 跑命令。

Windows 上没有 sshpass，`ssh` 又不能从管道喂密码，所以统一走 paramiko（tools/pylibs 里已装）。

凭据文件（不进 git，runs/ 已 gitignore）：
    runs/_ssh/cred.json
    {"host": "1.2.3.4", "port": 22, "user": "root", "password": "..."}
  或 {"host": ..., "port": 22, "user": "root", "key_path": "C:/Users/x/.ssh/id_ed25519"}

用法：
    python tools/remote_ssh.py --inventory                 # 只读盘点（默认就是它）
    python tools/remote_ssh.py --cmd "uname -a" --cmd "df -h"
    python tools/remote_ssh.py --upload E:/ctfy_project/xxx.tar.gz:/root/xxx.tar.gz
    python tools/remote_ssh.py --download /ctfy/work/result.json:runs/_ssh/result.json
    python tools/remote_ssh.py --shell-script script.sh   # 把本地脚本传上去执行（只读盘点用）

--inventory 的清单刻意全是**只读**命令：不改系统、不装包、不重启服务。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRED = ROOT / "runs" / "_ssh" / "cred.json"

# paramiko 在 tools/pylibs（gitignore 的本机补库）里，不在运行时装机中
_PYLIBS = ROOT / "tools" / "pylibs"
if _PYLIBS.is_dir():
    import os  # noqa: PLC0415

    sys.path.insert(0, str(_PYLIBS))
    for _libs in _PYLIBS.glob("*.libs"):
        os.environ["PATH"] = str(_libs) + os.pathsep + os.environ.get("PATH", "")

# 只读盘点：官方托管机上「平台会推 task.json、起容器」，所以只看现状，不动任何东西
INVENTORY = [
    ("身份/内核", "id; uname -a; cat /etc/os-release 2>/dev/null | head -5"),
    ("资源", "nproc; free -h; df -h / /ctfy 2>/dev/null; uptime"),
    ("Python", "python3 --version; which python3; python3 -c 'import sys;print(sys.executable)'"),
    ("关键库", "python3 - <<'PY'\nimport importlib\nfor m in ('pwn','Crypto','z3','sympy','gmpy2','capstone','angr','paramiko','requests'):\n    try:\n        mod=importlib.import_module(m); print(m, getattr(mod,'__version__','?'))\n    except Exception as e:\n        print(m, 'MISSING', type(e).__name__)\nPY"),
    ("工具链", "for c in gdb objdump readelf strings file nm qemu-i386 socat nc curl tar unzip 7z docker podman tmux git; do printf '%-8s ' $c; command -v $c || echo '-'; done"),
    ("Docker", "docker version --format '{{.Server.Version}}' 2>&1 | head -2; docker images --format '{{.Repository}}:{{.Tag}}' 2>&1 | head -20"),
    ("CTFY 工作区", "ls -la /ctfy /ctfy/work 2>&1; echo '--- task.json ---'; cat /ctfy/work/task.json 2>&1 | head -40"),
    ("CTFY 环境变量", "env | grep -i '^CTFY' | sed 's/=.*/=<hidden>/'"),
    ("服务/进程", "ps aux --sort=-%mem 2>/dev/null | head -12; systemctl list-units --type=service --state=running --no-pager 2>/dev/null | head -15"),
    ("网络", "timeout 8 curl -sS -o /dev/null -w 'ctf2_mcp_http=%{http_code}\\n' https://ctf2.dasctf.com/api/ai/v1/mcp -X POST -H 'content-type: application/json' -d '{}' 2>&1 | tail -2"),
    ("时间/编码", "date -Is; locale 2>/dev/null | head -3"),
]


def load_cred() -> dict:
    if CRED.exists():
        return json.loads(CRED.read_text(encoding="utf-8"))
    print(f"缺少凭据文件 {CRED}\n格式：{{\"host\":\"...\",\"port\":22,\"user\":\"root\",\"password\":\"...\"}}",
          file=sys.stderr)
    raise SystemExit(2)


def connect(cred: dict):
    import paramiko  # noqa: PLC0415

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(hostname=cred["host"], port=int(cred.get("port") or 22),
                  username=str(cred.get("user") or "root"), timeout=20,
                  banner_timeout=20, auth_timeout=20)
    if cred.get("password"):
        kwargs["password"] = cred["password"]
    if cred.get("key_path"):
        kwargs["key_filename"] = str(cred["key_path"])
        kwargs["password"] = cred.get("password") or None
    cli.connect(**kwargs)
    return cli


def run(cli, cmd: str, timeout: float = 120.0) -> tuple[int, str]:
    stdin, stdout, stderr = cli.exec_command(cmd, timeout=timeout, get_pty=False)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out + (("\n[stderr] " + err) if err.strip() else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", action="store_true", help="只读盘点（默认）")
    ap.add_argument("--cmd", action="append", default=[], help="执行命令（可多次）")
    ap.add_argument("--upload", action="append", default=[], help="本地:远端")
    ap.add_argument("--download", action="append", default=[], help="远端:本地")
    ap.add_argument("--shell-script", default="", help="上传并在远端执行本地脚本")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    cred = load_cred()
    cli = connect(cred)
    print(f"已连接 {cred.get('user')}@{cred.get('host')}:{cred.get('port')}\n")
    try:
        if args.upload:
            sftp = cli.open_sftp()
            for pair in args.upload:
                # 本地路径在 Windows 上带盘符冒号（E:/...），远端路径不会 → 从右切
                local, remote = pair.rsplit(":", 1)
                sftp.put(local, remote)
                print(f"[upload] {local} -> {remote}")
            sftp.close()
        if args.download:
            sftp = cli.open_sftp()
            for pair in args.download:
                remote, local = pair.split(":", 1)
                sftp.get(remote, local)
                print(f"[download] {remote} -> {local}")
            sftp.close()
        if args.shell_script:
            sftp = cli.open_sftp()
            remote = "/tmp/_dsh_remote_script.sh"
            sftp.put(args.shell_script, remote)
            sftp.close()
            rc, out = run(cli, f"bash {remote}; echo '[rc]='$?", timeout=300)
            print(out)
            if rc:
                print(f"[warn] 脚本整体退出码 {rc}")
        cmds = args.cmd
        if args.inventory or not (cmds or args.upload or args.download or args.shell_script):
            for title, cmd in INVENTORY:
                rc, out = run(cli, f"{{ {cmd}\n}} 2>&1")
                print(f"===== {title} (rc={rc}) =====")
                print(out.rstrip())
                print()
        for cmd in cmds:
            rc, out = run(cli, cmd)
            print(f"===== $ {cmd} (rc={rc}) =====")
            print(out.rstrip())
            print()
    finally:
        cli.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())