"""
ArticleParser - раздел 6 требований.

Для обычных страниц: HTTP (httpx) + main-text extraction через BeautifulSoup
(тот же подход, что app/ingestion/web_adapter.py, но с полным набором полей:
title, canonical_url, publication/domain, author, published_at, main_text,
outbound_links, image_urls, metadata, observed_at).

Для JS-страниц (main_text из HTTP-фетча подозрительно короткий - явный признак
контента, отрендеренного клиентским JS) - Playwright fallback: получаем html
ПОСЛЕ рендеринга и парсим тем же способом.

Не использует OCR - текст уже доступен в DOM (раздел 6: "Не использовать OCR,
если текст уже доступен в DOM"). Скриншот+vision - отдельный enrichment слой
(app/enrichment/*), вызывается только для ambiguous-детекций, не здесь.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse
import json

from app.runtime_budget import budget_exhausted, clamp_timeout, remaining_seconds

import httpx
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": "InfluenceIntelligenceAgent/1.0 (+articles-connector; respects robots.txt)"
}
JS_FALLBACK_MIN_TEXT_LEN = 200
DEFAULT_REQUEST_TIMEOUT = 10.0
PLAYWRIGHT_TIMEOUT_MS = 15_000


@dataclass
class ArticleParseResult:
    source_url: str
    canonical_url: Optional[str] = None
    title: Optional[str] = None
    domain: Optional[str] = None
    author: Optional[str] = None
    published_at: Optional[datetime] = None
    main_text: str = ""
    outbound_links: list[str] = field(default_factory=list)
    image_urls: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fetch_mode: str = "http"  # http | playwright
    status: str = "ok"  # ok | degraded
    error: Optional[str] = None


def _meta(soup: BeautifulSoup, *names: str) -> Optional[str]:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _parse_html(html: str, url: str, fetch_mode: str) -> ArticleParseResult:
    soup = BeautifulSoup(html, "html.parser")
    domain = urlparse(url).netloc

    title = _meta(soup, "og:title") or (soup.title.string.strip() if soup.title and soup.title.string else None)
    canonical_tag = soup.find("link", rel="canonical")
    canonical_url = canonical_tag["href"].strip() if canonical_tag and canonical_tag.get("href") else url
    author = _meta(soup, "author", "article:author", "og:article:author")

    published_at = _parse_iso(_meta(soup, "article:published_time", "og:article:published_time", "date", "pubdate"))
    if published_at is None:
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            published_at = _parse_iso(time_tag["datetime"])

    paragraph_nodes = soup.find_all("p")
    paragraphs = [p.get_text(" ", strip=True) for p in paragraph_nodes]
    nonempty_paragraphs = [t for t in paragraphs if t]
    main_text = " ".join(nonempty_paragraphs)[:8000]

    # Lightweight page-type signals for the Articles content gate.  These are
    # deterministic DOM/metadata facts, not AI guesses.
    og_type = (_meta(soup, "og:type") or "").strip().lower()
    has_article_tag = soup.find("article") is not None
    schema_types: set[str] = set()
    for node in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = node.string or node.get_text() or ""
        if not raw.strip():
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        stack = payload if isinstance(payload, list) else [payload]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                typ = item.get("@type")
                if isinstance(typ, str):
                    schema_types.add(typ.lower())
                elif isinstance(typ, list):
                    schema_types.update(str(x).lower() for x in typ)
                graph = item.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
            elif isinstance(item, list):
                stack.extend(item)

    outbound_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("http") and urlparse(href).netloc and urlparse(href).netloc != domain:
            outbound_links.append(href)

    image_urls: list[str] = []
    og_image = _meta(soup, "og:image")
    if og_image:
        image_urls.append(og_image)
    for img in soup.find_all("img", src=True):
        src = img["src"]
        if src.startswith("http"):
            image_urls.append(src)

    return ArticleParseResult(
        source_url=url, canonical_url=canonical_url, title=title, domain=domain, author=author,
        published_at=published_at, main_text=main_text,
        outbound_links=list(dict.fromkeys(outbound_links))[:50],
        image_urls=list(dict.fromkeys(image_urls))[:20],
        metadata={
            "description": _meta(soup, "og:description", "description"),
            "og_type": og_type,
            "has_article_tag": has_article_tag,
            "paragraph_count": len(nonempty_paragraphs),
            "schema_types": sorted(schema_types),
        },
        fetch_mode=fetch_mode, status="ok",
    )


class ArticleParser:
    def __init__(self, timeout: float = DEFAULT_REQUEST_TIMEOUT, enable_playwright_fallback: bool = True) -> None:
        self.timeout = timeout
        self.enable_playwright_fallback = enable_playwright_fallback

    def parse(self, url: str) -> ArticleParseResult:
        try:
            resp = httpx.get(url, headers=DEFAULT_HEADERS, timeout=clamp_timeout(self.timeout), follow_redirects=True)
            resp.raise_for_status()
            result = _parse_html(resp.text, url, fetch_mode="http")
        except httpx.HTTPStatusError as exc:
            return ArticleParseResult(source_url=url, status="degraded", error=f"HTTP {exc.response.status_code}")
        except Exception as exc:  # noqa: BLE001 - парсер не должен ронять discovery
            return ArticleParseResult(source_url=url, status="degraded", error=str(exc))

        if self.enable_playwright_fallback and len(result.main_text) < JS_FALLBACK_MIN_TEXT_LEN and not budget_exhausted(30):
            rendered_html = self._fetch_via_playwright(url)
            if rendered_html is not None:
                rendered_result = _parse_html(rendered_html, url, fetch_mode="playwright")
                if len(rendered_result.main_text) > len(result.main_text):
                    result = rendered_result

        return result

    def _fetch_via_playwright(self, url: str) -> Optional[str]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    remaining = remaining_seconds()
                    timeout_ms = PLAYWRIGHT_TIMEOUT_MS
                    if remaining is not None:
                        timeout_ms = max(1000, min(PLAYWRIGHT_TIMEOUT_MS, int(max(1.0, remaining - 1.0) * 1000)))
                    page.goto(url, timeout=timeout_ms, wait_until="load")
                    return page.content()
                finally:
                    browser.close()
        except Exception:  # noqa: BLE001 - JS fallback - best effort, никогда не роняет parse()
            return None
