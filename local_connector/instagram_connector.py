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
import re
from pathlib import Path
from typing import Optional

from app.connectors.models import ConnectorJob, ConnectorResultItem, ConnectorResultsSubmission
from local_connector.social_auth import detect_challenge, ensure_authenticated_context

SEARCH_LEVEL_TARGETS = {"light": 30, "standard": 60, "deep": 100}
NAV_TIMEOUT_MS = 20_000


def _target_for_job(job: ConnectorJob) -> int:
    level = str((job.settings or {}).get("search_level") or "light").lower()
    return SEARCH_LEVEL_TARGETS.get(level, 30)


def _collect_post_links(page, target: int, max_scrolls: int = 12) -> list[str]:
    """Collect real rendered Instagram post/reel URLs with bounded scrolling."""
    seen: list[str] = []
    for _ in range(max_scrolls + 1):
        try:
            urls = page.eval_on_selector_all(
                'a[href*="/p/"], a[href*="/reel/"]',
                "els => els.map(e => e.href).filter(Boolean)",
            )
        except Exception:
            urls = []
        for url in urls:
            if url not in seen:
                seen.append(url)
                if len(seen) >= target:
                    return seen
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1200)
        except Exception:
            break
    return seen


def handle_job(job: ConnectorJob, connector_id: str, connector_token: str, playwright,
                state_path: Path) -> ConnectorResultsSubmission:
    browser = None
    try:
        browser, context, page = ensure_authenticated_context("instagram", state_path, playwright, headless_if_authenticated=False)
    except Exception as exc:  # noqa: BLE001 - не роняем весь connector process из-за одного job
        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
            status="error", detail=f"не удалось открыть authenticated browser: {exc}", items=[],
        )

    try:
        source_url = (job.settings or {}).get("brand_source_url")
        brand_handle = ((job.settings or {}).get("brand_handle") or "").lstrip("@").lower()
        post_links: list[tuple[str, str]] = []  # (url, relation_hint)
        target = _target_for_job(job)

        # Direct brand-account relationships have priority when the user supplied
        # an Instagram profile URL. We only read publicly rendered posts/reels.
        # If the UI did not pass a URL but the brand itself looks like an
        # Instagram handle, try the native profile first. A 404 simply falls
        # through to search; no synthetic data is created.
        if not source_url and re.fullmatch(r"[A-Za-z0-9_.]{2,30}", str(job.brand or "")):
            source_url = f"https://www.instagram.com/{str(job.brand).lstrip('@')}/"
            if not brand_handle:
                brand_handle = str(job.brand).lstrip("@").lower()

        if source_url and "instagram.com" in source_url:
            page.goto(source_url, timeout=NAV_TIMEOUT_MS)
            page.wait_for_timeout(1500)
            if detect_challenge("instagram", page):
                return ConnectorResultsSubmission(
                    connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
                    status="manual_intervention_required", detail="Instagram запросил challenge/CAPTCHA", items=[],
                )
            direct_links = _collect_post_links(page, target=max(18, target), max_scrolls=12)
            post_links.extend((u, "brand_post") for u in direct_links)

            # Instagram exposes posts where the brand was tagged under /tagged/.
            # These are direct brand relationships and are more valuable than
            # generic keyword search.  Best-effort only: if the tab/layout is not
            # available we simply continue to search fallback.
            tagged_url = source_url.rstrip("/") + "/tagged/"
            try:
                page.goto(tagged_url, timeout=NAV_TIMEOUT_MS)
                page.wait_for_timeout(1500)
                if page.query_selector('a[href*="/p/"], a[href*="/reel/"]'):
                    tagged_links = _collect_post_links(page, target=max(18, target), max_scrolls=10)
                    post_links.extend((u, "tagged_brand") for u in tagged_links)
            except Exception:
                pass

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
        if page.query_selector('a[href*="/p/"], a[href*="/reel/"]'):
            search_links = _collect_post_links(page, target=target, max_scrolls=10)
            post_links.extend((u, "search") for u in search_links)

        # Deduplicate by URL while preserving the strongest provenance.
        priority = {"tagged_brand": 3, "brand_post": 2, "search": 1}
        dedup: dict[str, str] = {}
        for url, relation in post_links:
            if url not in dedup or priority.get(relation, 0) > priority.get(dedup[url], 0):
                dedup[url] = relation

        items: list[ConnectorResultItem] = []
        for url, relation_hint in list(dedup.items())[:target]:
            item = _extract_post(page, url, job.brand, job.aliases, brand_handle=brand_handle, relation_hint=relation_hint)
            if item:
                items.append(item)

        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id, status="ok",
            detail=f"{len(items)} публичных материалов собрано (цель {target}) по запросу '{job.brand}'", items=items,
        )
    except Exception as exc:  # noqa: BLE001
        return ConnectorResultsSubmission(
            connector_id=connector_id, connector_token=connector_token, job_id=job.job_id,
            status="error", detail=str(exc), items=[],
        )
    finally:
        try:
            if 'context' in locals() and context is not None:
                context.storage_state(path=str(state_path))
        except Exception:
            pass
        if browser:
            browser.close()


def _extract_post(page, url: str, brand: str, aliases: list[str], brand_handle: str = "", relation_hint: str = "search") -> Optional[ConnectorResultItem]:
    try:
        post_page = page.context.new_page()
        post_page.goto(url, timeout=NAV_TIMEOUT_MS)
        post_page.wait_for_timeout(1500)

        caption = _text_or_none(post_page, "article h1")
        if not caption:
            # Instagram DOM changes often; og:description is much more stable and
            # still comes from the real rendered page metadata.
            try:
                caption = post_page.locator('meta[property="og:description"]').get_attribute("content")
            except Exception:
                caption = None

        username = _text_or_none(post_page, "header a")
        if username:
            username = username.strip().lstrip("@")
        if not username:
            try:
                og_title = post_page.locator('meta[property="og:title"]').get_attribute("content") or ""
                m_user = re.search(r"@?([A-Za-z0-9_.]+)\s+(?:on Instagram|• Instagram)", og_title)
                if m_user:
                    username = m_user.group(1)
            except Exception:
                pass
        profile_url = None
        header_link = post_page.query_selector("header a")
        if header_link:
            href = header_link.get_attribute("href")
            if href:
                profile_url = f"https://www.instagram.com{href}" if href.startswith("/") else href

        # Collect real profile anchors rendered inside the post.  This lets a
        # brand-owned collab/tagged post resolve the non-brand creator even when
        # the username is not repeated in the caption.
        profile_candidates: list[str] = []
        try:
            hrefs = post_page.eval_on_selector_all(
                'article a[href^="/"], header a[href^="/"]',
                "els => els.map(e => e.getAttribute('href')).filter(Boolean)",
            )
            blocked = {"explore", "accounts", "direct", "reels", "stories", "about", "legal"}
            for href in hrefs:
                parts = str(href).strip("/").split("/")
                if len(parts) != 1:
                    continue
                handle = parts[0].lower()
                if handle and handle not in blocked and handle != brand_handle and handle not in profile_candidates:
                    profile_candidates.append(handle)
        except Exception:
            pass

        page_text = post_page.inner_text("body") if post_page.query_selector("body") else ""
        lowered = page_text.lower()
        paid_partnership = "paid partnership" in lowered or "платное партнёрство" in lowered
        collaboration = "collaboration" in lowered or "совместно с" in lowered or "collab" in lowered
        brand_terms = [brand] + list(aliases or [])
        brand_mention = any(t.lower() in (caption or "").lower() for t in brand_terms if t)
        hashtags = [w for w in (caption or "").split() if w.startswith("#")]

        # Direct relationship provenance from the brand account is a hard
        # platform-native signal for this product: posts from the brand that tag
        # a creator, and posts from the brand's /tagged/ tab, should surface as
        # people already connected to the brand.
        current_handle = username.lstrip("@").lower() if username else ""
        if relation_hint == "tagged_brand":
            brand_mention = True
            collaboration = True

        if brand_handle and current_handle == brand_handle:
            caption_tags = [
                m for m in re.findall(r"@([A-Za-z0-9_.]+)", caption or "")
                if m.lower() != brand_handle
            ]
            tagged = caption_tags or profile_candidates
            if tagged:
                username = tagged[0]
                profile_url = f"https://www.instagram.com/{username}/"
                brand_mention = True
                collaboration = True
            else:
                # Brand-owned post is still a real finding. Keep the content,
                # but do not turn the brand account itself into a hunting creator.
                username = None
                profile_url = None
                brand_mention = True

        screenshot_b64 = None
        try:
            screenshot_b64 = base64.b64encode(post_page.screenshot()).decode("ascii")
        except Exception:  # noqa: BLE001 - screenshot - best effort, не критично
            pass

        external_links: list[str] = []
        try:
            external_links = post_page.eval_on_selector_all(
                'a[href^="http"]',
                "els => els.map(e => e.href).filter(h => !h.includes('instagram.com'))",
            )[:10]
        except Exception:
            pass

        result = ConnectorResultItem(
            username=username, profile_url=profile_url, post_url=url, caption=caption, hashtags=hashtags,
            brand_mention=brand_mention, paid_partnership_label=paid_partnership,
            collaboration_label=collaboration, links=external_links, screenshot_base64=screenshot_b64,
            discovery_context=relation_hint,
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
