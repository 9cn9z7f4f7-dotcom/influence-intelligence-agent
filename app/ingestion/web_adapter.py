"""
Generic web-адаптер.

Достаёт title, основной текст, ссылки, source_url, observed_at из
произвольной публичной страницы. Используется как fallback-источник,
когда для платформы нет специализированного API.

Не обходит авторизацию, CAPTCHA или platform protections - если страница
недоступна/защищена, адаптер просто помечает источник degraded и идёт дальше.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup

from app.health import health_registry
from app.ingestion.base import BaseAdapter, IngestionResult

DEFAULT_HEADERS = {
    "User-Agent": "InfluenceIntelligenceAgent/1.0 (+hackathon-mvp; respects robots.txt)"
}


class WebAdapter(BaseAdapter):
    source_name = "web"

    def __init__(self, timeout: float = 8.0) -> None:
        self.timeout = timeout

    def is_available(self) -> bool:
        return True

    def fetch_page(self, url: str) -> dict:
        resp = httpx.get(url, headers=DEFAULT_HEADERS, timeout=self.timeout, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        title = soup.title.string.strip() if soup.title and soup.title.string else None
        # Основной текст: собираем видимые параграфы, режем до разумной длины
        paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        main_text = " ".join(t for t in paragraphs if t)[:4000]

        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http"):
                links.append(href)

        return {
            "title": title,
            "main_text": main_text,
            "links": links[:50],
            "source_url": url,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def fetch(self, url: str = "", **kwargs) -> IngestionResult:
        result = IngestionResult()
        if not url:
            result.notes.append("web adapter: url не передан")
            health_registry.ok(self.source_name, "нет запроса - источник простаивает")
            return result
        try:
            page = self._run_with_retries(self.fetch_page, url)
            result.notes.append(f"OK: {page['title']} ({url})")
            health_registry.ok(self.source_name, f"страница '{page.get('title')}' получена")
        except httpx.HTTPStatusError as exc:
            health_registry.degraded(self.source_name, f"HTTP {exc.response.status_code} на {url}")
            result.notes.append(f"web adapter degraded (HTTP): {exc}")
        except Exception as exc:  # noqa: BLE001
            health_registry.degraded(self.source_name, f"ошибка запроса {url}: {exc}")
            result.notes.append(f"web adapter degraded: {exc}")
        return result
