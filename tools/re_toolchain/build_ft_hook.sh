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

# 官方 VPS 常无 gcc 且封网（装不了 freetype 头）：随包带一份 x86_64 预编译 .so。
# 有编译器时优先本地真编译；没有编译器（或编译失败）再回落到预编译产物。
use_prebuilt() {
  local arch
  arch="$(uname -m 2>/dev/null || echo unknown)"
  for cand in "${script_dir}/prebuilt/${arch}/ft_hook.so" "${script_dir}/prebuilt/ft_hook.so"; do
    if [ -f "$cand" ]; then
      cp -f "$cand" "$out"
      echo "未使用本地编译器，已回落到随包预编译：${cand} -> ${out}"
      echo "（预编译由 glibc>=2.34 / x86_64 构建；其他架构请本地编译）"
      echo "自检：LD_PRELOAD=${out} CTFY_HOOK_LOG=/tmp/hook.log /bin/true && head -3 /tmp/hook.log"
      return 0
    fi
  done
  return 1
}

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
  if use_prebuilt; then
    exit 0
  fi
  echo "找不到 gcc/zig，且包内没有预编译 ft_hook.so：先在 VM 里安装编译工具（见脚本头注释）" >&2
  exit 3
fi

# shellcheck disable=SC2086
if ! $cc -shared -fPIC -O2 -Wall -Wextra -o "$out" "${script_dir}/ft_hook.c" \
    ${cflags} ${libs} -ldl; then
  echo "本地编译失败（可能是缺 FreeType 头），尝试回落到预编译产物" >&2
  if use_prebuilt; then
    exit 0
  fi
  exit 4
fi
echo "已生成：${out}"
echo "自检：LD_PRELOAD=${out} CTFY_HOOK_LOG=/tmp/hook.log /bin/true && head -3 /tmp/hook.log"