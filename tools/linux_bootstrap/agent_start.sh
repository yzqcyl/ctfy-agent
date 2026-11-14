#!/bin/sh
# agent_start.sh —— 官方 VPS 上启动参赛 agent 的统一入口。
#
# 前置：
#   1) 平台「本地 Agent 会话」生成网关变量，写入 $HOME/gateway.env（权限 600）：
#        export OPENAI_BASE_URL="https://<厂商>.<会话域名>"
#        export OPENAI_API_KEY="pk_<会话凭据>"
#        export OPENAI_MODEL="<网关支持的模型名>"      # 可选，视赛段/厂商
#        export CTFY_TOKEN="pf_xxx"                    # 平台解题令牌（agent_play 模板）
#        export AGENT_COMPETITION_ID="c0d8b4e3dd3b42c095d63d7dfa66431f"
#   2) 已跑过 vps_bootstrap.sh（工具链在 $HOME/tpctf-toolkit）
#
# 用法：sh agent_start.sh [driver_args...]
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
TOOLKIT="${TPCTF_TOOLKIT:-$HOME/tpctf-toolkit}"
PY="${PYTHON:-python3}"

# 网关/令牌：不在命令行暴露
if [ -f "${GATEWAY_ENV:-$HOME/gateway.env}" ]; then
  . "${GATEWAY_ENV:-$HOME/gateway.env}"
fi

# 走平台网关（封网后只能访问白名单；网关由平台转发模型请求）
# 注意：本项目的 LLM 客户端读 DEEPSEEK_*；config.py 已支持从 OPENAI_* 回退。
: "${OPENAI_BASE_URL:?缺 OPENAI_BASE_URL（平台「本地 Agent 会话」生成）}"
: "${OPENAI_API_KEY:?缺 OPENAI_API_KEY（pk_ 会话凭据）}"
: "${CTFY_TOKEN:?缺 CTFY_TOKEN（平台解题令牌 pf_）}"

export PYTHONPATH="$TOOLKIT/pylibs:$REPO/src:$REPO/runs/_tpctf:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="$TOOLKIT/sysroot/usr/lib/x86_64-linux-gnu:$TOOLKIT/sysroot/usr/lib:${LD_LIBRARY_PATH:-}"
export CTFY_WIREPROXY="${CTFY_WIREPROXY:-$REPO/.tools/wireproxy/wireproxy}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

RUN_ID="${AGENT_RUN_ID:-tpctf2026}"
LOG_DIR="$REPO/runs/$RUN_ID"
mkdir -p "$LOG_DIR"

echo "[agent_start] competition=${AGENT_COMPETITION_ID:-未设置} gateway=$OPENAI_BASE_URL"
echo "[agent_start] toolkit=$TOOLKIT wireproxy=$CTFY_WIREPROXY"
echo "[agent_start] 日志目录=$LOG_DIR"

# 默认跑 supervisor（自治调度），还没有 supervisor 时退回 run_live.py
if [ -f "$REPO/src/ctfy_agent/supervisor.py" ] || [ -f "$REPO/runs/_tpctf/supervisor.py" ]; then
  exec "$PY" -u "$REPO/runs/_tpctf/supervisor.py" \
    --competition "${AGENT_COMPETITION_ID:?}" --run-id "$RUN_ID" "$@"
else
  echo "[agent_start] 未找到 supervisor.py，退回 run_live.py（固定顺序 + 固定配额）"
  exec "$PY" -u "$REPO/runs/_tpctf/run_live.py" "$@"
fi
