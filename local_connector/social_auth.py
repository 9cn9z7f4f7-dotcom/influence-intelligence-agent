"""Authenticated local browser session helpers."""
from __future__ import annotations
from pathlib import Path

LOGIN_URLS = {
    "instagram": "https://www.instagram.com/accounts/login/",
    "tiktok": "https://www.tiktok.com/login",
}
HOME_URLS = {
    "instagram": "https://www.instagram.com/",
    "tiktok": "https://www.tiktok.com/",
}
CHALLENGE_URL_MARKERS = {
    "instagram": ["/challenge/", "/accounts/suspended/", "/accounts/access_tool/"],
    "tiktok": ["/captcha", "/verify", "/check"],
}
LOGIN_URL_MARKERS = {
    "instagram": ["/accounts/login"],
    "tiktok": ["/login"],
}


def _looks_logged_out(platform: str, page) -> bool:
    url = (page.url or "").lower()
    if any(marker in url for marker in LOGIN_URL_MARKERS.get(platform, [])):
        return True
    try:
        if platform == "instagram" and page.query_selector('input[name="username"], input[name="password"]'):
            return True
        if platform == "tiktok" and page.query_selector('input[type="password"]'):
            return True
    except Exception:
        pass
    return False


def _interactive_login(platform: str, state_path: Path, playwright, existing_state: bool):
    print(f"\n[{platform}] Требуется вход - открываю видимое окно браузера.")
    print(f"[{platform}] Залогинься вручную (включая 2FA, если платформа его запросит).")
    browser = playwright.chromium.launch(headless=False)
    kwargs = {"storage_state": str(state_path)} if existing_state and state_path.exists() else {}
    context = browser.new_context(**kwargs)
    page = context.new_page()
    page.goto(LOGIN_URLS[platform])
    input(f">>> Когда вход в {platform} завершён и лента/профиль загрузились, нажми Enter здесь... ")
    context.storage_state(path=str(state_path))
    try:
        page.close()
    except Exception:
        pass
    page = context.new_page()
    page.goto(HOME_URLS[platform])
    return browser, context, page


def ensure_authenticated_context(platform: str, state_path: Path, playwright, headless_if_authenticated: bool = True):
    if state_path.exists():
        browser = playwright.chromium.launch(headless=headless_if_authenticated)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        try:
            page.goto(HOME_URLS[platform], wait_until="domcontentloaded", timeout=20_000)
            page.wait_for_timeout(800)
            if not _looks_logged_out(platform, page):
                print(f"[{platform}] Использую сохранённую сессию.")
                return browser, context, page
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass
        print(f"[{platform}] Сохранённая сессия недействительна; обновляю авторизацию.")
        return _interactive_login(platform, state_path, playwright, existing_state=True)
    print(f"\n[{platform}] Сохранённая сессия не найдена.")
    return _interactive_login(platform, state_path, playwright, existing_state=False)


def detect_challenge(platform: str, page) -> bool:
    url = page.url or ""
    return any(marker in url for marker in CHALLENGE_URL_MARKERS.get(platform, []))
