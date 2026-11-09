"""商城固定购买与资源包检查。

设计（用户确认的简化版）：
- 触发条件一条：Packages/listMine 无任何「未过期且有剩余额度」的资源包 → 购买固定商品
- 幂等：clientRequestId 先入库（purchases 表）再请求 payGood；
  超时/未知结果标记 unknown（「已受理但响应丢失」）。
  注意：不存在同键重试路径，真正的兜底是下一轮 ensure_package
  发现可用资源包则不再购买；unknown 记录由
  reconcile_pending_purchases() 用订单列表对账收敛。
"""

from __future__ import annotations

import logging
import secrets
import time
from datetime import datetime
from typing import Any

from .picix.client import PicixClient, PicixError
from .store import Store

log = logging.getLogger("picix.mall")


async def paginate_goods(client: PicixClient, session: dict[str, Any]) -> list[dict[str, Any]]:
    """商品为分组结构：{groups: [{name, goods: [...]}], nextCursor}。摊平返回。"""
    items: list[dict[str, Any]] = []
    cursor: Any = None
    for _ in range(20):
        resp = await client.mall_list_goods(session, after_id=cursor) if cursor is not None else await client.mall_list_goods(session)
        # groups 实为字典：{package: [商品...], inviteCode: [...], redeemCode: [...]}
        groups = resp.get("groups") or {}
        values = groups.values() if isinstance(groups, dict) else groups
        for goods in values:
            if isinstance(goods, list):
                items.extend(g for g in goods if isinstance(g, dict))
        nxt = resp.get("nextCursor")
        if nxt is None:
            break
        cursor = nxt
    return items


async def paginate_packages(client: PicixClient, session: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = None
    for _ in range(20):
        resp = await client.packages_list_mine(session, cursor=cursor)
        page = resp.get("items") if isinstance(resp, dict) else None
        if not isinstance(page, list):
            break
        items.extend(page)
        nxt = resp.get("nextCursor")
        if nxt is None:
            break
        cursor = nxt
    return items


def _expired(expired_at: Any) -> bool:
    """expiredAt 兼容 epoch 秒/毫秒与 ISO 字符串；空值视为永久。"""
    if expired_at in (None, "", 0):
        return False
    if isinstance(expired_at, (int, float)):
        v = float(expired_at)
        if v > 1e12:  # 毫秒
            v /= 1000.0
        return v <= time.time()
    try:
        dt = datetime.fromisoformat(str(expired_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt.timestamp() <= time.time()
    except ValueError:
        return False


def package_usable(p: dict[str, Any]) -> bool:
    """未过期且有剩余额度。"""
    if not isinstance(p, dict):
        return False
    if _expired(p.get("expiredAt")):
        return False
    try:
        total = float(p.get("total") or 0)
        used = float(p.get("used") or 0)
    except (TypeError, ValueError):
        return False
    if total <= 0:
        # total 缺失按无剩余处理，避免把未知包当可用
        return False
    return used < total


async def buy_good(
    store: Store,
    client: PicixClient,
    session: dict[str, Any],
    good_id: int,
) -> dict[str, Any]:
    """购买固定商品（幂等）。返回 purchases 表记录。"""
    good_name, price = "", None
    try:
        for g in await paginate_goods(client, session):
            if int(g.get("id") or 0) == int(good_id):
                good_name = str(g.get("name") or "")
                price = g.get("price")
                break
    except PicixError:
        pass  # 商品信息仅用于记录，拉不到不阻塞购买

    cri = secrets.token_hex(16)  # 16 字节随机 hex，与前端 clientRequestId 同格式
    purchase_id, is_new = store.create_purchase(cri, int(good_id), good_name, price)
    if not is_new:
        return store.get_purchase(purchase_id) or {}

    try:
        resp = await client.mall_pay_good(session, int(good_id), cri)
        order_code = ""
        if isinstance(resp, dict):
            order_code = str(
                resp.get("orderCode") or resp.get("code") or resp.get("id") or ""
            )
        store.finish_purchase(purchase_id, "success", order_code=order_code or None)
        log.info("购买成功 goodId=%s cri=%s…", good_id, cri[:8])
    except PicixError as e:
        # 超时/网关错误可能是「已受理但响应丢失」→ unknown，让用户核对订单后处理
        status = "unknown" if getattr(e, "status", 0) in (0, 502, 503, 504) else "failed"
        store.finish_purchase(purchase_id, status, error=str(e))
        log.warning("购买失败(%s) goodId=%s: %s", status, good_id, e)
    except Exception as e:  # noqa: BLE001
        store.finish_purchase(purchase_id, "unknown", error=repr(e))
        log.warning("购买异常 goodId=%s: %r", good_id, e)
    return store.get_purchase(purchase_id) or {}



async def reconcile_pending_purchases(
    store: Store,
    client: PicixClient,
    session: dict[str, Any],
) -> list[dict[str, Any]]:
    """对账 pending/unknown 购买记录，用订单列表收敛终态。

    匹配规则：订单 goodId 相同且 createdAt 与购买 created_at 相差 10 分钟内。
    返回仍无法判定的记录列表（本轮不买新包）。
    """
    unresolved: list[dict[str, Any]] = []
    pendings = [
        p for p in store.list_purchases(limit=50)
        if p.get("status") in ("pending", "unknown")
    ]
    if not pendings:
        return unresolved
    try:
        orders_resp = await client.mall_list_orders(session)
    except Exception:  # noqa: BLE001 - 对账失败不阻塞，保持原状
        log.warning("订单列表拉取失败，%d 条购买记录保持未决", len(pendings))
        return pendings
    orders = orders_resp.get("items") if isinstance(orders_resp, dict) else None
    if not isinstance(orders, list):
        # 订单结构不符合预期 → 不做破坏性判定，保持未决
        log.warning("订单列表结构异常，%d 条购买记录保持未决", len(pendings))
        return pendings
    for p in pendings:
        match = None
        for o in orders:
            if not isinstance(o, dict):
                continue
            try:
                if int(o.get("goodId") or 0) != int(p.get("good_id") or 0):
                    continue
            except (TypeError, ValueError):
                continue
            o_created = o.get("createdAt") or o.get("created_at") or 0
            try:
                o_ts = float(o_created)
                if o_ts > 1e12:  # 毫秒
                    o_ts /= 1000.0
            except (TypeError, ValueError):
                continue
            if abs(o_ts - float(p.get("created_at") or 0)) <= 600:
                match = o
                break
        if match is not None:
            code = str(match.get("orderCode") or match.get("code") or match.get("id") or "")
            store.finish_purchase(int(p["id"]), "success", order_code=code or None)
            log.info("对账收敛 purchase=%s → success (订单 %s)", p["id"], code)
        else:
            # 订单列表完整拉到但没有匹配 → 该次支付未成功
            store.finish_purchase(int(p["id"]), "failed", error="对账：订单列表无匹配记录")
            log.info("对账收敛 purchase=%s → failed", p["id"])
    return unresolved

async def ensure_package(
    store: Store,
    client: PicixClient,
    session: dict[str, Any],
    good_id: int,
) -> dict[str, Any]:
    """流水线商城步骤：无可用资源包 → 买固定商品。返回步骤摘要供 runs detail。"""
    out: dict[str, Any] = {"usable_packages": 0, "action": "none"}
    try:
        packages = await paginate_packages(client, session)
    except PicixError as e:
        out["action"] = "error"
        out["error"] = f"资源包列表拉取失败: {e}"
        return out
    usable = [p for p in packages if package_usable(p)]
    out["usable_packages"] = len(usable)
    if usable:
        out["action"] = "none"  # 还有可用包，不买
        return out

    if not good_id:
        out["action"] = "skipped"
        out["error"] = "无可用资源包但未配置 mall_good_id，跳过购买"
        return out

    purchase = await buy_good(store, client, session, good_id)
    out["action"] = "bought" if purchase.get("status") == "success" else purchase.get("status")
    out["purchase"] = purchase
    return out
