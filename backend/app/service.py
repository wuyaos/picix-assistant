"""登录服务与每日任务流水线。

登录流程（需用户点一次 Telegram 确认）：
  1. POST /Users/getLoginCode（不签名）→ {code}
  2. 用户打开 https://t.me/vStreamingBot?start=login_<code> 确认
  3. POST /Users/checkLoginCode {code}（login 签名）轮询 → {token, sessionId}
  4. 保存 session（session proof 与 login proof 用同一把 P-256 私钥，
     proofKeyId 换成 sessionId，服务端已通过 proofPublicKey 绑定）

每日流水线（顺序，严格五阶段）：
  ① 任务申领 → ② 资源购买（先对账未决记录，再临界区购买）→
  ③ 解锁（plan_unlocks 公式决策，错误三分类）→ ④ 回读（进度+积分+最低消费）→
  ⑤ 消息推送（TG 保存的消息；skipped 不推）。

解锁决策公式（）：
  daily = 1（每日任务当期未完成时）
  monthly = monthly_unlocks_per_day（月度任务当期未完成时，默认 1，0-5）
  total = min(daily + monthly, max_unlocks_per_run)   # cap 恒定生效
  冲刺：月度 periodEndsAt 剩余自然日 <= 3 且缺口 > 0 且未过期
        → total = max(total, min(cap, 缺口))
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .mall import ensure_package, reconcile_pending_purchases
from .tg import TgError, bot_get_chat_id, bot_send, send_login_confirm
from .picix.client import PicixClient, PicixError
from .picix.signing import new_proof_key_id
from .picix.transport import TransportUnknown
from .store import Store
from .version import APP_VERSION

log = logging.getLogger("picix.service")

TG_BOT = "vStreamingBot"
CODE_TTL_SECONDS = 600  # 前端 V 常量同量级；过期后重新取码
UNLOCK_TASK_PREFIXES = ("D_UL", "M_UL")
SHANGHAI = timezone(timedelta(hours=8))

# 购买临界区：自动补包与手动 /api/mall/buy 共享，防止并发重复扣款
_purchase_lock = asyncio.Lock()


# ---------- 登录 ----------

def build_tg_url(code: str) -> str:
    return f"https://t.me/{TG_BOT}?start=login_{code}"


async def start_login(store: Store, client: PicixClient) -> dict[str, Any]:
    pending = await client.get_login_code()
    pending["issued_at"] = time.time()
    store.put("login_pending", pending)
    return {
        "code": pending["code"],
        "tg_url": build_tg_url(pending["code"]),
        "ttl_seconds": CODE_TTL_SECONDS,
    }


async def poll_login(store: Store, client: PicixClient) -> dict[str, Any]:
    """轮询一次登录结果。返回 {status: pending|confirmed|none|expired, ...}"""
    session = store.get_session()
    pending = store.get_login_pending()
    if session and not pending:
        return {"status": "confirmed", "logged_at": session.get("logged_at")}
    if not pending:
        return {"status": "none"}
    if time.time() - pending["issued_at"] > CODE_TTL_SECONDS:
        store.delete("login_pending")
        return {"status": "expired"}
    try:
        resp = await client.check_login_code(pending["code"], pending["priv_pem"])
    except PicixError as e:
        log.warning("checkLoginCode 失败: %s", e)
        return {
            "status": "pending",
            "code": pending["code"],
            "tg_url": build_tg_url(pending["code"]),
            "error": str(e),
        }
    token = resp.get("token") if isinstance(resp, dict) else None
    session_id = resp.get("sessionId") if isinstance(resp, dict) else None
    if token and session_id:
        store.save_session(
            {
                "token": str(token),
                "session_id": str(session_id),
                "priv_pem": pending["priv_pem"],
                "proof_key_id": pending.get("proof_key_id") or new_proof_key_id(),
            }
        )
        store.delete("login_pending")
        store.delete("session_invalid")  # 新会话生效，解除失效标记
        log.info("登录成功 sessionId=%s…", str(session_id)[:8])
        return {"status": "confirmed", "logged_at": store.get_session().get("logged_at")}
    auto = await _tg_autosend(store, pending)
    out = {"status": "pending", "code": pending["code"], "tg_url": build_tg_url(pending["code"])}
    out.update(auto)
    return out


def mask_token(token: str) -> str:
    return token[:8] + "…" + token[-4:] if len(token) > 16 else "***"


async def _tg_autosend(store: Store, pending: dict[str, Any]) -> dict[str, Any]:
    """未确认时按需自动发送 TG 确认（每个登录码只发一次；失败降级为手动链接）。"""
    if pending.get("tg_sent") or pending.get("tg_error"):
        return {"tg_sent": bool(pending.get("tg_sent")), "tg_error": pending.get("tg_error")}
    if not store.get_config().get("tg_auto_login"):
        return {}
    try:
        await send_login_confirm(store, pending["code"])
    except TgError as e:
        pending["tg_error"] = str(e)
        store.save_login_pending(pending)
        log.warning("TG 自动发送失败: %s", e)
        return {"tg_error": str(e)}
    pending["tg_sent"] = True
    pending["tg_sent_at"] = time.time()
    store.save_login_pending(pending)
    return {"tg_sent": True}


async def auto_relogin(store: Store, client: PicixClient) -> bool:
    """401 后自动恢复：取登录码 → TG 发确认 → 轮询（最多 90s）。

    用户手动登录进行中（pending 新鲜且非本次自动产生）时不覆盖，
    避免顶掉用户正在确认的链接；发送成功即置 tg_sent，防止
    _tg_autosend 在轮询中重复发送同一条确认。
    """
    if not store.get_config().get("tg_auto_login"):
        return False
    existing = store.get_login_pending()
    if (
        existing
        and not existing.get("from_relogin")
        and time.time() - existing.get("issued_at", 0) <= CODE_TTL_SECONDS
    ):
        log.info("检测到用户手动登录进行中，跳过自动重登")
        return False
    try:
        pending = await client.get_login_code()
        pending["issued_at"] = time.time()
        pending["from_relogin"] = True
        store.put("login_pending", pending)
        await send_login_confirm(store, pending["code"])
        pending["tg_sent"] = True
        pending["tg_sent_at"] = time.time()
        store.save_login_pending(pending)
    except (TgError, PicixError) as e:
        log.warning("自动重新登录失败: %s", e)
        return False
    for _ in range(30):
        await asyncio.sleep(3)
        r = await poll_login(store, client)
        if r.get("status") == "confirmed":
            return True
        if r.get("status") in ("expired", "none"):
            return False
    return False


# ---------- 任务周期与解锁计划（）----------

def _task_claimed(task: dict[str, Any]) -> bool:
    return bool(task.get("progress"))


def _task_done(task: dict[str, Any]) -> bool:
    """当期完成判定：finishedAt 非零，或 progress >= target。"""
    p = task.get("progress") or {}
    if p.get("finishedAt"):
        return True
    try:
        prog = float(p.get("progress") or 0)
        tgt = float(p.get("target") or 0)
    except (TypeError, ValueError):
        return False
    return tgt > 0 and prog >= tgt


def _monthly_state(tasks: list[dict[str, Any]]) -> tuple[bool, int, float | None]:
    """扫描月度任务：返回 (是否有未完成, 最大缺口, 最早到期时间戳)。"""
    need = False
    deficit = 0
    end: float | None = None
    for t in tasks:
        code = str(t.get("code") or "")
        if not code.startswith("M_UL"):
            continue
        if _task_done(t):
            continue
        if not _task_claimed(t):
            # 未申领的月度任务不计需求（申领失败由流水线记错误）
            continue
        need = True
        p = t.get("progress") or {}
        try:
            tgt = float(p.get("target") or 0)
            prog = float(p.get("progress") or 0)
            deficit = max(deficit, int(math.ceil(tgt - prog)) if tgt > 0 else 1)
        except (TypeError, ValueError):
            deficit = max(deficit, 1)
        pe = p.get("periodEndsAt") or t.get("periodEndsAt")
        if isinstance(pe, (int, float)):
            v = float(pe)
            if v > 1e12:  # 毫秒
                v /= 1000.0
            end = min(end, v) if end is not None else v
    return need, deficit, end


def plan_unlocks(tasks: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, Any]:
    """解锁决策公式。返回 {total, daily, monthly, cap, sprint, reason}。"""
    daily_need = False
    for t in tasks:
        code = str(t.get("code") or "")
        if code.startswith("D_UL") and _task_claimed(t) and not _task_done(t):
            daily_need = True
            break
    monthly_need, monthly_deficit, monthly_end = _monthly_state(tasks)

    cap = int(cfg.get("max_unlocks_per_run") or 2)
    try:
        pace = int(cfg.get("monthly_unlocks_per_day") or 0)
    except (TypeError, ValueError):
        pace = 0
    pace = max(0, min(5, pace))
    daily = 1 if daily_need else 0
    monthly = pace if monthly_need else 0
    total = min(daily + monthly, cap)
    reason = f"每日{daily}+月度{monthly}"
    sprint = False
    if monthly_need and monthly_end is not None:
        now = time.time()
        if now < monthly_end:  # 已过期周期不触发冲刺
            days_left = math.ceil((monthly_end - now) / 86400)
            if days_left <= 3 and monthly_deficit > 0:
                total = max(total, min(cap, monthly_deficit))
                sprint = True
                reason += f"｜冲刺：剩{days_left}天 缺口{monthly_deficit}"
    return {
        "total": total,
        "daily": daily,
        "monthly": monthly,
        "cap": cap,
        "sprint": sprint,
        "reason": reason,
    }


def _finalize(detail: dict[str, Any], plan: dict[str, Any] | None, force: bool = False) -> str:
    """终态归并（）。"""
    errors = detail["errors"]
    done = int(detail.get("confirmed_unlocks") or 0)
    total = int((plan or {}).get("total") or 0)
    has_action = bool(detail["unlocks"] or detail["accepted"] or detail.get("mall"))
    if not force and total == 0 and not has_action and not errors:
        return "skipped"
    if errors and not has_action:
        return "failed"
    if errors or (total > 0 and done < total):
        return "partial"
    return "success"


# ---------- 推送（）----------

def _shanghai_date() -> str:
    return datetime.now(SHANGHAI).strftime("%Y-%m-%d")


async def _push(store: Store, text: str) -> bool:
    """经 Bot 推送（唯一通道）。失败返回 False（不抛，不阻塞流水线结果）。"""
    try:
        await bot_send(store, text)
        return True
    except Exception as e:  # noqa: BLE001 — 推送失败只记日志
        log.warning("推送失败: %s", e)
        return False


async def _notify_session_invalid(store: Store, recovered: bool) -> None:
    """会话失效提醒：每自然日最多 1 条（claim_kv 原子占位）。"""
    key = f"notify:session:{_shanghai_date()}"
    if not store.claim_kv(key):
        return
    if recovered:
        text = "Picix 会话已自动恢复，流水线继续。"
    else:
        text = "Picix 会话失效且自动恢复失败，请到面板重新登录（开启自动重登可自动恢复）。"
    await _push(store, text)


async def _notify_consumption(store: Store, mc: dict[str, Any] | None) -> None:
    """最低消费临界提醒：每结算周期 1 条，键用 nextSettlementAt 原值。"""
    if not isinstance(mc, dict) or not mc:
        return
    shortfall = mc.get("estimatedShortfall")
    nxt = mc.get("nextSettlementAt")
    if shortfall is None or not nxt:
        return
    try:
        if float(shortfall) <= 0:
            return
        nxt_v = float(nxt)
        if nxt_v > 1e12:  # 毫秒
            nxt_v /= 1000.0
    except (TypeError, ValueError):
        return
    days = (nxt_v - time.time()) / 86400
    if days > 3:
        return
    key = f"notify:consume:{nxt}"
    if not store.claim_kv(key):
        return
    await _push(store, f"最低消费临界：距结算不足 3 天，预估缺口 {shortfall} 分，请补足。")


def _fmt_summary(detail: dict[str, Any], status: str) -> str:
    """运行结果汇总文本（skipped 不推送，不会走到这里）。"""
    lines = [f"Picix 运行结束 [{status}] trigger={detail.get('trigger')}"]
    plan = detail.get("plan") or {}
    if plan:
        lines.append(
            f"计划 {plan.get('total')}（{plan.get('reason')}）｜确认解锁 {detail.get('confirmed_unlocks')}"
        )
    if detail["accepted"]:
        lines.append("领取: " + ", ".join(detail["accepted"]))
    for u in detail["unlocks"][:10]:
        mark = "[OK]" if u.get("ok") else "[X]"
        kind = f"({u.get('kind')})" if u.get("kind") else ""
        lines.append(f"解锁 {mark} movieId={u.get('movieId')} {kind}")
    if len(detail["unlocks"]) > 10:
        lines.append(f"…共 {len(detail['unlocks'])} 条解锁记录")
    mall = detail.get("mall")
    if isinstance(mall, dict):
        lines.append(f"商城: {mall.get('action')} 可用包 {mall.get('usable_packages')}")
    if detail.get("points") is not None:
        lines.append(f"积分: {detail.get('points')}")
    if detail["errors"]:
        lines.append("错误(" + str(len(detail["errors"])) + "): " + "; ".join(detail["errors"][:5]))
    return "\n".join(lines)


async def send_notify_test(store: Store) -> dict[str, Any]:
    """测试推送端点：不消耗去重键；异常向上抛（端点返回 400 + 原因）。"""
    await bot_send(store, "Picix 助手测试推送：通道正常。")
    return {"sent": True}


# ---------- 401 统一恢复（）----------

async def _handle_auth_failure(store: Store, client: PicixClient, detail: dict[str, Any]) -> bool:
    """标记会话失效（不清除，保留旧会话供人工核对），尝试自动恢复并推送提醒。"""
    store.put("session_invalid", time.time())
    detail["errors"].append("会话失效（401）")
    recovered = await auto_relogin(store, client)
    if recovered:
        detail["steps"].append("TG 自动重新登录成功")
    else:
        detail["errors"].append("自动恢复失败，请手动登录")
    await _notify_session_invalid(store, recovered)
    return recovered


# ---------- 每日流水线 ----------

async def run_daily(
    store: Store, client: PicixClient, trigger: str = "manual", force: bool = False
) -> dict[str, Any]:
    """执行一次每日流程，返回 detail 结构供 runs 表与 UI 展示。

    401 恢复语义：恢复成功后整轮重试一次（auth_budget 控制，最多一次）；
    恢复失败则本轮 failed 并推送会话提醒。
    """
    cfg = store.get_config()
    run_id = store.start_run(trigger)
    detail: dict[str, Any] = {
        "run_id": run_id,
        "trigger": trigger,
        "force": force,
        "steps": [],
        "unlocks": [],
        "errors": [],
        "accepted": [],
    }
    session = store.get_session()
    if not session:
        detail["errors"].append("未登录，跳过执行")
        store.finish_run(run_id, "skipped", detail)
        return detail

    plan: dict[str, Any] | None = None
    status = "failed"
    auth_budget = 2  # 每轮 401 恢复预算（恢复后整轮重试一次）
    try:
        for _attempt in range(2):
            session = store.get_session()
            if not session:
                detail["errors"].append("会话丢失，终止本轮")
                break
            try:
                plan, status = await _pipeline(store, client, session, cfg, detail, force)
                break
            except PicixError as e:
                if e.status == 401 and auth_budget > 0:
                    auth_budget -= 1
                    if await _handle_auth_failure(store, client, detail):
                        detail["steps"].append("会话已恢复，整轮重试")
                        continue
                raise
    except PicixError as e:
        detail["errors"].append(f"API 错误: {e}")
        status = "failed"
    except TransportUnknown as e:
        detail["errors"].append(f"传输未知错误: {e}")
        status = "failed"
    except Exception as e:  # noqa: BLE001 — 单次运行失败要完整记录，不让调度器静默
        detail["errors"].append(f"意外错误: {e!r}")
        status = "failed"

    detail["plan"] = plan
    detail["status"] = status

    # ⑤ 推送（skipped 不推；force 时即便 skipped 判定也推）
    if status != "skipped":
        text = _fmt_summary(detail, status)
        if plan and plan.get("sprint"):
            text = "【冲刺】" + text
        if await _push(store, text):
            detail["notified"] = True
        await _notify_consumption(store, detail.get("minimum_consumption"))

    store.finish_run(run_id, status, detail)
    log.info(
        "运行结束 status=%s unlocks=%d errors=%d",
        status,
        len(detail["unlocks"]),
        len(detail["errors"]),
    )
    return detail


async def _pipeline(
    store: Store,
    client: PicixClient,
    session: dict[str, Any],
    cfg: dict[str, Any],
    detail: dict[str, Any],
    force: bool,
) -> tuple[dict[str, Any] | None, str]:
    """五阶段流水线主体。返回 (plan, status)。抛 401 PicixError 由上层恢复重试。"""
    # ① 任务申领
    tasks = await _collect_tasks_async(client, session)
    detail["steps"].append(f"任务列表共 {len(tasks)} 项")
    accept_codes = set(cfg.get("accept_task_codes") or [])
    for t in tasks:
        code = str(t.get("code") or "")
        if not t.get("progress") and code in accept_codes:
            try:
                await client.accept_task(session, code)
                detail["accepted"].append(code)
                log.info("领取任务 %s", code)
            except PicixError as e:
                detail["errors"].append(f"领取 {code} 失败: {e}")
    if detail["accepted"]:
        tasks = await _collect_tasks_async(client, session)

    # ② 资源购买（先对账未决记录，再临界区购买）
    if cfg["mall_enabled"]:
        async with _purchase_lock:
            unresolved = await reconcile_pending_purchases(store, client, session)
            if unresolved:
                detail["errors"].append(
                    f"存在 {len(unresolved)} 条未决购买记录，本轮不买新包"
                )
                mall_result: dict[str, Any] = {
                    "action": "skipped",
                    "usable_packages": 0,
                    "error": "存在未决购买记录，待下轮对账",
                }
            else:
                mall_result = await ensure_package(
                    store, client, session, int(cfg["mall_good_id"])
                )
        detail["mall"] = mall_result
        detail["steps"].append(
            f"商城：可用资源包 {mall_result.get('usable_packages')} 个，动作 {mall_result.get('action')}"
        )
        if mall_result.get("error"):
            detail["errors"].append(mall_result["error"])

    # ③ 解锁（公式决策 + 错误三分类）
    plan = plan_unlocks(tasks, cfg)
    total = int(plan["total"])
    detail["steps"].append(
        f"解锁计划 {total}（{plan['reason']}），上限 {plan['cap']}"
    )
    done = 0
    unknown_streak = 0
    if total > 0:
        if not cfg["unlock_list_ids"]:
            detail["errors"].append("未配置解锁片单（unlock_list_ids），无法解锁")
        else:
            for list_id in cfg["unlock_list_ids"]:
                if done >= total or unknown_streak >= 2:
                    break
                try:
                    ml = await client.get_movie_list(session, int(list_id))
                except PicixError as e:
                    detail["errors"].append(f"片单 {list_id} 拉取失败: {e}")
                    continue
                movies = (
                    ml.get("list")
                    if isinstance(ml, dict) and isinstance(ml.get("list"), list)
                    else []
                )
                for m in movies:
                    if done >= total or unknown_streak >= 2:
                        break
                    if not isinstance(m, dict) or m.get("isUnlock"):
                        continue
                    movie_id = m.get("id") or m.get("movieId")
                    if not movie_id:
                        continue
                    link_id = int(m.get("movieListLinkId") or 0)
                    if not link_id:
                        # movieListLinkId 缺失时按 DIRECT 处理，不计入片单任务——跳过
                        detail["steps"].append(f"影片 {movie_id} 缺少 movieListLinkId，跳过")
                        continue
                    try:
                        await client.unlock_movie(session, int(movie_id), link_id)
                        done += 1
                        unknown_streak = 0
                        detail["unlocks"].append(
                            {
                                "movieId": movie_id,
                                "listId": list_id,
                                "movieListLinkId": link_id,
                                "ok": True,
                            }
                        )
                        log.info(
                            "解锁成功 movieId=%s listId=%s linkId=%s",
                            movie_id,
                            list_id,
                            link_id,
                        )
                    except PicixError as e:
                        # 明确业务拒绝（冷却/积分/权限/片单限制）→ 跳过该影片继续
                        detail["unlocks"].append(
                            {
                                "movieId": movie_id,
                                "listId": list_id,
                                "movieListLinkId": link_id,
                                "ok": False,
                                "kind": "business",
                                "error": str(e),
                            }
                        )
                        detail["errors"].append(f"解锁 movieId={movie_id} 失败: {e}")
                    except TransportUnknown as e:
                        # 未知结果：服务端可能已受理，不当失败处理，不计 done 不重放
                        unknown_streak += 1
                        detail["unlocks"].append(
                            {
                                "movieId": movie_id,
                                "listId": list_id,
                                "movieListLinkId": link_id,
                                "ok": False,
                                "kind": "unknown",
                                "error": str(e),
                            }
                        )
                        detail["errors"].append(f"解锁 movieId={movie_id} 结果未知: {e}")
                if unknown_streak >= 2:
                    detail["steps"].append("连续 2 次未知结果，终止本轮解锁")
            if done < total:
                detail["steps"].append(f"解锁完成 {done}/{total}")

    # ④ 回读（进度 + 积分 + 最低消费）
    try:
        tasks_after = await _collect_tasks_async(client, session)
        detail["tasks_snapshot"] = [
            {
                "code": t.get("code"),
                "name": t.get("name"),
                "periodType": t.get("periodType"),
                "cycleEndAt": t.get("cycleEndAt"),
                "acceptStartAt": t.get("acceptStartAt"),
                "target": t.get("target"),
                "rewardPoints": t.get("rewardPoints"),
                "progress": (t.get("progress") or {}).get("progress"),
                "progressTarget": (t.get("progress") or {}).get("target"),
                "finishedAt": (t.get("progress") or {}).get("finishedAt"),
            }
            for t in tasks_after
        ]
        uc = await client.load_user_config(session)
        if isinstance(uc, dict):
            detail["points"] = uc.get("points") if "points" in uc else uc.get("pointBalance")
            mc = uc.get("minimumConsumption") or uc.get("minimum_consumption") or {}
            if isinstance(mc, dict) and mc:
                detail["minimum_consumption"] = mc
    except PicixError as e:
        detail["errors"].append(f"快照失败: {e}")

    detail["confirmed_unlocks"] = sum(1 for u in detail["unlocks"] if u.get("ok"))
    status = _finalize(detail, plan, force)
    return plan, status


async def _collect_tasks_async(client: PicixClient, session: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = 0
    for _ in range(20):
        resp = await client.tasks_list(session, after_id=cursor)
        page_items = resp.get("items") if isinstance(resp, dict) else None
        if not isinstance(page_items, list):
            break
        items.extend(page_items)
        next_cursor = resp.get("nextCursor")
        if next_cursor is None:
            break
        try:
            cursor = int(next_cursor)
        except (TypeError, ValueError):
            log.warning("任务游标非整数，停止分页: %r", next_cursor)
            break
    return items


async def fetch_status(store: Store, client: PicixClient) -> dict[str, Any]:
    """供状态页使用：会话态 + 实时任务 + 积分。

    401 不再清除会话（保留旧凭据供恢复/人工核对），只标记 session_invalid。
    """
    session = store.get_session()
    out: dict[str, Any] = {"logged_in": bool(session), "version": APP_VERSION}
    if session:
        out["logged_at"] = session.get("logged_at")
        out["session_invalid"] = bool(store.get("session_invalid"))
    try:
        if session:
            out["tasks"] = await _collect_tasks_async(client, session)
            uc = await client.load_user_config(session)
            out["user_config"] = uc if isinstance(uc, dict) else {}
            mc = (
                uc.get("minimumConsumption") or uc.get("minimum_consumption") or {}
                if isinstance(uc, dict)
                else {}
            )
            if isinstance(mc, dict) and mc:
                out["minimum_consumption"] = mc
    except PicixError as e:
        out["api_error"] = str(e)
        if e.status == 401:
            store.put("session_invalid", time.time())
            out["session_invalid"] = True
            out["api_error"] += "（会话失效，待自动恢复或手动登录）"
    out["last_runs"] = store.list_runs(limit=10)
    return out
