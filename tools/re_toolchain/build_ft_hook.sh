#!/usr/bin/env bash
# build_ft_hook.sh —— 在 VM 里一条命令编出 ft_hook.so（P2-14）。
#
#   ./build_ft_hook.sh                 # 默认输出 ../build/ft_hook.so
#   ./build_ft_hook.sh --outdir /tmp   # 指定输出目录
#
# 依赖：gcc（或 zig） + FreeType 开发头。bundle 里已有 libfreetype.so，但头文件需要：
#   Debian/Ubuntu: apt-get install -y gcc libfreetype6-dev pkg-config
#   alpine:        apk add gcc musl-dev freetype-dev pkgconfig
# 若 VM 里没有 gcc，可把 bundle 的 zig 放进 PATH（zig cc 可交叉编译 Linux .so）。
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
outdir="${script_dir}/../build"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --outdir) outdir="$2"; shift 2 ;;
    -h|--help) sed -n '1,20p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
mkdir -p "$outdir"
out="${outdir}/ft_hook.so"

cflags=""
libs=""
if command -v pkg-config >/dev/null 2>&1 && pkg-config --exists freetype2; then
  cflags="$(pkg-config --cflags freetype2)"
  libs="$(pkg-config --libs freetype2)"
else
  # 没有 pkg-config 时按常见路径兜底
  for inc in /usr/include/freetype2 /usr/local/include/freetype2; do
    if [ -d "$inc" ]; then cflags="${cflags} -I${inc}"; fi
  done
  libs="-lfreetype"
fi

if command -v gcc >/dev/null 2>&1; then
  cc=gcc
elif command -v zig >/dev/null 2>&1; then
  cc="zig cc"
else
  echo "找不到 gcc/zig：先在 VM 里安装编译工具（见脚本头注释）" >&2
  exit 3
fi

# shellcheck disable=SC2086
$cc -shared -fPIC -O2 -Wall -Wextra -o "$out" "${script_dir}/ft_hook.c" \
    ${cflags} ${libs} -ldl
echo "已生成：${out}"
echo "自检：LD_PRELOAD=${out} CTFY_HOOK_LOG=/tmp/hook.log /bin/true && head -3 /tmp/hook.log"