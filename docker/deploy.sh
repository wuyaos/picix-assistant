#!/bin/sh
# picix-assistant 一键部署：构建 → 启动 → 终验
# 前置：WSL dockerd 运行中（wsl -u root -e sh -c "nohup dockerd > /tmp/dockerd.log 2>&1 &"）
# 用法: wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/deploy.sh"
set -e
cd /mnt/d/work/project/person/picix-assistant
echo "== 1/3 构建 =="
docker compose build 2>&1 | tail -1
echo "== 2/3 启动 =="
docker compose up -d 2>&1 | tail -1
sleep 4
echo "== 3/3 终验 =="
sh /mnt/d/work/project/person/picix-assistant/docker/final_ui_check.sh
