"""批次C 冒烟脚本：对 frontend/dist 静态构建产物做 DOM 结构断言（不依赖后端数据）。

用法：
    .venv/Scripts/python.exe .smoke/dom_analysis.py

起一个本地静态 http.server 指向 frontend/dist，用 playwright 打开页面，只断言
DOM 结构/CSS（topbar、分段导航、status-grid、config-anchor-nav、sticky 保存条、
历史空态、360px 无横向溢出等），不校验任何来自后端 API 的数据（除 /api/runs 打桩
返回空数组以触发历史页空态分支外，其余 API 请求失败无妨，页面落入未登录/加载态
分支属预期）。
"""
import http.server
import functools
import os
import socket
import sys
import threading
import time

DIST_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
PORT = 8911
BASE_URL = f"http://127.0.0.1:{PORT}/"
SHOT_DIR = os.path.dirname(__file__)

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (" | " + str(detail) if detail else ""))


def wait_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=DIST_DIR)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler)
    httpd.serve_forever()


def main():
    threading.Thread(target=serve, daemon=True).start()
    if not wait_port(PORT):
        print("静态服务器未就绪")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.launch(
        headless=True,
        executable_path=r"C:\Users\wff19\AppData\Local\ms-playwright\chromium_headless_shell-1228\chrome-headless-shell-win64\chrome-headless-shell.exe",
        args=["--no-first-run"],
    )

    # ---------- 桌面视口 1280x800：状态页 ----------
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    # /api/runs 打桩返回空数组：让历史页进入“空态”分支而非 fetch 失败的 error 分支。
    # 其余 /api/* 路由不打桩，保持“API 请求失败无妨，只查 DOM 结构”的约定。
    page.route("**/api/runs*", lambda route: route.fulfill(status=200, content_type="application/json", body="[]"))
    page.goto(BASE_URL, wait_until="load", timeout=30000)
    page.wait_for_timeout(1000)

    topbar = page.query_selector(".topbar")
    check("topbar 存在", topbar is not None)
    if topbar:
        text = topbar.inner_text()
        check("topbar 含品牌名 picix-assistant", "picix-assistant" in text, text.replace("\n", " / "))
    tabs = page.query_selector_all(".segmented-nav .tab")
    check("分段导航存在且含 3 个 tab", len(tabs) == 3, f"count={len(tabs)}")
    seg_nav = page.query_selector(".segmented-nav")
    check("分段导航样式类 .segmented-nav 存在", seg_nav is not None)

    # 状态页：StatusHeader 挂载点
    status_header = page.query_selector(".status-header")
    check("StatusHeader 挂载点 .status-header 存在", status_header is not None)

    # status-grid 双列（1280 视口）
    grid = page.query_selector(".status-grid")
    check("status-grid 存在", grid is not None)
    if grid:
        cols = page.evaluate(
            "(el) => getComputedStyle(el).gridTemplateColumns.split(' ').length", grid
        )
        check("status-grid 桌面视口下为两列", cols == 2, f"grid-template-columns 列数={cols}")

    # ConsumptionCard 挂载点
    consumption = page.query_selector(".consumption-card")
    check("ConsumptionCard 挂载点 .consumption-card 存在", consumption is not None)

    # TasksCard / PackagesCard 挂载点（status-grid 双列内容）
    tasks_card = page.query_selector(".tasks-card")
    check("TasksCard 挂载点 .tasks-card 存在", tasks_card is not None)
    packages_card = page.query_selector(".packages-card")
    check("PackagesCard 挂载点 .packages-card 存在", packages_card is not None)

    # 今日运行区
    body_text = page.inner_text("body")
    check("今日运行记录区块文案存在", "今日运行记录" in body_text)

    page.screenshot(path=os.path.join(SHOT_DIR, "rebuild-desktop-status.png"))

    # ---------- 桌面视口：配置页 ----------
    page.click(".segmented-nav .tab:nth-child(2)")
    page.wait_for_timeout(600)

    section_titles = ["Telegram 连接", "Picix 登录", "调度与运行", "解锁策略", "自动领取的任务", "商城固定购买", "站点数据解析"]
    cfg_text = page.inner_text("body")
    missing = [t for t in section_titles if t not in cfg_text]
    check("配置页 7 个分区标题存在", len(missing) == 0, f"缺失={missing}" if missing else "")

    anchor_nav = page.query_selector(".config-anchor-nav")
    check("config-anchor-nav 存在于 DOM", anchor_nav is not None)
    anchor_visible_1280 = anchor_nav.is_visible() if anchor_nav else False
    check("config-anchor-nav 在 1280 视口可见", anchor_visible_1280)

    save_bar = page.query_selector(".save-bar-sticky")
    check("sticky 保存条 .save-bar-sticky 存在", save_bar is not None)

    page.screenshot(path=os.path.join(SHOT_DIR, "rebuild-desktop-config.png"))

    # 720px 视口：锚点导航应不可见
    page.set_viewport_size({"width": 720, "height": 800})
    page.wait_for_timeout(400)
    anchor_nav_720 = page.query_selector(".config-anchor-nav")
    anchor_visible_720 = anchor_nav_720.is_visible() if anchor_nav_720 else False
    check("config-anchor-nav 在 720 视口不可见", not anchor_visible_720)

    # ---------- 桌面视口：历史页 ----------
    page.set_viewport_size({"width": 1280, "height": 800})
    page.wait_for_timeout(300)
    page.click(".segmented-nav .tab:nth-child(3)")
    page.wait_for_timeout(600)
    hist_text = page.inner_text("body")
    check("历史页空态文案「暂无历史」可见", "暂无历史" in hist_text)
    page.screenshot(path=os.path.join(SHOT_DIR, "rebuild-desktop-history.png"))

    # ---------- 移动视口 360x740 ----------
    page.set_viewport_size({"width": 360, "height": 740})
    page.wait_for_timeout(400)

    page.click(".segmented-nav .tab:nth-child(1)")
    page.wait_for_timeout(500)
    overflow = page.evaluate("document.documentElement.scrollWidth")
    check("360px 视口状态页无横向溢出", overflow <= 362, f"scrollWidth={overflow}")
    page.screenshot(path=os.path.join(SHOT_DIR, "rebuild-mobile-status.png"))

    page.click(".segmented-nav .tab:nth-child(2)")
    page.wait_for_timeout(500)
    overflow_cfg = page.evaluate("document.documentElement.scrollWidth")
    check("360px 视口配置页无横向溢出", overflow_cfg <= 362, f"scrollWidth={overflow_cfg}")
    page.screenshot(path=os.path.join(SHOT_DIR, "rebuild-mobile-config.png"))

    page.click(".segmented-nav .tab:nth-child(3)")
    page.wait_for_timeout(500)
    overflow_hist = page.evaluate("document.documentElement.scrollWidth")
    check("360px 视口历史页无横向溢出", overflow_hist <= 362, f"scrollWidth={overflow_hist}")
    page.screenshot(path=os.path.join(SHOT_DIR, "rebuild-mobile-history.png"))

    browser.close()
    pw.stop()

    fails = [r for r in results if not r[1]]
    print(f"\n共 {len(results)} 项，通过 {len(results) - len(fails)}，失败 {len(fails)}")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
