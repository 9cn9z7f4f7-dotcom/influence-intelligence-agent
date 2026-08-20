"""
Screenshot capture (Playwright, headless Chromium) - раздел 3 требований.

Вызывается ТОЛЬКО когда deterministic DOM/API detector вернул manual_review
(см. app/detection.py::should_escalate_to_visual_evidence) - НЕ на каждой
странице.

Если Playwright не установлен или browser binaries недоступны (например,
на Render без `playwright install chromium` в build-команде) - возвращает
None, а не бросает исключение. Вызывающий код (VisualEvidenceEnricher) в
этом случае честно возвращает visual_ai_status = "unavailable", pipeline
продолжает работать без vision-эскалации (раздел 24 - тот же failsafe-принцип,
что и для OpenRouter).
"""
from __future__ import annotations

from typing import Callable, Optional

DEFAULT_TIMEOUT_MS = 15_000
DEFAULT_VIEWPORT = {"width": 1280, "height": 900}


def capture_screenshot(url: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> Optional[bytes]:
    """Возвращает PNG bytes скриншота публичной страницы, или None если
    недоступно/упало по любой причине (нет Playwright, нет браузера, timeout,
    страница защищена и т.п.) - НИКОГДА не бросает исключение наружу."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport=DEFAULT_VIEWPORT)
                page.goto(url, timeout=timeout_ms, wait_until="load")
                return page.screenshot(full_page=False)
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 - любая ошибка браузера/сети => failsafe None
        return None


class ScreenshotCache:
    """Кэширует screenshot по URL в рамках одного analysis run, чтобы одна и
    та же страница не скриншотилась повторно (раздел 3 требований)."""

    def __init__(self, capture_fn: Callable[[str], Optional[bytes]] = capture_screenshot) -> None:
        self._capture_fn = capture_fn
        self._by_url: dict[str, Optional[bytes]] = {}
        self.capture_calls = 0  # для тестов/наблюдаемости - сколько раз реально скриншотили

    def get_or_capture(self, url: str) -> Optional[bytes]:
        if url in self._by_url:
            return self._by_url[url]
        self.capture_calls += 1
        screenshot = self._capture_fn(url)
        self._by_url[url] = screenshot
        return screenshot

    def stats(self) -> dict:
        return {"unique_urls": len(self._by_url), "capture_calls": self.capture_calls}
