"""PNG канваса: один долгоживущий Chromium, скриншот /t/{token}?print=1."""
import asyncio
import logging
import os

from . import config

log = logging.getLogger(__name__)

_pw = None
_browser = None
_lock = asyncio.Lock()


async def _ensure_browser():
    global _pw, _browser
    if _browser is not None and _browser.is_connected():
        return _browser
    from playwright.async_api import async_playwright
    if _pw is None:
        _pw = await async_playwright().start()
    exe = os.getenv("CHROMIUM_PATH") or None   # если браузер Playwright стоит не там, где ждёт пакет
    _browser = await _pw.chromium.launch(args=["--no-sandbox"], executable_path=exe)
    return _browser


async def start() -> None:
    try:
        async with _lock:
            await _ensure_browser()
        log.info("playwright chromium started")
    except Exception as e:  # noqa: BLE001
        log.warning("playwright unavailable: %s", e)


async def stop() -> None:
    global _pw, _browser
    try:
        if _browser:
            await _browser.close()
        if _pw:
            await _pw.stop()
    except Exception:  # noqa: BLE001
        pass
    _pw = _browser = None


async def canvas_png(token: str) -> bytes | None:
    url = f"http://127.0.0.1:{config.PORT}/t/{token}?print=1"
    try:
        async with _lock:
            browser = await _ensure_browser()
        page = await browser.new_page(viewport={"width": 1600, "height": 1000},
                                      device_scale_factor=1.5)
        try:
            await page.goto(url, timeout=config.SNAPSHOT_TIMEOUT_S * 1000)
            await page.wait_for_function("window.__canvasReady === true",
                                         timeout=config.SNAPSHOT_TIMEOUT_S * 1000)
            return await page.screenshot(full_page=True, type="png")
        finally:
            await page.close()
    except Exception as e:  # noqa: BLE001
        log.warning("snapshot failed for %s: %s", token, e)
        return None
