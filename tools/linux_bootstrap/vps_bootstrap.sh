#!/bin/sh
# vps_bootstrap.sh —— 在「主办方分配的官方 VPS」上自举运行环境（封网、可能无 root）。
# 用法：sh vps_bootstrap.sh [bundle_dir]
#   默认 bundle_dir=./tools/linux_bootstrap/bundle（赛前在有网机器上 provision.py --bundle 生成）
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUNDLE="${1:-$HERE/bundle}"
TOOLKIT="$HOME/tpctf-toolkit"
PY="${PYTHON:-python3}"

echo "== 1) 环境探测 =="
"$PY" "$HERE/env_probe.py" --json > "$HOME/env_probe.json" 2>/dev/null || true
"$PY" "$HERE/env_probe.py" 2>/dev/null | tail -25 || true

echo
echo "== 2) 离线工具箱 =="
if [ -d "$BUNDLE" ]; then
  "$PY" "$HERE/provision.py" --install "$BUNDLE" --toolkit "$TOOLKIT" --verify
else
  echo "!! 没有 bundle（$BUNDLE）：封网环境无法下载，请赛前在有网机器上跑"
  echo "   python3 tools/linux_bootstrap/provision.py --bundle tools/linux_bootstrap/bundle"
fi

echo
echo "== 3) wireproxy（无 root 时的用户态 WireGuard）=="
if [ -x "$REPO/.tools/wireproxy/wireproxy" ]; then
  echo "已内置 Linux wireproxy：$REPO/.tools/wireproxy/wireproxy"
  chmod +x "$REPO/.tools/wireproxy/wireproxy" 2>/dev/null || true
elif command -v wireproxy >/dev/null 2>&1; then
  echo "PATH 里有 wireproxy"
else
  echo "!! 缺 wireproxy（Linux 版），VPN 题会受影响"
fi

echo
echo "== 4) 冒烟测试 =="
export PYTHONPATH="$TOOLKIT/pylibs:$REPO/src:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="$TOOLKIT/sysroot/usr/lib/x86_64-linux-gnu:$TOOLKIT/sysroot/usr/lib:${LD_LIBRARY_PATH:-}"
"$PY" - <<'PY' || true
try:
    import freetype
    print("  freetype:", freetype.__file__)
except Exception as exc:
    print("  freetype 不可用:", exc)
try:
    import capstone, unicorn, elftools
    print("  capstone/unicorn/pyelftools OK")
except Exception as exc:
    print("  反汇编/模拟库缺失:", exc)
PY
if [ -f "$REPO/runs/tpctf2026/attachments/AUTHORED-37DA5EFD/checker" ]; then
  echo "  checker 存在（rev 题）；若 FreeType 就绪可本地跑："
  echo "    HINT_FONT=<ttf> ./checker <24hex>"
fi

echo
echo "== 完成 =="
echo "下一步："
echo "  1) 把平台「本地 Agent 会话」给的 OPENAI_BASE_URL/OPENAI_API_KEY 写进 \$HOME/gateway.env"
echo "  2) 用 agent_start.sh 启动 agent"
