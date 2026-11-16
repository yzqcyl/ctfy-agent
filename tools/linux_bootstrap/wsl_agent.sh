#!/usr/bin/env bash
# wsl_agent.sh —— 在 WSL（Ubuntu 24.04）里以 Linux 原生环境跑 agent/工具。
#
# 为什么需要：Windows GUI 里的 shell 工具跑在 Windows 宿主（cmd.exe），rev/pwn 要用的
# gcc/gdb/patchelf/pwntools/ROPgadget 都不在 Windows 上，正确率数据不可信。正式 VPS 是
# Ubuntu 4C8G，所以评测/彩排都应该在这个 Linux 运行时里跑。
#
# 用法（Windows 侧）：
#   wsl.exe -d Ubuntu-24.04 -- bash /mnt/e/ctfy_project/tools/linux_bootstrap/wsl_agent.sh \
#       python3 runs/_tpctf/rehearsal.py
#   wsl.exe -d Ubuntu-24.04 -- bash tools/linux_bootstrap/wsl_agent.sh \
#       python3 -m ctfy_agent.gui.app        # 需要 X 时可换 GUI 方案
#
# 可用环境变量覆盖：CTFY_REPO、CTFY_TOOLKIT。
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${CTFY_REPO:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
TOOLKIT="${CTFY_TOOLKIT:-/root/tpctf-toolkit/pylibs}"
SYSROOT="${CTFY_TOOLKIT_SYSROOT:-/root/tpctf-toolkit/sysroot}"

export PYTHONPATH="$TOOLKIT:$REPO/src${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONIOENCODING=utf-8
export PYTHONUTF8=1
if [ -d "$SYSROOT" ]; then
  export LD_LIBRARY_PATH="$SYSROOT/usr/lib/x86_64-linux-gnu:$SYSROOT/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi
if [ -d "$TOOLKIT/bin" ]; then
  export PATH="$TOOLKIT/bin:$PATH"
fi
cd "$REPO" || exit 1
exec "$@"
