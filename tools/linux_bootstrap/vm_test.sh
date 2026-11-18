#!/bin/sh
# vm_test.sh —— 在 Linux 虚拟机里一键做「官方 VPS 形态」的完整冒烟。
#
# 覆盖：环境探测 → 离线工具链解包 → 单元/端到端测试 → 导入冒烟 →
#       LLM 网关冒烟（真实或 mock）→ （可选）rev checker 冒烟。
#
# 用法：
#   # 1) 把仓库 + 离线 bundle 拷进 VM（或 VM 能联网时先 provision --bundle）
#   # 2) 在 VM 里：
#   sh tools/linux_bootstrap/vm_test.sh
#   # 3) 走真实网关时：
#   export OPENAI_BASE_URL="https://<厂商>.<会话域名>"
#   export OPENAI_API_KEY="<会话凭据>"
#   export OPENAI_MODEL="<网关模型名>"
#   sh tools/linux_bootstrap/vm_test.sh --gateway
set -eu
export LANG="${LANG:-C.UTF-8}" LC_ALL="${LC_ALL:-C.UTF-8}" PYTHONIOENCODING=utf-8
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
BUNDLE="${TPCTF_BUNDLE:-$HERE/bundle}"
PY="${PYTHON:-python3}"
GATEWAY="${1:-}"

echo "################ 1) 环境探测 ################"
"$PY" "$HERE/env_probe.py" 2>&1 | tail -30 || true

echo
echo "################ 2) 离线工具链 ################"
if [ -d "$BUNDLE" ]; then
  "$PY" "$HERE/provision.py" --install "$BUNDLE" --toolkit "$HOME/tpctf-toolkit" --verify
else
  echo "!! 无 bundle；尝试联网生成（封网环境会失败）"
  "$PY" "$HERE/provision.py" --bundle "$BUNDLE" && \
  "$PY" "$HERE/provision.py" --install "$BUNDLE" --toolkit "$HOME/tpctf-toolkit" --verify
fi

export PYTHONPATH="$HOME/tpctf-toolkit/pylibs:$REPO/src:$REPO/runs/_tpctf:${PYTHONPATH:-}"
# freetype-py 的 manylinux wheel 里自带 libfreetype.so，checker 直接可用
export LD_LIBRARY_PATH="$HOME/tpctf-toolkit/pylibs/freetype:$HOME/tpctf-toolkit/sysroot/usr/lib/x86_64-linux-gnu:$HOME/tpctf-toolkit/sysroot/usr/lib:${LD_LIBRARY_PATH:-}"
export PYTHONIOENCODING=utf-8

echo
echo "################ 3) 单元 / 端到端测试 ################"
for t in policy_test m3_test m4_test mock_platform_test; do
  echo "--- $t ---"
  "$PY" -u "$REPO/runs/_tpctf/$t.py" || echo "!! $t 失败"
done

echo
echo "################ 4) 模块导入冒烟 ################"
"$PY" - <<'PY'
mods = ["freetype", "capstone", "unicorn", "elftools",
        "policy", "store_ext", "instances", "plat_adapt",
        "platform_health", "auto_handoff", "supervisor"]
ok = 0
for name in mods:
    try:
        __import__(name)
        print("  OK  ", name); ok += 1
    except Exception as exc:
        print("  FAIL", name, type(exc).__name__, str(exc)[:80])
print(f"导入 {ok}/{len(mods)} 成功")
PY

echo
echo "################ 5) rev checker 冒烟（可选） ################"
CHECKER="$REPO/runs/tpctf2026/attachments/AUTHORED-37DA5EFD/checker"
TTF="$REPO/runs/tpctf2026/attachments/AUTHORED-37DA5EFD/challenge.ttf"
if [ -f "$CHECKER" ] && [ -f "$TTF" ]; then
  chmod +x "$CHECKER" 2>/dev/null || true
  echo "checker 存在；本地跑 HINT_TRACE=1（FreeType 从 toolkit 生效）"
  HINT_TRACE=1 HINT_FONT="$TTF" "$CHECKER" 000000000000000000000000 2>&1 | tail -6 || \
    echo "（checker 跑不起来，可能是 glibc/依赖不匹配，不影响其它项）"
  echo "--- 24 hex 全 0 的 marker_y（应为 128）---"
else
  echo "无 checker / ttf，跳过"
fi

echo
echo "################ 5b) .deb 解包器回归（需联网，可选） ################"
"$PY" -u "$REPO/runs/_tpctf/deb_extract_test.py" 2>&1 | tail -5 || \
  echo "（.deb 回归失败/无网络，不影响其它项）"

echo
if [ "$GATEWAY" = "--gateway" ]; then
  echo "################ 6) LLM 网关冒烟（真实） ################"
  "$PY" "$HERE/gateway_smoke.py" || true
else
  echo "################ 6) LLM 网关冒烟（本地 mock） ################"
  "$PY" "$HERE/mock_gateway.py" 18080 & MOCK_PID=$!
  sleep 1
  OPENAI_BASE_URL="http://127.0.0.1:18080/v1" OPENAI_API_KEY="pk_mock" \
    OPENAI_MODEL="mock-model" "$PY" "$HERE/gateway_smoke.py" || true
  kill "$MOCK_PID" 2>/dev/null || true
fi

echo
echo "################ 完成 ################"
echo "若第 3 步全部 PASS、第 4 步 11/11、第 6 步「通过 ✅」，则本地 VM 冒烟通过。"
