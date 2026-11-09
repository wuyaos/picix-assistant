"""Telegram 用户会话（Telethon）：扫码连接 + 自动向 @vStreamingBot 发送登录确认。

流程（一次性）：
  配置页填 api_id/api_hash（my.telegram.org 免费申请）→ 点「连接 Telegram」
  → 后端出二维码 → 用官方 Telegram App「设置 → 设备 → 链接桌面设备」扫码
  → StringSession 存入 kv:tg_session。

之后每次 Picix 登录（token 失效或手动重登）：tg_auto_login 开启时自动向
@vStreamingBot 发送 `/start login_<code>`，无需人工。

安全边界：StringSession = TG 账号完全控制权，明文存 /data/picix.db；
可在 TG「设置 → 设备」随时终止本会话（服务端登出见 logout()）。

Telethon 依赖懒加载：未安装 telethon 时 status() 等只读功能仍可用。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any
from urllib.parse import urlparse

from .store import Store

log = logging.getLogger("picix.tg")

TG_BOT = "vStreamingBot"
QR_MAX_RECREATES = 8  # 单个 token 约 30s，8 次轮换 ≈ 4 分钟扫码窗口


class TgError(Exception):
    """对用户可读的 Telegram 集成错误。"""


def _proxy_arg(cfg: dict[str, Any]):
    """复用全局 proxy 配置（http/socks5）。返回 pysocks 风格元组，Telethon 需要 python-socks。"""
    p = (cfg.get("proxy") or "").strip()
    if not p:
        return None
    u = urlparse(p)
    scheme = (u.scheme or "http").lower()
    ptype = {"socks4": 1, "socks5": 2, "http": 3, "https": 3}.get(scheme, 3)
    host = u.hostname
    if not host:
        raise TgError(f"代理地址无法解析: {p}")
    port = u.port or (1080 if "socks" in scheme else 8080)
    return (ptype, host, port, True)  # rdns=True: 域名在代理端解析


def _make_client(cfg: dict[str, Any], session_str: str = ""):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(cfg.get("tg_api_id") or 0)
    api_hash = (cfg.get("tg_api_hash") or "").strip()
    if not api_id or not api_hash:
        raise TgError("未配置 tg_api_id / tg_api_hash（在 my.telegram.org → API development tools 免费申请）")
    return TelegramClient(
        StringSession(session_str),
        api_id,
        api_hash,
        proxy=_proxy_arg(cfg),
        device_model="picix-assistant",
        auto_reconnect=False,
    )


# ---- 会话存取（kv: tg_session，等同 picix session 的信任域）----

def _get_session_str(store: Store) -> str:
    return (store.get("tg_session") or {}).get("string") or ""


def _fmt_me(me: Any) -> dict[str, Any]:
    names = " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")])).strip()
    return {"name": names or str(me.id), "username": me.username or "", "id": me.id}


async def _save_linked(store: Store, client: Any, me: Any) -> None:
    s = client.session.save()
    if not isinstance(s, str) or not s:
        raise TgError("会话保存失败（StringSession 为空）")
    store.put("tg_session", {"string": s, "account": _fmt_me(me), "linked_at": time.time()})
    log.info("TG 会话已保存 account=%s", _fmt_me(me)["name"])


async def status(store: Store) -> dict[str, Any]:
    cfg = store.get_config()
    linked = bool(_get_session_str(store))
    return {
        "configured": bool(int(cfg.get("tg_api_id") or 0) and (cfg.get("tg_api_hash") or "").strip()),
        "linked": linked,
        "auto_enabled": bool(cfg.get("tg_auto_login")),
        "account": (store.get("tg_session") or {}).get("account"),
        "qr_status": _qr.get("status") if _qr else None,
    }


# ---- 扫码登录（QRLogin 状态机）----
# _qr: {client, qr, task, status: waiting|authorized|error, url, account, error, updated_at, recreates}
_qr: dict[str, Any] = {}
_qr_lock = asyncio.Lock()


async def qr_start(store: Store) -> dict[str, Any]:
    cfg = store.get_config()
    async with _qr_lock:
        await _reset_locked()
        client = _make_client(cfg, _get_session_str(store))
        await client.connect()
        if await client.is_user_authorized():
            me = await client.get_me()
            if not _get_session_str(store):  # 兜底：会话有效但 kv 缺失
                await _save_linked(store, client, me)
            account = _fmt_me(me)
            await client.disconnect()
            return {"status": "authorized", "account": account}
        qr = await client.qr_login()
        _qr.update(
            {
                "client": client,
                "qr": qr,
                "task": None,
                "status": "waiting",
                "url": qr.url,
                "account": None,
                "error": None,
                "updated_at": time.time(),
                "recreates": 0,
            }
        )
        _qr["task"] = asyncio.create_task(_qr_wait_loop(store))
        return {"status": "waiting", "qr_url": _qr["url"], "qr_svg": _qr_svg(_qr["url"])}

async def _qr_wait_loop(store: Store) -> None:
    client, qr = _qr["client"], _qr["qr"]
    try:
        while True:
            try:
                if qr is None:
                    raise TgError("二维码状态已失效，请重新生成")
                me = await qr.wait()
                break
            except (asyncio.TimeoutError, TimeoutError):
                qr = await _recreate_or_fail(client)
                if qr is None:
                    break
            except Exception as e:  # noqa: BLE001 — 过期类错误轮换二维码，其余上抛
                if "EXPIRED" in str(e).upper() or "TOKEN" in str(e).upper():
                    qr = await _recreate_or_fail(client)
                    if qr is None:
                        break
                else:
                    raise
        me2 = await client.get_me()
        await _save_linked(store, client, me2)
        _qr["status"] = "authorized"
        _qr["account"] = _fmt_me(me2)
        with contextlib.suppress(Exception):
            await client.disconnect()
        _qr.pop("client", None)
        log.info("TG 扫码登录成功 account=%s", _qr["account"]["name"])
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — 状态机错误落到 UI
        if type(e).__name__ == "SessionPasswordNeededError" or "password is required" in str(e).lower():
            _qr["status"] = "waiting_password"
            _qr["error"] = "Telegram 已开启两步验证，请输入 Telegram 两步验证密码"
            log.info("TG 扫码完成，等待两步验证密码")
            return
        _qr["status"] = "error"
        _qr["error"] = str(e) or repr(e)
        with contextlib.suppress(Exception):
            await client.disconnect()
        _qr.pop("client", None)
        log.warning("TG 扫码登录失败: %r", e)


async def qr_password(store: Store, password: str) -> dict[str, Any]:
    """提交扫码后的 Telegram 两步验证密码；密码只在本次请求和内存中使用。"""
    if not password:
        raise TgError("两步验证密码不能为空")
    async with _qr_lock:
        if _qr.get("status") != "waiting_password" or not _qr.get("client"):
            raise TgError("当前没有等待两步验证密码的扫码会话")
        client = _qr["client"]
        try:
            await client.sign_in(password=password)
            me = await client.get_me()
            await _save_linked(store, client, me)
            _qr["status"] = "authorized"
            _qr["account"] = _fmt_me(me)
            with contextlib.suppress(Exception):
                await client.disconnect()
            _qr.pop("client", None)
            return {"status": "authorized", "account": _qr["account"]}
        except Exception as e:  # noqa: BLE001 — Telethon 密码错误类型跨版本兼容
            if "password" in str(e).lower() or "invalid" in str(e).lower():
                _qr["error"] = "两步验证密码错误，请重新输入"
                raise TgError(_qr["error"]) from e
            raise TgError(f"Telegram 两步验证失败：{e}") from e

async def _recreate_or_fail(client: Any):
    if _qr.get("recreates", 0) >= QR_MAX_RECREATES:
        raise TgError("二维码长时间未扫码已失效，请重新生成")
    qr = _qr["qr"]
    await qr.recreate()  # Telethon v1.45 原地更新 _resp，返回 None
    _qr["recreates"] = int(_qr.get("recreates", 0)) + 1
    if getattr(qr, "url", None) is None:
        raise TgError("二维码轮换失败，请重新生成")
    _qr["url"] = qr.url
    _qr["updated_at"] = time.time()
    log.info("QR token 已轮换（第 %d 次）", _qr["recreates"])
    return qr


async def qr_poll(store: Store) -> dict[str, Any]:
    st = {
        "status": _qr.get("status") or "idle",
        "qr_url": _qr.get("url"),
        "qr_svg": _qr_svg(_qr["url"]) if _qr.get("url") else None,
        "updated_at": _qr.get("updated_at"),
        "account": _qr.get("account"),
        "error": _qr.get("error"),
    }
    if st["status"] in ("authorized", "error", "idle"):
        st["done"] = True
        if st["status"] == "authorized":
            asyncio.create_task(_cleanup_done())
    return st


async def _cleanup_done() -> None:
    async with _qr_lock:
        await _reset_locked()


async def qr_cancel() -> dict[str, Any]:
    async with _qr_lock:
        await _reset_locked()
    return {"ok": True}


async def _reset_locked() -> None:
    task = _qr.get("task")
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    client = _qr.pop("client", None)
    _qr.clear()
    if client:
        with contextlib.suppress(Exception):
            await client.disconnect()


# ---- 自动发送登录确认 ----

async def send_login_confirm(store: Store, code: str) -> dict[str, Any]:
    """向 @vStreamingBot 发送 /start login_<code>（等价于用户点 t.me 深链）。"""
    cfg = store.get_config()
    session_str = _get_session_str(store)
    if not session_str:
        raise TgError("未连接 Telegram 会话（先在配置页扫码连接）")
    client = _make_client(cfg, session_str)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            store.delete("tg_session")
            raise TgError("Telegram 会话已失效，请在配置页重新扫码连接")
        entity = await client.get_entity(TG_BOT)
        await client.send_message(entity, f"/start login_{code}")
        log.info("已发送 TG 确认 /start login_%s", code)
        return {"sent": True}
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()



async def send_to_me(store: Store, text: str) -> dict[str, Any]:
    """发消息到「保存的消息」（自己），用作运行结果推送通道。"""
    cfg = store.get_config()
    session_str = _get_session_str(store)
    if not session_str:
        raise TgError("未连接 Telegram 会话（先在配置页扫码连接）")
    client = _make_client(cfg, session_str)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            store.delete("tg_session")
            raise TgError("Telegram 会话已失效，请在配置页重新扫码连接")
        me = await client.get_me()
        await client.send_message(me, text)
        log.info("已发送推送到保存的消息（%d 字）", len(text))
        return {"sent": True}
    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()

async def logout(store: Store) -> dict[str, Any]:
    """终止服务端会话并清除本地 StringSession。"""
    session_str = _get_session_str(store)
    if session_str:
        try:
            client = _make_client(store.get_config(), session_str)
            await client.connect()
            if await client.is_user_authorized():
                await client.log_out()
            with contextlib.suppress(Exception):
                await client.disconnect()
        except Exception as e:  # noqa: BLE001 — 服务端登出失败不影响本地清除
            log.warning("TG 服务端登出失败（仍清除本地会话）: %s", e)
    store.delete("tg_session")
    return {"ok": True}


# ---- 二维码渲染 ----

def _qr_svg(url: str) -> str:
    import io

    import qrcode
    import qrcode.image.svg

    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
