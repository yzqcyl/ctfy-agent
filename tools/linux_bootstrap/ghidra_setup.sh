#!/usr/bin/env bash
# 安装 Ghidra headless + JDK25，并把 ghidra_decompile 装到 /usr/local/bin。
#
# 用途：给 rev 题提供"伪代码"能力（2026-11-16 rev 彩排卡在只有 objdump/capstone 逐段读）。
# 只在需要 rev 深挖的机器上跑（下载 ~700MB：JDK25 ~135MB + Ghidra ~550MB，解压后 ~1.1GB）。
#
# 用法：
#   sudo bash tools/linux_bootstrap/ghidra_setup.sh            # 默认 /opt/ghidra*
#   sudo GHIDRA_PARENT=/opt bash tools/linux_bootstrap/ghidra_setup.sh
#
# 装完后：`ghidra_decompile <binary> --limit 40` 直接出 C 伪代码（首次约 10 秒）。
set -eu
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
TOOLS="$REPO_DIR/tools/linux_bootstrap"
GHIDRA_PARENT=${GHIDRA_PARENT:-/opt}
JDK_DIR=${JDK_DIR:-/opt/jdk25}

[ "$(id -u)" = "0" ] || { echo "需要 root（sudo）"; exit 2; }

echo "== 1) 依赖（unzip/curl） =="
for pkg in unzip curl; do
  command -v "$pkg" >/dev/null 2>&1 || DEBIAN_FRONTEND=noninteractive apt-get -y -qq install "$pkg"
done

echo "== 2) JDK 25（Ghidra 12.x 要求 JDK25；老版本 Ghidra 可用 21） =="
if [ ! -x "$JDK_DIR/bin/java" ]; then
  mkdir -p "$(dirname "$JDK_DIR")" /tmp/jdk25-dl
  curl -4 -L --retry 3 -o /tmp/jdk25-dl/jdk25.tar.gz \
    "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jdk/hotspot/normal/eclipse"
  rm -rf "$JDK_DIR" && mkdir -p "$JDK_DIR"
  tar xzf /tmp/jdk25-dl/jdk25.tar.gz -C "$JDK_DIR" --strip-components=1
  rm -f /tmp/jdk25-dl/jdk25.tar.gz
fi
chmod -R a+rX "$JDK_DIR"
JAVA_HOME_25=$JDK_DIR
echo "JDK: $($JAVA_HOME_25/bin/java -version 2>&1 | head -1)"

echo "== 3) Ghidra 最新 release =="
mkdir -p "$GHIDRA_PARENT/ghidra-dl"
URL=$(python3 - <<'PY'
import json, urllib.request
data = json.load(urllib.request.urlopen(
    "https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest", timeout=30))
for asset in data.get("assets") or []:
    name = asset.get("name", "")
    if name.endswith(".zip") and "PUBLIC" in name:
        print(asset["browser_download_url"])
        break
PY
)
[ -n "$URL" ] || { echo "找不到 Ghidra PUBLIC zip"; exit 1; }
mkdir -p "$GHIDRA_PARENT/ghidra"
curl -4 -L --retry 3 -o "$GHIDRA_PARENT/ghidra-dl/ghidra.zip" "$URL"
rm -rf "$GHIDRA_PARENT/ghidra" && mkdir -p "$GHIDRA_PARENT/ghidra"
unzip -q "$GHIDRA_PARENT/ghidra-dl/ghidra.zip" -d "$GHIDRA_PARENT/ghidra"
rm -f "$GHIDRA_PARENT/ghidra-dl/ghidra.zip"
GHIDRA_HOME=$(find "$GHIDRA_PARENT/ghidra" -maxdepth 1 -mindepth 1 -type d | head -1)
chmod -R a+rX "$GHIDRA_HOME"
echo "Ghidra: $GHIDRA_HOME"
echo "$GHIDRA_HOME" > "$GHIDRA_PARENT/ghidra/INSTALL_DIR.txt"
echo "$JAVA_HOME_25" > "$GHIDRA_PARENT/ghidra/JAVA_HOME25.txt"

echo "== 4) 安装 ghidra_decompile 到 /usr/local =="
DEST=/usr/local/lib/ghidra_decompile
mkdir -p "$DEST"
cp -f "$TOOLS/ghidra_decompile.py" "$DEST/ghidra_decompile.py"
cp -f "$TOOLS/DecompileFunc.java" "$DEST/DecompileFunc.java"
chmod 755 "$DEST/ghidra_decompile.py"
ln -sf "$DEST/ghidra_decompile.py" /usr/local/bin/ghidra_decompile
chmod 755 /usr/local/bin/ghidra_decompile

echo "== 5) 自检（用 /bin/ls 反编译 1 个函数） =="
tmp=$(mktemp -d)
timeout 300 ghidra_decompile /bin/ls --limit 1 --timeout 300 > "$tmp/out.txt" 2>"$tmp/err.txt" || true
head -5 "$tmp/out.txt" 2>/dev/null || true
rm -rf "$tmp"
echo "完成。用法：ghidra_decompile <binary> [--function SUBSTR] [--limit N] [--timeout S]"