"""Playwright Web 控制器。

封装 Playwright 浏览器操作，API 与 ADBController 对齐：
- screenshot(path) -> Path
- wait(seconds) -> None

额外提供 Web 特有的 DOM 操作方法。
"""

from __future__ import annotations

import os
import signal
from pathlib import Path

from playwright.sync_api import sync_playwright, Browser, Page


class WebController:
    """Playwright 浏览器控制封装。"""

    # 移动端 UA，避免服务端检测到 HeadlessChrome 返回降级数据
    _MOBILE_UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1"
    )

    def __init__(
        self,
        browser_type: str = "chromium",
        headless: bool = True,
        base_url: str = "",
    ) -> None:
        self._playwright = sync_playwright().start()
        browser_launcher = getattr(self._playwright, browser_type, self._playwright.chromium)
        self._browser: Browser = browser_launcher.launch(headless=headless)
        # 创建移动端 Context（与诊断脚本一致），确保 H5 页面正常渲染
        self._context = self._browser.new_context(
            user_agent=self._MOBILE_UA,
            viewport={"width": 375, "height": 812},
            device_scale_factor=3,
            is_mobile=True,
            has_touch=True,
        )
        self._page: Page = self._context.new_page()
        self.base_url = base_url.rstrip("/") if base_url else ""
        self._force_killed = False  # force_kill 后跳过 teardown

    @property
    def page(self) -> Page:
        return self._page

    # ── 核心方法（与 ADBController 对齐）─────────────────

    def screenshot(self, path: str | Path) -> Path:
        """截取当前页面，保存为 PNG 文件，返回文件路径。"""
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self._page.screenshot(path=str(output_path), full_page=False)
        return output_path

    def wait(self, seconds: float) -> None:
        import time
        time.sleep(seconds)

    # ── Web 特有方法 ──────────────────────────────────────

    def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """导航到目标 URL。相对路径会拼接到 base_url。

        wait_until 可选值: "load" | "domcontentloaded" | "networkidle" | "commit"
        - SPA/H5 页面推荐使用 "load"，配合 wait_for_selector 等待具体元素
        - 避免使用 "networkidle"，对有埋点/长轮询的页面可能永不返回
        """
        if not url.startswith("http") and self.base_url:
            url = f"{self.base_url}/{url.lstrip('/')}"
        self._page.goto(url, wait_until=wait_until)

    def click(self, selector: str) -> None:
        self._page.click(selector)

    def fill(self, selector: str, text: str) -> None:
        self._page.fill(selector, text)

    def type(self, selector: str, text: str) -> None:
        """逐字输入（模拟键盘输入）。"""
        self._page.type(selector, text)

    def get_text(self, selector: str) -> str:
        return self._page.text_content(selector) or ""

    def get_texts(self, selector: str) -> list[str]:
        return self._page.locator(selector).all_text_contents()

    def wait_for_selector(self, selector: str, timeout: float = 10_000) -> None:
        self._page.wait_for_selector(selector, timeout=timeout)

    def is_visible(self, selector: str) -> bool:
        return self._page.is_visible(selector)

    def evaluate(self, js: str) -> str:
        """执行 JS 并返回结果字符串。"""
        result = self._page.evaluate(js)
        return str(result) if result is not None else ""

    def title(self) -> str:
        return self._page.title()

    def get_url(self) -> str:
        return self._page.url

    def get_page_html(self) -> str:
        """获取当前页面的完整 HTML。"""
        return self._page.content()

    def teardown(self) -> None:
        """关闭浏览器和 Playwright。如果已被 force_kill 则跳过。"""
        if self._force_killed:
            return  # 进程已死，再调 close/stop 会永久挂起
        self._context.close()
        self._browser.close()
        self._playwright.stop()

    def force_kill(self) -> None:
        """强制杀死浏览器进程（用于线程卡住时从外部中止）。

        直接杀死浏览器子进程，使挂起的 Playwright 操作抛出异常，
        从而让卡住的线程得以退出。之后 Playwright 驱动进程也会随之清理。
        """
        self._force_killed = True  # 标记，防止 teardown 再次操作已死进程
        try:
            # Playwright 内部结构: _impl -> _connection -> _transport -> _proc (driver 进程)
            driver_proc = self._playwright._impl._connection._transport._proc
            if driver_proc and driver_proc.poll() is None:
                # 杀死 Playwright 驱动进程，浏览器子进程会随之被 OS 回收
                os.kill(driver_proc.pid, signal.SIGTERM)
        except Exception:
            pass
