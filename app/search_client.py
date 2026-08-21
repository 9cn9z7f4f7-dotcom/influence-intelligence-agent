"""
WebSearchProvider - раздел 5 требований: "не fake search".

Discovery для Articles/Web платформы должен идти через настоящий search API,
а НЕ через скрейпинг HTML поисковых систем (Google/Bing/DuckDuckGo) - это
было бы обходом anti-bot защиты, что проект осознанно не делает (раздел 3,
33 - "никакого stealth anti-bot bypass").

Точечная доработка (Tavily primary / SerpAPI fallback): раньше был один
backend (SerpApiSearchClient). Теперь общий интерфейс WebSearchProvider
(алиас SearchClient сохранён для обратной совместимости импортов) реализуют
ДВА провайдера:

    TavilySearchProvider  - https://tavily.com/, PRIMARY, если задан TAVILY_API_KEY
    SerpApiSearchClient   - https://serpapi.com/, FALLBACK, если задан SERPAPI_KEY

SearchProviderRouter инкапсулирует выбор:
    TAVILY_API_KEY есть  -> использовать Tavily; при ошибке Tavily (timeout/
                            401/quota/невалидный ответ) - честный fallback на
                            SerpAPI, если он настроен;
    иначе SERPAPI_KEY есть -> использовать SerpAPI (текущее поведение);
    иначе                -> get_default_search_client() возвращает
                            NullSearchClient (status="unavailable" для
                            ArticlesPlatformAdapter) - никакого demo/synthetic
                            fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

import httpx

from app.runtime_budget import clamp_timeout

SERPAPI_URL = "https://serpapi.com/search.json"
TAVILY_API_URL = "https://api.tavily.com/search"
DEFAULT_TIMEOUT = 10.0


class SearchProviderError(Exception):
    """Транспортная/API ошибка конкретного провайдера (timeout, 401, quota,
    невалидный JSON/схема ответа). НЕ означает "0 результатов" (это валидный
    исход search()) - именно эта ошибка заставляет SearchProviderRouter
    честно попробовать fallback-провайдера, если он настроен."""


@dataclass
class SearchResultItem:
    url: str
    title: str | None = None
    snippet: str | None = None
    # Доработка Tavily: если провайдер уже вернул достаточный текст, он
    # сохраняется здесь как "search_provider_evidence" - ArticleParser
    # остаётся source of truth для канонического текста страницы, снипет/
    # content из search-провайдера никогда его не подменяет (раздел 4).
    content: str | None = None
    score: float | None = None
    source_provider: str | None = None


class SearchClient(Protocol):
    """Общий интерфейс web-search провайдера. WebSearchProvider - каноничное
    имя из этой доработки; SearchClient оставлен алиасом ради обратной
    совместимости существующих импортов (app/platforms/articles.py и тесты)."""

    source_name: str

    def is_available(self) -> bool: ...

    def search(self, query: str, max_results: int = 10) -> list[SearchResultItem]: ...


WebSearchProvider = SearchClient  # каноничное имя по спецификации доработки


class SerpApiSearchClient:
    """Реальный (не fake) web search через SerpAPI. Теперь используется как
    FALLBACK-провайдер (раньше был единственным)."""

    source_name = "serpapi"

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 10) -> list[SearchResultItem]:
        if not self.is_available():
            return []
        params = {"q": query, "api_key": self.api_key, "engine": "google", "num": max_results}
        resp = httpx.get(SERPAPI_URL, params=params, timeout=clamp_timeout(self.timeout))
        resp.raise_for_status()
        data = resp.json()
        results: list[SearchResultItem] = []
        for item in (data.get("organic_results") or [])[:max_results]:
            url = item.get("link")
            if not url:
                continue
            results.append(SearchResultItem(
                url=url, title=item.get("title"), snippet=item.get("snippet"), source_provider="serpapi",
            ))
        return results


class TavilySearchProvider:
    """PRIMARY web search provider (Tavily Search API). Ключ ТОЛЬКО из
    TAVILY_API_KEY через config.settings - никогда не хардкодится и не
    принимается откуда-либо ещё (симметрично OpenRouterProvider).

    Никогда не бросает исключение наружу молча и никогда не имитирует
    результаты: любая транспортная/API-ошибка (timeout/401/429 quota/
    невалидный JSON или схема ответа) превращается в SearchProviderError,
    которую SearchProviderRouter использует как сигнал "попробовать
    SerpAPI fallback, если он настроен" (раздел 5 доработки)."""

    source_name = "tavily"

    def __init__(self, api_key: str, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str, max_results: int = 10) -> list[SearchResultItem]:
        if not self.is_available():
            return []
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
        }
        try:
            resp = httpx.post(TAVILY_API_URL, json=payload, timeout=clamp_timeout(self.timeout))
        except httpx.HTTPError as exc:  # таймаут/сетевая ошибка
            raise SearchProviderError(f"tavily network error: {exc}") from exc

        if resp.status_code == 401:
            raise SearchProviderError("tavily: invalid TAVILY_API_KEY (401)")
        if resp.status_code == 429:
            raise SearchProviderError("tavily: rate limit / quota exceeded (429)")
        if resp.status_code >= 400:
            raise SearchProviderError(f"tavily: HTTP {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise SearchProviderError(f"tavily: invalid JSON response: {exc}") from exc

        raw_results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(raw_results, list):
            raise SearchProviderError("tavily: unexpected response schema (нет 'results' списка)")

        items: list[SearchResultItem] = []
        for item in raw_results[:max_results]:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not url:
                continue
            content = item.get("content") if isinstance(item.get("content"), str) else None
            items.append(SearchResultItem(
                url=url, title=item.get("title"),
                snippet=(content[:280] if content else None),
                content=content,
                score=item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                source_provider="tavily",
            ))
        return items


class SearchProviderRouter:
    """Tavily PRIMARY / SerpAPI FALLBACK (раздел 5 доработки).

    Логика per-query (не глобально на весь discovery-прогон, чтобы отдельный
    сбой Tavily на одном запросе не выключал его для остальных):
        Tavily настроен и доступен  -> пробуем Tavily;
          при SearchProviderError    -> пробуем SerpAPI, если он настроен;
        иначе если SerpAPI настроен -> используем SerpAPI напрямую;
        если оба провалились/не настроены -> честная ошибка вызывающему коду
          (ArticlesPlatformAdapter уже перехватывает Exception per-query и
          учитывает это как queries_failed -> status="degraded"), НИКОГДА не
          подменяется demo/synthetic результатами.

    last_used_provider - какой провайдер реально ответил на последний search()
    вызов ("tavily"/"serpapi"/None) - используется ArticlesPlatformAdapter,
    чтобы честно показать search_provider в coverage (раздел 6 доработки).
    """

    def __init__(self, tavily: Optional[TavilySearchProvider] = None,
                 serpapi: Optional[SerpApiSearchClient] = None) -> None:
        self.tavily = tavily
        self.serpapi = serpapi
        self.last_used_provider: Optional[str] = None

    @property
    def source_name(self) -> str:
        return self.last_used_provider or "router"

    def is_available(self) -> bool:
        return bool(self.tavily and self.tavily.is_available()) or bool(self.serpapi and self.serpapi.is_available())

    def search(self, query: str, max_results: int = 10) -> list[SearchResultItem]:
        errors: list[str] = []

        if self.tavily is not None and self.tavily.is_available():
            try:
                results = self.tavily.search(query, max_results)
                self.last_used_provider = "tavily"
                return results
            except SearchProviderError as exc:
                errors.append(str(exc))  # honest fallback ниже, если SerpAPI настроен

        if self.serpapi is not None and self.serpapi.is_available():
            try:
                results = self.serpapi.search(query, max_results)
                self.last_used_provider = "serpapi"
                return results
            except Exception as exc:  # noqa: BLE001 - SerpAPI бросает httpx-исключения, не SearchProviderError
                errors.append(str(exc))

        self.last_used_provider = None
        if errors:
            # НЕ падать всему Analyze - вызывающий код (ArticlesPlatformAdapter)
            # перехватывает эту ошибку per-query и честно отражает её в
            # queries_failed/status="degraded", а не подменяет synthetic данными.
            raise SearchProviderError("; ".join(errors))
        return []


class NullSearchClient:
    """Честный 'unavailable' client - когда ни TAVILY_API_KEY, ни SERPAPI_KEY
    не заданы. Никогда не возвращает выдуманные результаты."""

    source_name = "none"

    def is_available(self) -> bool:
        return False

    def search(self, query: str, max_results: int = 10) -> list[SearchResultItem]:
        return []


def get_default_search_client(settings=None) -> SearchClient:
    """Tavily PRIMARY / SerpAPI FALLBACK / unavailable (раздел 5 доработки):

        TAVILY_API_KEY задан  -> SearchProviderRouter(tavily=Tavily, serpapi=<SerpAPI если задан>)
        elif SERPAPI_KEY задан -> SearchProviderRouter(tavily=None, serpapi=SerpAPI) (тот же router,
                                   чтобы coverage.search_provider всегда честно проставлялся)
        else                   -> NullSearchClient() (status="unavailable", без demo/synthetic fallback)
    """
    from config.settings import settings as default_settings
    cfg = settings or default_settings

    tavily_key = getattr(cfg, "tavily_api_key", "")
    serpapi_key = getattr(cfg, "serpapi_key", "")

    if not tavily_key and not serpapi_key:
        return NullSearchClient()

    tavily = TavilySearchProvider(tavily_key) if tavily_key else None
    serpapi = SerpApiSearchClient(serpapi_key) if serpapi_key else None
    return SearchProviderRouter(tavily=tavily, serpapi=serpapi)
