"""CloakBrowser 传输层：所有 picix API 请求经真实隐身 Chromium 发出。

背景：站点 API（/api/adult/*）被 Cloudflare 按 TLS/HTTP2 指纹强制质询，
纯 HTTP 客户端（httpx/curl_cffi + 任意指纹模拟）一律 403「Just a moment」，
连浏览器内 fetch 都依赖页面导航后的完整浏览器上下文。唯一稳定通路是真实
浏览器网络栈：CloakBrowser（源码级指纹隐身 Chromium，Playwright 兼容 API）
加载站点页面后，在页面上下文内发 fetch，签名头原样带上。

- 免费版二进制（v146）通过 CLOAKBROWSER_BINARY_PATH 指定，无需 license；
  Dockerfile 构建期从 GitHub Releases 下载。
- headless 若被 CF 识别，容器内用 xvfb 跑 headed（launch 参数不变）。
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .client import BASE_URL

log = logging.getLogger("picix.transport")

_JS_FETCH = """async ([url, method, headers, body, timeoutMs]) => {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort("timeout"), timeoutMs || 45000);
    try {
        const resp = await fetch(url, {
            method,
            headers,
            body: body == null || body === "" ? undefined : body,
            credentials: "include",
            signal: ctrl.signal,
        });
        const text = await resp.text();
        return { status: resp.status, text };
    } catch (err) {
        // 网络失败/超时（AbortError）→ status 0，由上层按「未知结果」分类
        return { status: 0, text: String(err && err.message ? err.message : err) };
    } finally {
        clearTimeout(timer);
    }
}"""


class TransportError(Exception):
    pass



class TransportUnknown(Exception):
    """传输层未知结果：超时/连接中断/非预期响应。

    上层不得当作普通失败处理（服务端可能已受理扣额度），需回读对账。
    """

class BrowserTransport:
    """单浏览器单页面传输层。lazy 启动，异常时重建。"""

    def __init__(self, headless: bool | None = None):
        self._browser = None
        self._page = None
        self._proxy: str | None = None
        self._lock = asyncio.Lock()
        # 容器无显示时默认 headless；可用 PICIX_HEADLESS=0 强制 headed（配 xvfb）
        env = os.environ.get("PICIX_HEADLESS")
        self._headless = (env != "0") if env is not None else (headless if headless is not None else True)

    async def _ensure_locked(self) -> Any:
        """初始化或返回可用页面。调用方必须已持有 self._lock。"""
        if self._page is not None:
            return self._page
        if self._browser is not None:  # 代理变更等导致页面重置：先关旧实例
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        from cloakbrowser import launch_async

        self._browser = await launch_async(
            headless=self._headless,
            proxy=self._proxy or None,
            args=["--no-first-run", "--disable-dev-shm-usage"],
        )
        self._page = await self._browser.new_page()
        resp = await self._page.goto(BASE_URL, wait_until="load", timeout=60000)
        log.info(
            "CloakBrowser 启动 headless=%s proxy=%s goto=%s(%s)",
            self._headless,
            self._proxy or "(直连)",
            BASE_URL,
            resp.status if resp else "n/a",
        )
        # 等 cf_clearance cookie 签发（无感质询一般几秒内完成）；
        # 未就绪就发 API 请求会被 CF 以 403 质询页拦截
        for i in range(30):
            cookies = await self._page.context.cookies()
            if any(c["name"].startswith("cf") for c in cookies):
                log.info("CF clearance 就绪（约 %ds）", i * 2)
                break
            await asyncio.sleep(2)
        else:
            log.warning("60s 内未取得 CF clearance cookie，继续尝试")
        # 换乘到非 SPA 资源页：同源、无站点 JS，SPA 路由不会再自动导航
        # 打断 page.evaluate（"Execution context destroyed"）
        stable = BASE_URL.rstrip("/") + "/favicon.ico"
        resp2 = await self._page.goto(stable, wait_until="load", timeout=30000)
        log.info("传输层稳定页 %s(%s)", stable, resp2.status if resp2 else "n/a")
        return self._page

    async def fetch(
        self, method: str, url: str, headers: dict[str, str], body: str | None
    ) -> tuple[int, str]:
        """在站点页面上下文内发 fetch，返回 (status, text)。

        全程持有实例锁（串行化：初始化/请求/重建互斥，单用户足够）。
        传输失败分类：GET 幂等 → 重建重试一次；非 GET → TransportUnknown
        （服务端可能已受理，禁止重放）；CF 403 质询请求未达 API → 任意
        method 都可安全重建重试一次。
        """
        async with self._lock:
            page = await self._ensure_locked()
            try:
                result = await page.evaluate(_JS_FETCH, [url, method, headers, body, 45000])
            except Exception as e:  # noqa: BLE001 — 页面崩溃/导航中断等
                if method.upper() != "GET":
                    log.warning("浏览器 fetch 异常（非 GET 不重放）: %r", e)
                    await self._restart_locked()
                    raise TransportUnknown(f"transport error: {e!r}") from e
                log.warning("浏览器 fetch 异常，重建会话: %r", e)
                await self._restart_locked()
                page = await self._ensure_locked()
                result = await page.evaluate(_JS_FETCH, [url, method, headers, body, 45000])
            status = int(result.get("status") or 0)
            text = str(result.get("text") or "")
            if status == 0:
                # JS 层超时/网络失败（AbortController 触发）：未知结果
                if method.upper() != "GET":
                    raise TransportUnknown(f"transport unknown: {text[:200]}")
                log.warning("浏览器 fetch 失败（GET 重建重试）: %s", text[:120])
                await self._restart_locked()
                page = await self._ensure_locked()
                result = await page.evaluate(_JS_FETCH, [url, method, headers, body, 45000])
                status = int(result.get("status") or 0)
                text = str(result.get("text") or "")
                if status == 0:
                    raise TransportUnknown(f"transport unknown after retry: {text[:200]}")
            if status == 403 and "just a moment" in text.lower():
                # clearance 过期/失效：请求未达 API，重建后重试一次
                log.warning("CF 拦截 API 请求，重建浏览器会话: %s", url)
                await self._restart_locked()
                page = await self._ensure_locked()
                result = await page.evaluate(_JS_FETCH, [url, method, headers, body, 45000])
                status = int(result.get("status") or 0)
                text = str(result.get("text") or "")
            return status, text
    async def restart(self) -> None:
        async with self._lock:
            await self._restart_locked()

    async def _restart_locked(self) -> None:
        """关闭当前浏览器实例。调用方必须已持有 self._lock。"""
        self._page = None
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None

    async def stop(self) -> None:
        await self.restart()


_transport: BrowserTransport | None = None


def get_transport(proxy: str | None = None) -> BrowserTransport:
    """进程级单例。proxy 变化时重建。"""
    global _transport
    want = proxy or ""
    if _transport is None:
        _transport = BrowserTransport()
        _transport._proxy = want or None
    elif _transport._proxy != (want or None):
        _transport._proxy = want or None
        _transport._page = None
    return _transport


async def reset_transport() -> None:
    global _transport
    if _transport is not None:
        await _transport.stop()
        _transport = None
