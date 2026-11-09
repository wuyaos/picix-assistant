# picix-assistant：jav.picix.us 每日任务自动化（Docker + FastAPI + Vue3）

## 目录地图
- `backend/app/`：FastAPI 后端（五阶段任务流水线、商城、TG 推送、调度），有局部 AGENTS.md
- `frontend/src/`：Vue3 前端（仪表盘分区式），有局部 AGENTS.md
- `docker/`：entrypoint、部署与终验脚本；`cloakbrowser-*.tgz` 为本地种子/备份，严禁入库
- `.smoke/`：前端 DOM 断言脚本与基准截图
- `data/`：运行时数据（SQLite + 配置，含登录 token），已被 .gitignore，勿删勿提交

## 命令
- 本地起后端：`cd backend && DATA_DIR=./data ../.venv/Scripts/python -m uvicorn app.main:app --port 8110`（Niubash 下用 `.venv/Scripts/python.exe`）
- 前端构建：`cd frontend && npm run build`（后端托管 `../frontend/dist`）
- 一键部署（WSL dockerd + 构建 + 终验）：`wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/deploy.sh"`
- 前端 DOM 断言：`.venv/Scripts/python.exe .smoke/dom_analysis.py`

## 规范
- 版本单一来源：`backend/app/version.py` 的 `APP_VERSION`，改版本只动这里；MINOR=功能/界面，PATCH=修复
- compose 的 `PORT` 环境变量必须与 `ports` 右侧一致（历史坑：曾硬编码 8000）
- 用户可见配置通过 Web UI（`/api/config`）管理；环境变量只放部署层关注项（代理、端口、缓存路径）

## 红线
- `data/`、`backend/data/`、`docker/*.tgz` 含会话 token 与账号数据：不提交、不打印内容、不随日志外传
- 所有 Picix API 请求必须经 CloakBrowser 传输层发出——纯 HTTP 客户端（httpx/curl_cffi）必被 CF 指纹质询 403，不要"优化"掉
- 不要改动已入库用户的 SQLite schema 兼容性；改 store 需考虑旧库平滑迁移
