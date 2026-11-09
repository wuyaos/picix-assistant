#!/bin/sh
# picix-assistant 启动入口：确保 CloakBrowser Chromium 就绪后启动服务
#
# 缓存布局（/opt/cloakbrowser 为命名卷，升级/重建镜像不重新下载）：
#   /opt/cloakbrowser/chromium-<版本>-pro/chrome
# 就绪策略（三级）：
#   1. 卷内已有对应版本二进制 -> 直接命中
#   2. 无缓存但挂载了种子包(/tmp/cb.tar.gz) -> 解包到卷（离线，约 5 秒）
#   3. 无缓存无种子 -> 从 GitHub Releases 直下免费包（无需 license；
#      CB_DOWNLOAD_URL 可覆盖下载地址；代理优先 HTTPS_PROXY，缺省复用 PICIX_PROXY）
set -e

CACHE="${CLOAKBROWSER_CACHE_DIR:-/opt/cloakbrowser}"
SEED_VER="${CB_SEED_VERSION:-146.0.7680.177.5}"
SEED_TAR="/tmp/cb.tar.gz"
# 下载代理：优先 HTTPS_PROXY，缺省复用 PICIX_PROXY（同一出口代理场景免重复配置）
export HTTPS_PROXY="${HTTPS_PROXY:-${PICIX_PROXY:-}}"
export HTTP_PROXY="${HTTP_PROXY:-${PICIX_PROXY:-}}"
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
    echo "[entrypoint] 无缓存无种子，尝试在线下载（GitHub Releases 直下，无需 license）"
    python - "$CACHE" "$SEED_VER" <<'PYEOF' || true
import os, sys, tarfile, time, urllib.request

cache, ver = sys.argv[1], sys.argv[2]
raw_url = f"https://github.com/CloakHQ/CloakBrowser/releases/download/chromium-v{ver}/cloakbrowser-linux-x64.tar.gz"
single = os.environ.get("CB_DOWNLOAD_URL")
if single:
    urls = [single]
else:
    prefixes = [
        p.strip().rstrip("/")
        for p in os.environ.get(
            "CB_MIRROR_PREFIXES",
            "https://ghfast.top/,https://ghproxy.net/,https://gh.llkk.cc/",
        ).split(",")
        if p.strip()
    ]
    urls = [raw_url] + [f"{p}/{raw_url}" for p in prefixes]
dest_dir = os.path.join(cache, f"chromium-{ver}-pro")
os.makedirs(dest_dir, exist_ok=True)
tmp = os.path.join(cache, ".cb-download.tar.gz")
last = None
plan = urls * 2
for attempt, url in enumerate(plan, 1):
    try:
        print(f"[entrypoint] 下载（{attempt}/{len(plan)}）: {url}")
        with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as f:
            total = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)
        if total < 50 * 1024 * 1024:
            raise RuntimeError(f"下载不完整（{total} 字节）")
        with tarfile.open(tmp, "r:gz") as tf:
            tf.extractall(dest_dir, filter="data")
        os.remove(tmp)
        chrome = os.path.join(dest_dir, "chrome")
        if not os.access(chrome, os.X_OK):
            os.chmod(chrome, 0o755)
        print(f"[entrypoint] Chromium 就绪: {chrome}")
        sys.exit(0)
    except Exception as exc:  # noqa: BLE001
        last = f"{url} -> {exc}"
        try:
            os.remove(tmp)
        except OSError:
            pass
        time.sleep(3)
print(f"[entrypoint] 全部下载源失败（最后错误: {last}），浏览器功能将不可用")
print("[entrypoint] 可选处理：compose 加 HTTPS_PROXY（缺省复用 PICIX_PROXY）/ 挂载种子包 /tmp/cb.tar.gz / CB_MIRROR_PREFIXES 自定义镜像前缀")
sys.exit(1)
PYEOF
  if [ -x "$DIR/chrome" ]; then
    export CLOAKBROWSER_BINARY_PATH="$DIR/chrome"
  fi
fi
fi

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
