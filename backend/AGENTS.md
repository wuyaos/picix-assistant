# backend：FastAPI 后端（任务流水线 / 商城 / Telegram / 调度）

## 入口
- `app/main.py`：REST API + 托管前端静态产物（`FRONTEND_DIST`）
- `app/service.py`：五阶段流水线（任务申领 → 资源购买 → 解锁 → 回读 → 推送）与 `plan_unlocks` 解锁决策
- `app/picix/client.py`：PicixClient（签名请求）；`signing.py`：ECDSA P-256（PICIX-PROOF / LOGIN-V1）；`transport.py`：CloakBrowser 单例传输
- `app/tg.py`：Telethon 扫码连接、自动重登、bot 推送；`app/mall.py`：商城分页/资源包检查/购买（单次请求 + 留痕）；`app/store.py`：SQLite（kv/config/runs/purchases）；`app/scheduler.py`：APScheduler cron + 启动补跑

## 契约
- API 面向 `frontend/src/api.js`：`/api/status`、`/api/login/*`、`/api/config`、`/api/run`（`{"force":true}` 越过当日跳过）、`/api/mall/*`、`/api/tg/qr/*`；改端点需同步前端 api.js
- 配置持久化在 SQLite kv，键清单见 README「配置项」；默认值集中在代码内，不另设配置文件
- 推送唯一通道是 bot（`tg.py` 的 bot_send）；用户 Telethon 会话只用于登录自动确认，勿往用户会话发通知

## 坑
- 登录 token 与浏览器签名绑定：缺失原签名头会被站点自动撤销会话（"Token 未能提供原浏览器签名"），签名密钥与 token 必须成对存取
- 双机并发 Telethon 同一会话触发 `AuthKeyDuplicated` 互踢——部署假设单实例运行
- 交易/解锁流程按"单部失败跳过下一个"设计，不要改成整体中止
- 商城购买以站点资源包列表为准：每轮先看可用次数够不够本轮解锁计划再买，支付请求只发一次（传输层不重放非 GET）；超时/5xx 记 unknown 终态，不对账、不阻塞下一轮，勿加回对账/待确认状态机
