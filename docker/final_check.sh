#!/bin/sh
# 最终验证（外部可复跑）：容器健康 + 关键路由 + 最新 run 终态
# 用法: wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/final_check.sh"
set -e
cd /mnt/d/work/project/person/picix-assistant

echo "== 1. 容器与版本 =="
docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null || docker ps --filter name=picix-assistant --format '{{.Names}} {{.Status}}'

echo "== 2. /api/status =="
curl -sS http://localhost:8000/api/status | docker exec -i picix-assistant python -c "
import sys, json
s = json.load(sys.stdin)
print('logged_in:', s.get('logged_in'), '| session_invalid:', s.get('session_invalid'), '| points:', s.get('points'), '| tasks:', len(s.get('tasks') or []))
assert s.get('logged_in') is True
"

echo "== 3. 关键路由 =="
for p in /api/config /api/lists /api/mall/packages /api/runs /api/tg/status; do
  code=$(curl -sS -o /dev/null -w '%{http_code}' "http://localhost:8000$p")
  echo "$p -> $code"
done

echo "== 4. 最新 run 终态 =="
docker exec picix-assistant python /tmp/check_run.py

echo "== 5. 前端产物 =="
curl -sS http://localhost:8000/ | grep -o 'index-[A-Za-z0-9_-]*\.js' | head -1
echo "ALL PASS"
