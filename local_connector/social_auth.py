"""
Общая authenticated-browser логика для Instagram/TikTok (раздел 12-13
требований).

Первый запуск (для каждой платформы):
  1. открывается ОБЫЧНОЕ ВИДИМОЕ browser window (headless=False);
  2. пользователь вручную логинится;
  3. вручную проходит 2FA, если платформа его запросит;
  4. session state (cookies/localStorage) сохраняется локально в
     .local_sessions/<platform>_state.json.

Этот файл НИКОГДА:
  - не отправляется на Render;
  - не отправляется в OpenRouter;
  - не коммитится в git (см. .gitignore).

Пароль НИКОГДА не запрашивается и не хранится этим кодом - пользователь вводит
его сам в реальном окне браузера, которое открывает Playwright.

Если на странице появляется CAPTCHA/challenge/checkpoint - detect_challenge()
возвращает True, и вызывающий код (instagram_connector.py/tiktok_connector.py)
обязан вернуть status="manual_intervention_required" и остановиться. Никаких
попыток автоматически обойти защиту (раздел 12-13, 33).
"""
from __future__ import annotations

from pathlib import Path

LOGIN_URLS = {
    "instagram": "https://www.instagram.com/accounts/login/",
    "tiktok": "https://www.tiktok.com/login",
}

# URL-паттерны, характерные для challenge/CAPTCHA/checkpoint потоков.
# Не исчерпывающе - платформы часто меняют пути; при необходимости дополнить.
CHALLENGE_URL_MARKERS = {
    "instagram": ["/challenge/", "/accounts/suspended/", "/accounts/access_tool/"],
    "tiktok": ["/captcha", "/verify", "/check"],
}


def ensure_authenticated_context(platform: str, state_path: Path, playwright, headless_if_authenticated: bool = True):
    """Возвращает (browser, context, page).

    Если state_path уже существует - открывает headless-браузер с сохранённой
    сессией. Иначе открывает ВИДИМОЕ окно и блокирующе ждёт (input()), пока
    пользователь залогинится вручную, затем сохраняет storage_state."""
    if state_path.exists():
        browser = playwright.chromium.launch(headless=headless_if_authenticated)
        context = browser.new_context(storage_state=str(state_path))
        page = context.new_page()
        return browser, context, page

    print(f"\n[{platform}] Сохранённая сессия не найдена - открываю видимое окно браузера для входа.")
    print(f"[{platform}] Залогинься вручную (включая 2FA, если платформа его запросит).")
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context()
    login_page = context.new_page()
    login_page.goto(LOGIN_URLS[platform])
    input(f">>> Когда вход в {platform} завершён и лента/профиль загрузились, нажми Enter здесь... ")
    context.storage_state(path=str(state_path))
    login_page.close()

    page = context.new_page()
    return browser, context, page


def detect_challenge(platform: str, page) -> bool:
    """True, если текущий URL страницы похож на CAPTCHA/challenge/checkpoint
    поток - вызывающий код должен остановиться, а не пытаться обойти."""
    url = page.url or ""
    return any(marker in url for marker in CHALLENGE_URL_MARKERS.get(platform, []))
