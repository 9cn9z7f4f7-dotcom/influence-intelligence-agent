"""
Instagram local connector - раздел 12, 15 требований.

Использует authenticated Playwright-сессию (см. social_auth.py) для
навигации по ПУБЛИЧНЫМ страницам (поиск/посты) - никогда не обходит логин/
CAPTCHA, никогда не трогает приватные настройки чужого аккаунта.

Собирает ТОЛЬКО реально отображаемые публичные поля (раздел 15).
Недоступное поле = None - никогда не придумывается.

ВАЖНО: DOM-разметка Instagram часто меняется - это best-effort extraction.
Если селекторы не совпали с текущей версией страницы, job честно
возвращает 0 найденных постов (status="ok", items=[]), а не выдуманные данные.
"""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from app.connectors.models import ConnectorJob, ConnectorResultItem, ConnectorResultsSubmission
from local_connector.social_auth import detect_challenge, ensure_authenticated_context

MAX_POSTS_PER_JOB = 8
NAV_TIMEOUT_MS = 20_000


def handle_job(job: ConnectorJob, connector_id: str, connector_token: str, playwright,
                state_path: Path) -> ConnectorResultsSubmission:
    browser = None
    try:
        browser, context, page = ensure_authenticated_context("instagram", state_path, playwright)
    except Exception as exc:  # noqa: BLE001 - не роняем весь connector process из-за одного job
        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
            status="error", detail=f"не удалось открыть authenticated browser: {exc}", items=[],
        )

    try:
        page.goto(f"https://www.instagram.com/explore/search/keyword/?q={job.brand}", timeout=NAV_TIMEOUT_MS)

        if detect_challenge("instagram", page):
            return ConnectorResultsSubmission(
                connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
                status="manual_intervention_required",
                detail="Instagram запросил challenge/CAPTCHA во время навигации - продолжение требует "
                       "ручного шага пользователя в headed-браузере (см. LOCAL_CONNECTOR.md).",
                items=[],
            )

        page.wait_for_timeout(2000)
        post_links: list[str] = []
        if page.query_selector('a[href*="/p/"]'):
            post_links = page.eval_on_selector_all('a[href*="/p/"]', "els => els.map(e => e.href)")

        items: list[ConnectorResultItem] = []
        for url in list(dict.fromkeys(post_links))[:MAX_POSTS_PER_JOB]:
            item = _extract_post(page, url, job.brand, job.aliases)
            if item:
                items.append(item)

        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id, status="ok",
            detail=f"{len(items)} публичных постов найдено по запросу '{job.brand}'", items=items,
        )
    except Exception as exc:  # noqa: BLE001
        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
            status="error", detail=str(exc), items=[],
        )
    finally:
        if browser:
            browser.close()


def _extract_post(page, url: str, brand: str, aliases: list[str]) -> Optional[ConnectorResultItem]:
    try:
        post_page = page.context.new_page()
        post_page.goto(url, timeout=NAV_TIMEOUT_MS)
        post_page.wait_for_timeout(1500)

        caption = _text_or_none(post_page, "article h1, article span")
        username = _text_or_none(post_page, "header a")
        profile_url = None
        header_link = post_page.query_selector("header a")
        if header_link:
            href = header_link.get_attribute("href")
            if href:
                profile_url = f"https://www.instagram.com{href}" if href.startswith("/") else href

        page_text = post_page.inner_text("body") if post_page.query_selector("body") else ""
        lowered = page_text.lower()
        paid_partnership = "paid partnership" in lowered or "платное партнёрство" in lowered
        collaboration = "collaboration" in lowered or "совместно с" in lowered
        brand_terms = [brand] + list(aliases or [])
        brand_mention = any(t.lower() in (caption or "").lower() for t in brand_terms if t)
        hashtags = [w for w in (caption or "").split() if w.startswith("#")]

        screenshot_b64 = None
        try:
            screenshot_b64 = base64.b64encode(post_page.screenshot()).decode("ascii")
        except Exception:  # noqa: BLE001 - screenshot - best effort, не критично
            pass

        result = ConnectorResultItem(
            username=username, profile_url=profile_url, post_url=url, caption=caption, hashtags=hashtags,
            brand_mention=brand_mention, paid_partnership_label=paid_partnership,
            collaboration_label=collaboration, links=[], screenshot_base64=screenshot_b64,
        )
        post_page.close()
        return result
    except Exception:  # noqa: BLE001 - один пост не должен ронять весь job
        return None


def _text_or_none(page, selector: str) -> Optional[str]:
    el = page.query_selector(selector)
    if not el:
        return None
    text = (el.inner_text() or "").strip()
    return text or None
