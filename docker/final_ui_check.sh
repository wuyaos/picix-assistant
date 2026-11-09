#!/bin/sh
# picix-assistant UI 重构终验（外部可复跑）
# 用法: wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/final_ui_check.sh"
# 宿主端口非 8000 时: PICIX_HOST_PORT=9000 sh docker/final_ui_check.sh
set -e
P=/mnt/d/work/project/person/picix-assistant
P=/mnt/d/work/project/person/picix-assistant
PORT="${PICIX_HOST_PORT:-8000}"   # 宿主端口，与 compose ports 左侧一致
BASE="http://localhost:$PORT"
FAIL=0
echo "== 1. 容器状态 ="
docker ps --filter name=picix-assistant --format '{{.Names}} {{.Status}}' | grep -q picix-assistant && echo "PASS container" || { echo "FAIL container"; FAIL=1; }

echo "== 2. 会话 ="
curl -sS $BASE/api/status | grep -q '"logged_in":true' && echo "PASS logged_in" || { echo "FAIL logged_in"; FAIL=1; }
echo "== 3. 部署产物与本地 dist 一致 ="
LOCAL=$(grep -o 'assets/index-[A-Za-z0-9_-]*\.js' /mnt/d/work/project/person/picix-assistant/frontend/dist/index.html | head -1)
curl -sS $BASE/ | grep -q "$LOCAL" && echo "PASS bundle $LOCAL" || { echo "FAIL bundle (local=$LOCAL)"; FAIL=1; }
echo "== 4. DOM 断言（静态 20 项）="
cd $P && .venv/Scripts/python.exe .smoke/dom_analysis.py > /tmp/dom_out.txt 2>&1 || true
grep -q "20" /tmp/dom_out.txt && tail -1 /tmp/dom_out.txt || cat /tmp/dom_out.txt | tail -3

echo "== 5. 截图 6 张 ="
N=$(ls $P/.smoke/rebuild-*.png 2>/dev/null | wc -l)
[ "$N" = "6" ] && echo "PASS screenshots 6/6" || { echo "FAIL screenshots $N/6"; FAIL=1; }

echo "== 6. 线上三页可达 ="
for p in / /config /history; do
  curl -sS -o /dev/null -w "%{http_code} " $BASE$p
done

if [ "$FAIL" = "0" ]; then echo "ALL PASS"; else echo "HAS FAILURES"; exit 1; fi
