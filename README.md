# picix-assistant

Picix（jav.picix.us）每日任务自动化助手：独立 Docker 应用，Web UI 管理登录、任务进度、资源包与解锁策略。

## 功能

- **登录管理**：一键生成登录码与 `t.me` 深链，浏览器里点一次 Telegram 确认即完成登录（ECDSA P-256 请求签名，密钥与 token 全程本地存储）；401 自动恢复（Telethon 自动发确认码）
- **每日任务**：定时执行五阶段流水线——任务申领 → 资源购买 → 解锁 → 回读 → Telegram 推送；`keepalive`/`daily` 任务模板（仅前端预填配置）
- **解锁策略**：公式化决策 `min(每日1 + 月度节奏X, 单次上限)`，全部完成则跳过；月度截止前 ≤3 天自动冲刺补解；单部失败跳过继续下一部（不消耗额度）
- **商城自动化**：无可用资源包时自动购买固定商品（幂等键 + 事后对账收敛 pending 订单）；购买记录可追溯
- **状态看板**：仪表盘分区式 UI（吸顶导航 + 仪表头条 + 任务/资源包双列 + 最低消费横条 + 今日运行）；配置页 7 分区锚点导航 + 吸底保存
- **消息通知**：Telegram 保存的消息（复用扫码连接的 Telethon 会话，零配置）；运行结果、会话失效、消费临界（每结算周期 1 条）、购买结果、冲刺提醒，全部带去重
- **代理**：支持 http/socks5 代理（站点连通性测试按钮）

## CF 反爬与 CloakBrowser

站点 API（`/api/adult/*`）被 Cloudflare 按 TLS/HTTP2 指纹强制质询：**任何纯 HTTP 客户端（httpx/curl_cffi）都会拿到 403「Just a moment」，即使复刻浏览器 cf_clearance + UA 全套请求头也不行**。

因此所有 API 请求经 [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)（源码级指纹隐身 Chromium，Playwright 兼容 API）发出：浏览器导航到站点页面后，在页面上下文内 `fetch`，签名头原样携带。

- Chromium 二进制**不打进镜像**：首次启动由 `docker/picix-entrypoint.sh` 就绪到命名卷
  `cloakbrowser_cache`（映射 `/opt/cloakbrowser`），升级/重建镜像不重新下载。就绪策略三级：
  卷缓存命中 → 种子包解包 → 在线下载回退（`CLOAKBROWSER_DOWNLOAD_URL` 主源 + GitHub Releases
  自动回退，国内可把主源配成镜像地址）。
- 种子包默认**不挂载**（compose 保持干净）。仅离线首次部署时临时加一行挂载：

  ```yaml
  volumes:
    - ./docker/cloakbrowser-linux-x64.tar.gz:/tmp/cb.tar.gz:ro   # 空卷时自动解包播种
  ```

  种子包准备（GitHub 直连/代理不通时用镜像前缀下载；本机卷已播种，无需再下）：

  ```bash
  cd docker
  curl -L -C - -o cloakbrowser-linux-x64.tar.gz \
    'https://ghfast.top/https://github.com/CloakHQ/CloakBrowser/releases/download/chromium-v146.0.7680.177.5/cloakbrowser-linux-x64.tar.gz'
  sha256sum cloakbrowser-linux-x64.tar.gz   # 应与官方 SHA256SUMS 一致
  ```

- `PICIX_HEADLESS=1`（默认）headless 运行；若被 CF 识别改 `0`，由 xvfb 兜底 headed 模式

## 部署流程

```bash
# 前置（WSL 无 systemd，重启后需手动拉起 dockerd）
wsl -u root -e sh -c "nohup dockerd > /tmp/dockerd.log 2>&1 &"

# 一键部署（构建 → 启动 → 终验 ALL PASS）
wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/deploy.sh"
```

### 从远程镜像部署（无需克隆代码/本地构建）

```bash
# compose 已参数化镜像名，docker/login 后：
echo "$GITHUB_PAT" | docker login ghcr.io -u wuyaos --password-stdin   # 私有包需要；包转公开可跳过
docker pull ghcr.io/wuyaos/picix-assistant:1.0.0
PICIX_IMAGE=ghcr.io/wuyaos/picix-assistant:1.0.0 docker compose up -d --no-build
```

首次启动会自动解包/下载 Chromium 到卷（约 1-2 分钟），之后秒级。数据在 `./data`，升级仅换镜像 tag。

镜像约 0.67GB（Chromium 已移到持久卷，不再打进镜像）：`frontend` 多阶段构建（node:22-alpine → python:3.12-slim）；apt 层已清 lists。体积构成：Python 依赖 ~208MB、apt 运行库 ~350MB、基础镜像 ~80MB。Chromium ~730MB 存于命名卷 `cloakbrowser_cache`（`docker volume ls` 可见），升级镜像/`up -d` 不会重新下载；仅当卷被删且无种子包时才走在线下载（主源 `CLOAKBROWSER_DOWNLOAD_URL` + GitHub Releases 回退）。
## 环境变量

应用侧（镜像内已给默认值，一般无需设置）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `DATA_DIR` | `/data` | SQLite/配置目录（compose 映射 `./data`） |
| `FRONTEND_DIST` | `/app/static` | 前端静态产物目录 |
| `PORT` | `8000` | **启动端口**（uvicorn 监听）；compose `environment.PORT` 定义，须与 `ports` 右侧一致；改宿主端口只动 `ports` 左侧 |
| `LOG_LEVEL` | `INFO` | 后端日志级别，默认即可 |
| `PICIX_HEADLESS` | `1` | 浏览器 headless；被 CF 拦时改 `0`（xvfb headed 兜底） |
| `TZ` | 容器默认 UTC | compose 已设 `Asia/Shanghai`，影响日志时间戳 |

CloakBrowser 层：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `CLOAKBROWSER_CACHE_DIR` | `/opt/cloakbrowser` | 二进制缓存卷（`cloakbrowser_cache`），必挂 |
| `CB_SEED_VERSION` | `146.0.7680.177.5`（entrypoint 内置） | 种子版本号，决定卷内 `chromium-<版本>-pro` 子目录名；升级时同步改 |
| `CLOAKBROWSER_BINARY_PATH` | 未设（entrypoint 运行时自动填充） | 指定后跳过全部缓存/下载解析，回滚旧版本用 |
| `CLOAKBROWSER_DOWNLOAD_URL` | `cloakbrowser.dev` | 在线下载主源（失败自动回退 GitHub Releases）；国内可配镜像地址 |
| `PICIX_PROXY` | 未设 | **站点运行时代理**（优先于配置页；设了则 UI 修改不生效，清掉注释即回到 UI 管理）。只影响 Picix 流量，TG 不走它 |


构建代理：compose `build.args` 从项目根 `.env` 读取 `HTTP_PROXY`/`HTTPS_PROXY`，仅构建期生效（pip 拉依赖），运行时不携带；不需要代理时把 `.env` 里两行清空即可。

## 版本

单一来源：`backend/app/version.py` 的 `APP_VERSION`。改完重建部署，顶栏品牌旁与 `/api/status.version` 自动带出。语义化版本：MINOR=功能/界面迭代，PATCH=修复。当前 `v1.0.0`。

## 快速开始

```bash
docker compose up -d --build
# 浏览器打开 http://localhost:8000
```

1. 「配置」页设置代理并点「测试」（如 `http://192.168.123.1:7890`）
2. 「配置」页 Telegram 卡片扫码连接（通知与自动重登依赖它）→「开始登录」→ 打开 `t.me` 链接 → Telegram 里发送确认 → 页面自动完成
3. 「拉取站点数据」解析任务/片单/商品 → 选片单、按需调整解锁策略 → 保存 → 「立即执行」验证一轮

## 数据

全部状态在 SQLite（`./data/picix.db`，容器内 `/data`）：登录会话、密钥、配置、购买记录、执行历史。备份即备份该目录。

## 配置项

| 键 | 默认 | 说明 |
|---|---|---|
| `schedule_time` | `09:00` | 每日执行时间（HH:MM），错过启动补跑 |
| `timezone` | `Asia/Shanghai` | 调度时区 |
| `accept_task_codes` | `D_UL_1, M_UL_50, M_UL_ML_20, M_UL_ML_50` | 自动领取的任务类型 |
| `unlock_list_ids` | `[]` | 解锁影片来源片单（按顺序取未解锁，含订阅片单） |
| `monthly_unlocks_per_day` | `1` | 月度任务每日额外解锁节奏（0-5，0=纯搭车） |
| `max_unlocks_per_run` | `2` | 单次解锁上限（冲刺时封顶） |
| `mall_enabled` | `false` | 无可用资源包时自动购买 |
| `mall_good_id` | `0` | 固定购买的商品 ID |
| `proxy` | 空 | http/socks5 代理，空为直连 |
| `tg_auto_login` | `false` | 会话失效后自动重登（会顶掉浏览器登录） |

## 验证

```bash
# 线上终验（容器/会话/产物/DOM 20 断言/截图/三页可达）
wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/final_ui_check.sh"

# 后端终验
wsl -u root -e sh -c "sh /mnt/d/work/project/person/picix-assistant/docker/final_check.sh"

# 前端 DOM 断言 + 双端截图（本地静态预览，产物 frontend/dist）
.venv/Scripts/python.exe .smoke/dom_analysis.py
```

## 本地开发

```bash
# 后端
cd backend && python -m venv ../.venv
../.venv/Scripts/pip install -r requirements.txt
DATA_DIR=./data ../.venv/Scripts/python -m uvicorn app.main:app --port 8110

# 前端
cd frontend && npm install && npm run build   # 后端默认托管 ../frontend/dist
```

API：`GET /api/status`、`POST /api/login/start|poll|logout`、`GET/PUT /api/config`、`GET /api/lists`、`POST /api/run`（body `{"force":true}` 越过跳过）、`GET /api/runs`、`POST /api/proxy/test`、`GET /api/mall/goods|packages|purchases`、`POST /api/mall/buy`、`POST /api/notify/test`、`GET/POST /api/tg/qr/*`。

## 项目结构

```
├── backend/app/            # FastAPI
│   ├── picix/client.py     # PicixClient（签名请求，走 CloakBrowser）
│   ├── picix/signing.py    # ECDSA P-256 请求签名（PICIX-PROOF/LOGIN-V1）
│   ├── picix/transport.py  # CloakBrowser 单例传输层（CF 质询自动过）
│   ├── service.py          # 五阶段流水线 + plan_unlocks + 推送
│   ├── mall.py             # 商城分页/购买幂等/订单对账
│   ├── tg.py               # Telethon：扫码连接/自动重登/发通知
│   ├── scheduler.py        # APScheduler cron + 启动补跑
│   ├── store.py            # SQLite（kv/config/runs/purchases）
│   └── main.py             # REST API + 托管前端
├── frontend/src/           # Vue3（仪表盘分区式，设计令牌 style.css）
│   └── components/         # StatusHeader/TasksCard/PackagesCard/ConsumptionCard/
│                           # ConfigView/TelegramCard/LoginCard/HistoryView
├── docker/                 # cloakbrowser 二进制 + 终验脚本
├── .smoke/                 # dom_analysis.py（20 断言）+ rebuild-*.png 基准截图
└── data/                   # 运行时数据（SQLite，勿删）
```
