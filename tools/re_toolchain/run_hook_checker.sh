#!/usr/bin/env bash
# run_hook_checker.sh —— 用 ft_hook.so 跑 checker 并收集 hook.log（P2-14）。
#
#   ./run_hook_checker.sh --so build/ft_hook.so --log hook.log -- ./checker input.bin
#
# 任何 checker 参数原样透传；退出码就是 checker 的退出码（日志在 --log）。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
so="${script_dir}/../build/ft_hook.so"
log="${script_dir}/../build/hook.log"
checker=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --so) so="$2"; shift 2 ;;
    --log) log="$2"; shift 2 ;;
    --) shift; checker=("$@"); break ;;
    -h|--help) sed -n '1,12p' "$0"; exit 0 ;;
    *) checker+=("$1"); shift ;;
  esac
done

if [ ! -f "$so" ]; then
  echo "找不到 $so：先跑 build_ft_hook.sh" >&2
  exit 3
fi
if [ "${#checker[@]}" -eq 0 ]; then
  echo "用法：$0 [--so ft_hook.so] [--log hook.log] -- <checker> [args...]" >&2
  exit 2
fi
mkdir -p "$(dirname "$log")"
: > "$log"
echo "运行：LD_PRELOAD=$so ${checker[*]}" >&2
set +e
LD_PRELOAD="$so" CTFY_HOOK_LOG="$log" "${checker[@]}"
code=$?
set -e
echo "checker 退出码=${code}，日志：${log}（$(wc -l < "$log" 2>/dev/null || echo 0) 行）" >&2
if [ ! -s "$log" ]; then
  echo "警告：hook.log 为空——检查 libfreetype 是否动态链接（ldd）、或符号被静态编译进 checker" >&2
fi
exit "$code"