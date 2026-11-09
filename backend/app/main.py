"""picix-assistant FastAPI 入口。

职责：REST API（状态/登录/配置/运行）+ 托管前端静态文件 + 生命周期管理
（Store、PicixClient、Scheduler）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .mall import buy_good, paginate_goods, paginate_packages
from . import tg as tg_service
from .picix.client import PicixClient, PicixError
from .scheduler import Scheduler
from .service import fetch_status, poll_login, run_daily, send_notify_test, start_login
from .store import Store

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("picix.main")

BACKEND_DIR = Path(__file__).resolve().parent.parent  # backend/
DATA_DIR = Path(os.getenv("DATA_DIR", BACKEND_DIR / "data"))
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", BACKEND_DIR.parent / "frontend" / "dist"))
PORT = int(os.getenv("PORT", "8110"))


class ConfigUpdate(BaseModel):
    schedule_time: str | None = Field(default=None, description="HH:MM")
    timezone: str | None = None
    unlock_list_ids: list[int] | None = None
    accept_task_codes: list[str] | None = None
    max_unlocks_per_run: int | None = Field(default=None, ge=1, le=50)
    monthly_unlocks_per_day: int | None = Field(default=None, ge=0, le=5, description="月度任务每日额外解锁节奏（0-5）")
    mall_enabled: bool | None = Field(default=None, description="固定购买开关")
    mall_good_id: int | None = Field(default=None, ge=0, description="固定购买商品 id，0=未设置")
    proxy: str | None = Field(default=None, description="http://host:port 或 socks5://…，空为直连")
    tg_api_id: int | None = Field(default=None, ge=0, description="Telegram API id（my.telegram.org）")
    tg_api_hash: str | None = Field(default=None, description="Telegram API hash")
    tg_auto_login: bool | None = Field(default=None, description="登录需确认时自动经 TG 会话发送")
    notify_target: str | None = Field(default=None, description="推送目标：me=保存的消息；@username=公开频道；纯数字=Telegram ID")



@asynccontextmanager
async def lifespan(app: FastAPI):
    store = Store(DATA_DIR)
    try:
        applied = store.apply_config_file()
        if applied:
            log.info("已从 config.json 合并配置键: %s", ", ".join(applied))
    except ValueError as e:
        log.error("config.json 未生效: %s", e)
    scheduler = Scheduler(store, lambda trigger, force=False: run_daily(store, build_client(store), trigger, force))
    scheduler.apply_config()
    scheduler.start()
    app.state.store = store
    app.state.store = store
    app.state.purchase_lock = asyncio.Lock()
    app.state.scheduler = scheduler
    log.info("picix-assistant 启动完成 data=%s", DATA_DIR)
    catchup = asyncio.create_task(scheduler.startup_catchup())
    yield
    catchup.cancel()
    scheduler.shutdown()


def resolve_proxy(store: Store) -> str:
    """运行时站点代理：环境变量 PICIX_PROXY 优先（部署级声明），否则用配置页保存值。

    只有 Picix 站点流量走它；TG/Telegram 会话不受影响。空串=直连。
    """
    env_proxy = os.getenv("PICIX_PROXY", "").strip()
    return env_proxy or (store.get_config().get("proxy") or "")


def build_client(store: Store) -> PicixClient:
    return PicixClient(proxy=resolve_proxy(store))

app = FastAPI(title="picix-assistant", lifespan=lifespan)


# ---------- API ----------

@app.get("/api/status")
async def api_status():
    store: Store = app.state.store
    async with build_client(store) as client:
        out = await fetch_status(store, client)
    sched: Scheduler = app.state.scheduler
    out["next_run"] = sched.next_run_time()
    session = store.get_session()
    out["schedule_time"] = store.get_config()["schedule_time"]
    return out


@app.post("/api/login/start")
async def api_login_start():
    store: Store = app.state.store
    async with build_client(store) as client:
        return await start_login(store, client)


@app.post("/api/login/poll")
async def api_login_poll():
    store: Store = app.state.store
    async with build_client(store) as client:
        return await poll_login(store, client)


@app.post("/api/logout")
async def api_logout():
    store: Store = app.state.store
    store.clear_session()
    store.delete("login_pending")
    return {"ok": True}




# ---------- Telegram 自动确认 ----------

@app.get("/api/tg/status")
async def api_tg_status():
    return await tg_service.status(app.state.store)


@app.get("/api/tg/peers")
async def api_tg_peers():
    try:
        return await tg_service.list_peers(app.state.store)
    except tg_service.TgError as e:
        raise HTTPException(400, str(e))

@app.post("/api/tg/qr/start")
async def api_tg_qr_start():
    try:
        return await tg_service.qr_start(app.state.store)
    except tg_service.TgError as e:
        raise HTTPException(400, str(e))
    except Exception as e:  # noqa: BLE001 — Telethon/网络错误原样透出
        raise HTTPException(502, f"Telegram 连接失败: {e!r}")


@app.post("/api/tg/qr/password")
async def api_tg_qr_password(body: dict[str, str]):
    try:
        return await tg_service.qr_password(app.state.store, body.get("password", ""))
    except tg_service.TgError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tg/qr/poll")
async def api_tg_qr_poll():
    try:
        return await tg_service.qr_poll(app.state.store)
    except tg_service.TgError as e:
        raise HTTPException(400, str(e))


@app.post("/api/tg/qr/cancel")
async def api_tg_qr_cancel():
    return await tg_service.qr_cancel()


@app.post("/api/tg/logout")
async def api_tg_logout():
    try:
        return await tg_service.logout(app.state.store)
    except tg_service.TgError as e:
        raise HTTPException(400, str(e))

@app.get("/api/config")
async def api_config_get():
    return app.state.store.get_config()


@app.put("/api/config")
async def api_config_put(update: ConfigUpdate):
    store: Store = app.state.store
    changes = update.model_dump(exclude_none=True)
    if "timezone" in changes:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(changes["timezone"])
        except Exception:
            raise HTTPException(400, f"无效时区: {changes['timezone']}")
    store.update_config(changes)
    app.state.scheduler.apply_config()
    return store.get_config()


class ProxyTestRequest(BaseModel):
    proxy: str | None = Field(default=None, description="不传则测当前生效代理（PICIX_PROXY 环境变量优先，其次配置页）；传空字符串测直连")


@app.post("/api/proxy/test")
async def api_proxy_test(req: ProxyTestRequest | None = None):
    store: Store = app.state.store
    proxy = req.proxy if (req and req.proxy is not None) else resolve_proxy(store)
    async with PicixClient(proxy=proxy) as client:
        result = await client.ping()
    result["proxy"] = proxy or "(直连)"
    return result


@app.get("/api/lists")
async def api_lists():
    """配置页选片单用：拉取用户片单列表。"""
    store: Store = app.state.store
    session = store.get_session()
    if not session:
        raise HTTPException(401, "未登录")
    async with build_client(store) as client:
        try:
            mine = await client.list_my_movie_lists(session)
            subs = await client.list_movie_list_subscriptions(session)
        except PicixError as e:
            raise HTTPException(502, str(e))
        # 订阅片单条目键是 listId，归一化为 id 供前端统一使用
        sub_items = subs.get("items") if isinstance(subs, dict) else None
        normalized = [
            {**it, "id": it.get("listId", it.get("id")), "subscribed": True}
            for it in (sub_items or [])
            if isinstance(it, dict)
        ]
        out = dict(mine) if isinstance(mine, dict) else {"mine": mine}
        out["subscriptions"] = normalized
        return out


class RunRequest(BaseModel):
    force: bool = Field(default=False, description="强制完整执行（跳过 skipped 判定，仍推送结果）")


@app.post("/api/run")
async def api_run(req: RunRequest | None = None):
    sched: Scheduler = app.state.scheduler
    try:
        return await sched.run_manual(force=bool(req.force) if req else False)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.post("/api/notify/test")
async def api_notify_test():
    store: Store = app.state.store
    try:
        return await send_notify_test(store)
    except tg_service.TgError as e:
        raise HTTPException(400, str(e))

@app.get("/api/runs")
async def api_runs(limit: int = 20):
    return app.state.store.list_runs(limit=min(limit, 100))


# ---------- 商城 ----------

def _require_session(store: Store) -> dict[str, Any]:
    session = store.get_session()
    if not session:
        raise HTTPException(401, "未登录")
    return session


@app.get("/api/mall/goods")
async def api_mall_goods():
    store: Store = app.state.store
    session = _require_session(store)
    async with build_client(store) as client:
        try:
            return {"items": await paginate_goods(client, session)}
        except PicixError as e:
            raise HTTPException(502, str(e))


@app.get("/api/mall/packages")
async def api_mall_packages():
    """资源包卡片：我的资源包 + 流量汇总。"""
    store: Store = app.state.store
    session = _require_session(store)
    async with build_client(store) as client:
        try:
            packages, traffic = await asyncio.gather(
                paginate_packages(client, session), client.traffic_summary(session)
            )
            return {"items": packages, "traffic": traffic}
        except PicixError as e:
            raise HTTPException(502, str(e))


class BuyRequest(BaseModel):
    good_id: int = Field(ge=1)


@app.post("/api/mall/buy")
async def api_mall_buy(req: BuyRequest):
    store: Store = app.state.store
    session = _require_session(store)
    session = _require_session(store)
    async with app.state.purchase_lock:
        async with build_client(store) as client:
            purchase = await buy_good(store, client, session, req.good_id)
    return purchase


@app.get("/api/mall/purchases")
async def api_mall_purchases(limit: int = 20):
    return app.state.store.list_purchases(limit=min(limit, 100))




# ---------- 前端静态托管 ----------

if FRONTEND_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        candidate = FRONTEND_DIST / path
        if path and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.resolve().parents:
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False)
