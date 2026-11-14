#!/bin/sh
# diagnose.sh —— 收集一台 VM 的完整诊断信息，写进 ~/tpctf_vm_diag.txt，方便把结果发给我定位。
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="${PYTHON:-python3}"
OUT="${DIAG_OUT:-$HOME/tpctf_vm_diag.txt}"
BUNDLE="${TPCTF_BUNDLE:-$HERE/bundle}"

{
  echo "== uname / 发行版 =="
  uname -a
  cat /etc/os-release 2>/dev/null | head -5 || true
  echo
  echo "== python =="
  "$PY" -V
  echo
  echo "== env_probe（JSON）=="
  "$PY" "$HERE/env_probe.py" --json 2>&1 || true
  echo
  echo "== 单元/端到端测试 =="
  export PYTHONPATH="$HOME/tpctf-toolkit/pylibs:$REPO/src:$REPO/runs/_tpctf:${PYTHONPATH:-}"
  export PYTHONIOENCODING=utf-8
  for t in policy_test m3_test m4_test mock_platform_test; do
    echo "--- $t ---"
    "$PY" -u "$REPO/runs/_tpctf/$t.py" 2>&1 | tail -20 || true
  done
  echo
  echo "== 模块导入 =="
  "$PY" - <<'PY' 2>&1 || true
for name in ["freetype","capstone","unicorn","elftools","policy","supervisor"]:
    try:
        __import__(name); print("OK ", name)
    except Exception as exc:
        print("FAIL", name, type(exc).__name__, str(exc)[:120])
PY
  echo
  echo "== 工具链 =="
  ls -la "$HOME/tpctf-toolkit" 2>/dev/null || echo "(无 toolkit)"
  echo
  echo "== wireproxy =="
  ls -la "$REPO/.tools/wireproxy/" 2>/dev/null || echo "(无 wireproxy)"
} > "$OUT" 2>&1
echo "诊断已写入 $OUT"
