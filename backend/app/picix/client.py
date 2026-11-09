"""Picix API 客户端。

Base: https://jav.picix.us/api/adult/（request_target 签名时去掉 /api 前缀）。
全部端点为逆向自前端 JS 的已知路径：

- POST /Users/getLoginCode {proofPublicKey}        （不签名）
- POST /Users/checkLoginCode {code}                （login 签名）
- GET  /Tasks/list?afterId=0                       （session）
- POST /Tasks/accept {code}                        （session）
- POST /Movies/unlock {movieId, movieListLinkId}   （session）
- POST /Movies/unlockList {movieIds: [...]}        （session）
- GET  /Movies/getMovieList?listId=N               （session）
- GET  /Movies/listMyMovieList                     （session）
- GET  /Users/loadUserConfig                       （session）
- GET  /Users/loadContentAccess                    （session）
- GET  /Users/listPointHistory                     （session）

响应为原始 JSON；非 2xx 抛 PicixError（含 status 与响应体）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from .signing import (
    compact_json,
    load_private_key_pem,
    new_private_key,
    new_proof_key_id,
    proof_public_key_b64,
    private_key_pem,
    sign_request,
)
log = logging.getLogger("picix.client")


BASE_URL = "https://jav.picix.us"
API_PREFIX = "/api/adult"  # apiBase；站点另有 /api/cinema 未使用


class PicixError(Exception):
    def __init__(self, status: int, payload: Any, url: str):
        self.status = status
        self.payload = payload
        self.url = url
        code = ""
        message = ""
        if isinstance(payload, dict):
            code = str(payload.get("code") or "")
            message = str(payload.get("message") or payload.get("msg") or payload.get("error") or "")
        super().__init__(f"[{status}] {code or url} {message}".strip())

    @property
    def code(self) -> str:
        if isinstance(self.payload, dict):
            return str(self.payload.get("code") or "")
        return ""


class PicixClient:
    """无状态客户端；会话/密钥由调用方传入。

    session 结构: {token, session_id, priv_pem}
    """

    def __init__(self, proxy: str = "", timeout: float = 30.0):
        self._proxy = proxy or ""
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            proxy=proxy or None,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) picix-assistant/1.0",
                "Accept": "application/json, text/plain, */*",
            },
            follow_redirects=False,
        )

    async def __aenter__(self) -> "PicixClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def ping(self) -> dict[str, Any]:
        """连通性测试：GET 站点首页（无需鉴权），返回状态码与耗时。"""
        t0 = time.monotonic()
        try:
            resp = await self._http.get("/")
            return {
                "ok": True,
                "status_code": resp.status_code,
                "elapsed_ms": round((time.monotonic() - t0) * 1000),
            }
        except httpx.HTTPError as e:
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_ms": round((time.monotonic() - t0) * 1000),
            }

    # ---- 底层请求 ----
    async def _send(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: str,
        timeout: float | None = None,
    ) -> Any:
        """所有 API 请求经 CloakBrowser 页面内 fetch 发出。

        站点 API 被 Cloudflare 按 TLS/HTTP2 指纹强制质询，纯 HTTP 客户端
        （httpx/curl_cffi）一律 403；只有真实浏览器导航后的页面上下文放行。
        timeout 参数保留兼容（浏览器 fetch 自身无超时）。
        """
        from .transport import get_transport

        status, text = await get_transport(self._proxy).fetch(
            method, f"{BASE_URL}{url}", headers, body or None
        )
        try:
            payload = json.loads(text)
        except ValueError:
            payload = {"raw": text[:500]}
        if status >= 300:
            raise PicixError(status, payload, url)
        # 站点信封 {success, code, msg, data}：业务失败抛错，成功解包 data
        if isinstance(payload, dict) and "success" in payload:
            if payload.get("success") is not True:
                raise PicixError(status, payload, url)
            return payload.get("data")
        return payload
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        session: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """path 为含 API_PREFIX 的路径；签名 target 去掉 /api 前缀。"""
        query = ""
        if params:
            query = urlencode(params)
        url = f"{path}?{query}" if query else path
        request_target = path.replace("/api", "", 1) + (f"?{query}" if query else "")

        body = ""
        base_headers: dict[str, str] = {}
        if json_body is not None:
            body = compact_json(json_body)
            base_headers["Content-Type"] = "application/json"

        private_key = None
        if session:
            private_key = load_private_key_pem(session["priv_pem"])
            # 站点前端 zE(e,'Authorization', n.token) 只写 token 原文，无 Bearer 前缀；
            # repo bundle 全文搜索 "Bearer"/"bearer" 均为 0 次命中（已用 CloakBrowser 核实）。
            # 之前这里加 Bearer 前缀导致服务端签名/鉴权校验不通过，会话请求全部 401。
            base_headers["Authorization"] = session["token"]

        def _sign() -> dict[str, str]:
            h = dict(base_headers)
            if session:
                h.update(
                    sign_request(
                        kind="session",
                        credential=session["token"],
                        method=method,
                        request_target=request_target,
                        body=body,
                        private_key=private_key,
                    )
                )
            return h

        # 服务端偶发 SESSION/LOGIN_PROOF_EXPIRED（proof 时间窗口判定），站点 SPA 的
        # 处理是删签名头、以新 time/nonce 重签后重试一次（JE → proofRetryCount:1
        # → QE.request(i)）。这里保持同语义：仅这类 401 重签重试一次，其余照常抛错。
        last_err: PicixError | None = None
        for attempt in range(2):
            try:
                return await self._send(method, url, _sign(), body, timeout)
            except PicixError as e:
                last_err = e
                if (
                    attempt == 0
                    and e.status == 401
                    and e.code in ("SESSION_PROOF_EXPIRED", "LOGIN_PROOF_EXPIRED")
                ):
                    log.warning("proof 过期，重签重试: %s", url)
                    continue
                raise
        raise last_err
    # ---- 登录 ----
    async def get_login_code(self) -> dict[str, Any]:
        """生成登录密钥对 → 换取 login code。

        返回 {code, priv_pem, proof_key_id}；code 需用户在 TG 中确认。
        proof key id 与签名无关，仅本地标识（格式同前端 login-xxx）。
        """
        private_key = new_private_key()
        payload = await self._request(
            "POST",
            f"{API_PREFIX}/Users/getLoginCode",
            json_body={"proofPublicKey": proof_public_key_b64(private_key)},
        )
        code = payload.get("code") if isinstance(payload, dict) else None
        if not code:
            raise PicixError(
                0, {"message": f"getLoginCode 未返回 code: {payload}"}, "getLoginCode"
            )
        return {
            "code": str(code),
            "priv_pem": private_key_pem(private_key),
            "proof_key_id": new_proof_key_id(),
        }

    async def check_login_code(self, code: str, priv_pem: str) -> dict[str, Any]:
        """轮询登录结果；未确认时响应无 token。签名 kind=login, credential=code。"""
        private_key = load_private_key_pem(priv_pem)
        body = compact_json({"code": code})
        headers = sign_request(
            kind="login",
            credential=code,
            method="POST",
            request_target=f"{API_PREFIX}/Users/checkLoginCode".replace("/api", "", 1),
            body=body,
            private_key=private_key,
        )
        headers["Content-Type"] = "application/json"
        return await self._send(
            "POST", f"{API_PREFIX}/Users/checkLoginCode", headers, body
        )

    # ---- 会话态 API ----
    async def list_movie_list_subscriptions(self, session: dict[str, Any]) -> Any:
        """我订阅的片单：Movies/listMovieListSubscriptions → {items:[{listId,...}]}。"""
        return await self._request(
            "GET", f"{API_PREFIX}/Movies/listMovieListSubscriptions", session=session
        )

    async def tasks_list(self, session: dict[str, Any], after_id: int = 0) -> Any:
        return await self._request(
            "GET", f"{API_PREFIX}/Tasks/list", params={"afterId": after_id}, session=session
        )

    async def accept_task(self, session: dict[str, Any], code: str) -> Any:
        return await self._request(
            "POST", f"{API_PREFIX}/Tasks/accept", json_body={"code": code}, session=session
        )

    async def unlock_movie(
        self, session: dict[str, Any], movie_id: int, movie_list_link_id: int = 0
    ) -> Any:
        return await self._request(
            "POST",
            f"{API_PREFIX}/Movies/unlock",
            json_body={"movieId": int(movie_id), "movieListLinkId": int(movie_list_link_id)},
            session=session,
        )

    async def unlock_list(self, session: dict[str, Any], movie_ids: list[int]) -> Any:
        return await self._request(
            "POST",
            f"{API_PREFIX}/Movies/unlockList",
            json_body={"movieIds": [int(m) for m in movie_ids]},
            session=session,
        )

    async def get_movie_list(self, session: dict[str, Any], list_id: int) -> Any:
        return await self._request(
            "GET",
            f"{API_PREFIX}/Movies/getMovieList",
            params={"listId": int(list_id)},
            session=session,
        )

    async def list_my_movie_lists(self, session: dict[str, Any]) -> Any:
        return await self._request(
            "GET", f"{API_PREFIX}/Movies/listMyMovieList", session=session
        )

    async def load_user_config(self, session: dict[str, Any]) -> Any:
        return await self._request(
            "GET", f"{API_PREFIX}/Users/loadUserConfig", session=session
        )

    async def load_content_access(self, session: dict[str, Any]) -> Any:
        return await self._request(
            "GET", f"{API_PREFIX}/Users/loadContentAccess", session=session
        )

    async def list_point_history(self, session: dict[str, Any]) -> Any:
        return await self._request(
            "GET", f"{API_PREFIX}/Users/listPointHistory", session=session
        )

    # ---- 商城 / 资源包 ----
    async def mall_list_goods(self, session: dict[str, Any], after_id: int = 0) -> Any:
        return await self._request(
            "GET", f"{API_PREFIX}/Malls/listGoods", params={"afterId": after_id}, session=session
        )

    async def mall_pay_good(
        self, session: dict[str, Any], good_id: int, client_request_id: str
    ) -> Any:
        # 站点前端 payGood 超时为 30s；这里放宽到 60s 防边缘超时
        return await self._request(
            "POST",
            f"{API_PREFIX}/Malls/payGood",
            json_body={"goodId": int(good_id), "clientRequestId": client_request_id},
            session=session,
            timeout=60.0,
        )

    async def packages_list_mine(self, session: dict[str, Any], cursor: Any = None) -> Any:
        # 首页不传 cursor（服务端视为游标格式错误）；nextCursor 是不透明 token，原样回传
        params = {"cursor": cursor} if cursor is not None else None
        return await self._request("GET", f"{API_PREFIX}/Packages/listMine", params=params, session=session)

    async def traffic_summary(self, session: dict[str, Any]) -> Any:
        return await self._request("GET", f"{API_PREFIX}/Traffic/summary", session=session)

    async def mall_list_orders(self, session: dict[str, Any]) -> Any:
        return await self._request("GET", f"{API_PREFIX}/Malls/listOrders", session=session)
