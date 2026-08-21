"""
TikTok local connector - раздел 13, 16 требований, симметрично Instagram
(см. instagram_connector.py).

Использует authenticated Playwright-сессию для навигации по ПУБЛИЧНЫМ
страницам поиска/видео. Собирает только реально отображаемые публичные поля
(раздел 16) - недоступное поле = None.
"""
from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Optional

from app.connectors.models import ConnectorJob, ConnectorResultItem, ConnectorResultsSubmission
from local_connector.social_auth import detect_challenge, ensure_authenticated_context

MAX_VIDEOS_PER_JOB = 8
NAV_TIMEOUT_MS = 20_000


def handle_job(job: ConnectorJob, connector_id: str, connector_token: str, playwright,
                state_path: Path) -> ConnectorResultsSubmission:
    browser = None
    try:
        browser, context, page = ensure_authenticated_context("tiktok", state_path, playwright)
    except Exception as exc:  # noqa: BLE001
        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
            status="error", detail=f"не удалось открыть authenticated browser: {exc}", items=[],
        )

    try:
        source_url = (job.settings or {}).get("brand_source_url")
        brand_handle = ((job.settings or {}).get("brand_handle") or "").lstrip("@").lower()
        video_links: list[str] = []
        if source_url and "tiktok.com" in source_url:
            page.goto(source_url, timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(1800)
            if detect_challenge("tiktok", page):
                return ConnectorResultsSubmission(
                    connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
                    status="manual_intervention_required", detail="TikTok запросил challenge/CAPTCHA", items=[],
                )
            direct_links = page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)") if page.query_selector('a[href*="/video/"]') else []
            video_links.extend(direct_links[:4])

        page.goto(f"https://www.tiktok.com/search?q={job.brand}", timeout=NAV_TIMEOUT_MS)

        if detect_challenge("tiktok", page):
            return ConnectorResultsSubmission(
                connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
                status="manual_intervention_required",
                detail="TikTok запросил challenge/CAPTCHA во время навигации - продолжение требует "
                       "ручного шага пользователя в headed-браузере (см. LOCAL_CONNECTOR.md).",
                items=[],
            )

        page.wait_for_timeout(2500)
        if page.query_selector('a[href*="/video/"]'):
            video_links.extend(page.eval_on_selector_all('a[href*="/video/"]', "els => els.map(e => e.href)"))

        items: list[ConnectorResultItem] = []
        for url in list(dict.fromkeys(video_links))[:MAX_VIDEOS_PER_JOB]:
            item = _extract_video(page, url, job.brand, job.aliases, brand_handle=brand_handle)
            if item:
                items.append(item)

        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id, status="ok",
            detail=f"{len(items)} публичных видео найдено по запросу '{job.brand}'", items=items,
        )
    except Exception as exc:  # noqa: BLE001
        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
            status="error", detail=str(exc), items=[],
        )
    finally:
        if browser:
            browser.close()


def _extract_video(page, url: str, brand: str, aliases: list[str], brand_handle: str = "") -> Optional[ConnectorResultItem]:
    try:
        video_page = page.context.new_page()
        video_page.goto(url, timeout=NAV_TIMEOUT_MS)
        video_page.wait_for_timeout(1500)

        caption = _text_or_none(video_page, '[data-e2e="browse-video-desc"], [data-e2e="video-desc"]')
        username = _text_or_none(video_page, '[data-e2e="browse-username"], [data-e2e="video-author-uniqueid"]')
        profile_url = f"https://www.tiktok.com/@{username}" if username else None

        views = _text_or_none(video_page, '[data-e2e="video-views"]')
        likes = _text_or_none(video_page, '[data-e2e="browse-like-count"], [data-e2e="like-count"]')
        comments = _text_or_none(video_page, '[data-e2e="browse-comment-count"], [data-e2e="comment-count"]')

        brand_terms = [brand] + list(aliases or [])
        brand_mention = any(t.lower() in (caption or "").lower() for t in brand_terms if t)
        hashtags = [w for w in (caption or "").split() if w.startswith("#")]

        if brand_handle and username and username.lstrip("@").lower() == brand_handle:
            tagged = [m for m in re.findall(r"@([A-Za-z0-9_.]+)", caption or "") if m.lower() != brand_handle]
            if not tagged:
                video_page.close()
                return None
            username = tagged[0]
            profile_url = f"https://www.tiktok.com/@{username}"
            brand_mention = True

        screenshot_b64 = None
        try:
            screenshot_b64 = base64.b64encode(video_page.screenshot()).decode("ascii")
        except Exception:  # noqa: BLE001
            pass

        result = ConnectorResultItem(
            username=username, profile_url=profile_url, post_url=url, caption=caption,
            views=_to_int(views), likes=_to_int(likes), comments=_to_int(comments), hashtags=hashtags,
            brand_mention=brand_mention, links=[], screenshot_base64=screenshot_b64,
        )
        video_page.close()
        return result
    except Exception:  # noqa: BLE001 - одно видео не должно ронять весь job
        return None


def _text_or_none(page, selector: str) -> Optional[str]:
    el = page.query_selector(selector)
    if not el:
        return None
    text = (el.inner_text() or "").strip()
    return text or None


def _to_int(raw: Optional[str]) -> Optional[int]:
    """TikTok показывает счётчики как '12.3K'/'1.2M' - переводим в int, если
    возможно; если формат не распознан - честно None (не додумываем)."""
    if not raw:
        return None
    text = raw.strip().upper().replace(",", "")
    try:
        if text.endswith("K"):
            return int(float(text[:-1]) * 1_000)
        if text.endswith("M"):
            return int(float(text[:-1]) * 1_000_000)
        return int(float(text))
    except ValueError:
        return None
