#!/bin/sh
# picix-assistant 启动入口：确保 CloakBrowser Chromium 就绪后启动服务
#
# 缓存布局（/opt/cloakbrowser 为命名卷，升级/重建镜像不重新下载）：
#   /opt/cloakbrowser/chromium-<版本>-pro/chrome
# 就绪策略（三级）：
#   1. 卷内已有对应版本二进制 -> 直接命中
#   2. 无缓存但挂载了种子包(/tmp/cb.tar.gz) -> 解包到卷（离线，约 5 秒）
#   3. 无缓存无种子 -> cloakbrowser 在线下载（CLOAKBROWSER_DOWNLOAD_URL 主源，
#      失败自动回退 GitHub Releases；国内可将主源配置为镜像地址）
set -e

CACHE="${CLOAKBROWSER_CACHE_DIR:-/opt/cloakbrowser}"
SEED_VER="${CB_SEED_VERSION:-146.0.7680.177.5}"
SEED_TAR="/tmp/cb.tar.gz"

if [ -z "$CLOAKBROWSER_BINARY_PATH" ]; then
  DIR="$CACHE/chromium-$SEED_VER-pro"
  if [ -x "$DIR/chrome" ]; then
    echo "[entrypoint] Chromium 缓存命中: $DIR/chrome"
    export CLOAKBROWSER_BINARY_PATH="$DIR/chrome"
  elif [ -f "$SEED_TAR" ]; then
    echo "[entrypoint] 首次初始化: 解包种子 -> $DIR"
    mkdir -p "$DIR"
    tar -xzf "$SEED_TAR" -C "$DIR"
    chmod +x "$DIR/chrome" 2>/dev/null || true
    "$DIR/chrome" --version 2>/dev/null || true
    export CLOAKBROWSER_BINARY_PATH="$DIR/chrome"
  else
    echo "[entrypoint] 无缓存无种子，尝试在线下载（cloakbrowser 主源+镜像回退）"
    python -m cloakbrowser install || echo "[entrypoint] 在线下载失败，浏览器功能将不可用"
  fi
fi

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
