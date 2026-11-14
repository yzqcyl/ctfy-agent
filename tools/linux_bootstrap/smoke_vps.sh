#!/bin/sh
# smoke_vps.sh —— 在任意 Linux 上离线冒烟（不依赖 Docker）：探测环境 → 解包工具链 → 导入冒烟。
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUNDLE="${1:-$HERE/bundle}"

echo "== env_probe =="
python3 "$HERE/env_probe.py" 2>&1 | tail -30 || true

echo
echo "== provision --install（离线）=="
if [ -d "$BUNDLE" ]; then
  python3 "$HERE/provision.py" --install "$BUNDLE" --toolkit "$HOME/tpctf-toolkit" --verify
else
  echo "!! 无 bundle（$BUNDLE）；若机器能联网，先跑："
  echo "   python3 tools/linux_bootstrap/provision.py --bundle tools/linux_bootstrap/bundle"
fi

echo
echo "== 导入冒烟 =="
export PYTHONPATH="$HOME/tpctf-toolkit/pylibs:$REPO/src:$REPO/runs/_tpctf"
python3 - <<'PY' || true
mods = ["freetype", "capstone", "unicorn", "elftools", "policy", "store_ext",
        "instances", "plat_adapt", "platform_health", "auto_handoff", "supervisor"]
for name in mods:
    try:
        __import__(name)
        print("  OK  ", name)
    except Exception as exc:
        print("  FAIL", name, type(exc).__name__, str(exc)[:80])
PY
