"""
Точечная доработка: Tavily PRIMARY search provider для Articles/Web discovery,
SerpAPI - FALLBACK (раньше был единственным backend-ом). 8 тестов по разделу 8
задания. Не трогает UI/аналитику/Instagram/TikTok/OpenRouter - только
app/search_client.py, app/platforms/articles.py, app/platforms/base.py,
app/analysis/models.py::PlatformCoverage, config/settings.py.

Эти тесты - mocked/unit-тесты (в этой среде нет живого доступа к api.tavily.com/
serpapi.com - см. финальный отчёт REAL LIVE VALIDATION). Они проверяют, что КОД
реализует заявленную логику (primary/fallback, нормализацию, дедуп, честный
"unavailable" без демо/synthetic данных, отсутствие падений, coverage).
"""
from __future__ import annotations

import os
from unittest.mock import patch

import httpx
import pytest

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.article_parser import ArticleParseResult
from app.platforms.articles import ArticlesPlatformAdapter
from app.search_client import (
    NullSearchClient,
    SearchProviderError,
    SearchProviderRouter,
    SearchResultItem,
    SerpApiSearchClient,
    TavilySearchProvider,
    get_default_search_client,
)
from config.settings import Settings


def _brand(name: str = "Автор24") -> ResolvedBrand:
    return ResolvedBrand(brand_name=name, canonical_name=name, aliases=[], input_type="name")


def _settings(tavily: str = "", serpapi: str = "") -> Settings:
    cfg = Settings()
    cfg.tavily_api_key = tavily
    cfg.serpapi_key = serpapi
    return cfg


# ---------------------------------------------------------------------------
# 1. Tavily используется, когда задан TAVILY_API_KEY
# ---------------------------------------------------------------------------


def test_1_tavily_used_as_primary_when_key_present():
    cfg = _settings(tavily="tav-key", serpapi="serp-key")
    client = get_default_search_client(cfg)
    assert isinstance(client, SearchProviderRouter)
    assert client.tavily is not None and client.tavily.api_key == "tav-key"
    assert client.serpapi is not None

    fake_items = [SearchResultItem(url="https://x.example.com/a", source_provider="tavily")]
    with patch.object(client.tavily, "search", return_value=fake_items) as tavily_search, \
         patch.object(client.serpapi, "search") as serpapi_search:
        results = client.search("Автор24 обзор", max_results=5)

    assert results == fake_items
    tavily_search.assert_called_once()
    serpapi_search.assert_not_called()
    assert client.last_used_provider == "tavily"


# ---------------------------------------------------------------------------
# 2. SerpAPI fallback, когда Tavily недоступен/упал
# ---------------------------------------------------------------------------


def test_2_serpapi_fallback_when_tavily_errors():
    cfg = _settings(tavily="tav-key", serpapi="serp-key")
    client = get_default_search_client(cfg)

    fake_items = [SearchResultItem(url="https://y.example.com/b", source_provider="serpapi")]
    with patch.object(client.tavily, "search", side_effect=SearchProviderError("tavily: HTTP 401")), \
         patch.object(client.serpapi, "search", return_value=fake_items) as serpapi_search:
        results = client.search("Автор24 промокод", max_results=5)

    assert results == fake_items
    serpapi_search.assert_called_once()
    assert client.last_used_provider == "serpapi"


def test_2b_serpapi_used_directly_when_only_serpapi_key_present():
    """Без TAVILY_API_KEY - прежнее поведение (SerpAPI как единственный backend)
    полностью сохранено, просто через тот же router."""
    cfg = _settings(tavily="", serpapi="serp-key")
    client = get_default_search_client(cfg)
    assert isinstance(client, SearchProviderRouter)
    assert client.tavily is None
    assert isinstance(client.serpapi, SerpApiSearchClient)

    fake_items = [SearchResultItem(url="https://z.example.com/c", source_provider="serpapi")]
    with patch.object(client.serpapi, "search", return_value=fake_items):
        results = client.search("Автор24 партнер")
    assert results == fake_items
    assert client.last_used_provider == "serpapi"


# ---------------------------------------------------------------------------
# 3. Ни TAVILY_API_KEY, ни SERPAPI_KEY -> articles honestly unavailable
# ---------------------------------------------------------------------------


def test_3_no_keys_returns_null_client_and_articles_unavailable():
    cfg = _settings(tavily="", serpapi="")
    client = get_default_search_client(cfg)
    assert isinstance(client, NullSearchClient)
    assert client.is_available() is False

    adapter = ArticlesPlatformAdapter(search_client=client, settings=cfg)
    result = adapter.discover_brand_content(_brand(), AnalysisConfig())
    assert result.status == "unavailable"
    assert result.source_mode == "none"
    assert result.raw_items == []
    assert result.search_provider is None  # никакого демо/synthetic - просто честный unavailable


# ---------------------------------------------------------------------------
# 4. Tavily results нормализованы в общую SearchResultItem-схему
# ---------------------------------------------------------------------------


def test_4_tavily_results_are_normalized_to_search_result_item():
    provider = TavilySearchProvider(api_key="tav-key")
    long_content = "Автор24 обзор промокод текст статьи " * 10
    fake_response_json = {
        "results": [
            {"url": "https://news.example.com/a", "title": "A", "content": long_content, "score": 0.91},
            {"url": "https://news.example.com/b", "title": "B", "content": "short"},
        ]
    }

    class _FakeResp:
        status_code = 200

        def json(self):
            return fake_response_json

    with patch("httpx.post", return_value=_FakeResp()):
        items = provider.search("Автор24 обзор", max_results=10)

    assert len(items) == 2
    first = items[0]
    assert first.url == "https://news.example.com/a"
    assert first.title == "A"
    assert first.source_provider == "tavily"
    assert first.content == long_content
    assert first.snippet is not None and len(first.snippet) <= 280
    assert first.score == 0.91


def test_4b_tavily_error_statuses_raise_search_provider_error_not_generic():
    provider = TavilySearchProvider(api_key="tav-key")

    class _Resp401:
        status_code = 401

    with patch("httpx.post", return_value=_Resp401()):
        with pytest.raises(SearchProviderError):
            provider.search("q")

    class _Resp429:
        status_code = 429

    with patch("httpx.post", return_value=_Resp429()):
        with pytest.raises(SearchProviderError):
            provider.search("q")

    with patch("httpx.post", side_effect=httpx.ConnectTimeout("timeout")):
        with pytest.raises(SearchProviderError):
            provider.search("q")


# ---------------------------------------------------------------------------
# 5. Дедупликация URL между разными query
# ---------------------------------------------------------------------------


def test_5_duplicate_urls_across_queries_are_deduplicated():
    calls = {"n": 0}

    class _FakeSearchClient:
        source_name = "fake"
        last_used_provider = "tavily"

        def is_available(self):
            return True

        def search(self, query, max_results=10):
            calls["n"] += 1
            # Разные запросы возвращают один и тот же URL - должен остаться один candidate.
            return [SearchResultItem(url="https://dup.example.com/page", title=query, source_provider="tavily")]

    parsed = ArticleParseResult(
        source_url="https://dup.example.com/page", canonical_url="https://dup.example.com/page",
        domain="dup.example.com", main_text="Автор24 текст статьи достаточной длины " * 5,
    )
    adapter = ArticlesPlatformAdapter(search_client=_FakeSearchClient())
    with patch.object(adapter.parser, "parse", return_value=parsed) as parse_mock:
        result = adapter.discover_brand_content(_brand(), AnalysisConfig())

    assert calls["n"] > 1  # несколько разных query реально ушли в поиск
    assert parse_mock.call_count == 1  # но URL дедуплицирован до одного кандидата
    assert len(result.raw_items) == 1


# ---------------------------------------------------------------------------
# 6. Ошибка Tavily не должна ронять весь Analyze
# ---------------------------------------------------------------------------


def test_6_tavily_error_without_serpapi_degrades_but_does_not_raise():
    cfg = _settings(tavily="tav-key", serpapi="")
    client = get_default_search_client(cfg)
    adapter = ArticlesPlatformAdapter(search_client=client, settings=cfg)

    with patch.object(client.tavily, "search", side_effect=SearchProviderError("tavily: HTTP 500")):
        result = adapter.discover_brand_content(_brand(), AnalysisConfig())  # не должно бросить исключение

    assert result.status == "degraded"
    assert result.raw_items == []
    assert result.search_provider is None


def test_6b_full_analyze_endpoint_does_not_crash_when_tavily_configured_and_failing():
    from fastapi.testclient import TestClient

    import config.settings as settings_module
    from app.api.server import app

    original = settings_module.settings.tavily_api_key
    settings_module.settings.tavily_api_key = "tav-key"
    try:
        with patch("app.search_client.TavilySearchProvider.search",
                    side_effect=SearchProviderError("tavily down")):
            client = TestClient(app)
            resp = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["articles"]})
        assert resp.status_code == 200  # оркестрация не падает даже если Tavily полностью недоступен
        body = client.get(f"/api/analysis/{resp.json()['analysis_id']}").json()
        articles_cov = next(p for p in body["coverage"]["platforms"] if p["platform"] == "articles")
        assert articles_cov["status"] == "degraded"
    finally:
        settings_module.settings.tavily_api_key = original


# ---------------------------------------------------------------------------
# 7. Coverage честно показывает search_provider=tavily
# ---------------------------------------------------------------------------


def test_7_discovery_result_exposes_search_provider_tavily():
    cfg = _settings(tavily="tav-key", serpapi="")
    client = get_default_search_client(cfg)
    adapter = ArticlesPlatformAdapter(search_client=client, settings=cfg)

    fake_items = [SearchResultItem(url="https://news.example.com/nike", title="Nike", source_provider="tavily")]
    parsed = ArticleParseResult(
        source_url="https://news.example.com/nike", canonical_url="https://news.example.com/nike",
        domain="news.example.com", main_text="Nike на правах рекламы " * 5,
    )
    with patch.object(client.tavily, "search", return_value=fake_items), \
         patch.object(adapter.parser, "parse", return_value=parsed):
        result = adapter.discover_brand_content(_brand("Nike"), AnalysisConfig())

    assert result.status == "ok"
    assert result.search_provider == "tavily"


def test_7b_platform_coverage_propagates_search_provider_from_pipeline():
    from app.analysis.pipeline import stage_discover_and_extract
    from app.evidence import EvidenceStore
    from app.platforms import get_platform_adapter as real_get_platform_adapter

    cfg = _settings(tavily="tav-key", serpapi="")
    client = get_default_search_client(cfg)
    adapter = ArticlesPlatformAdapter(search_client=client, settings=cfg)

    fake_items = [SearchResultItem(url="https://news.example.com/nike2", title="Nike", source_provider="tavily")]
    parsed = ArticleParseResult(
        source_url="https://news.example.com/nike2", canonical_url="https://news.example.com/nike2",
        domain="news.example.com", main_text="Nike на правах рекламы " * 5,
    )

    def _fake_get_adapter(platform: str):
        return adapter if platform == "articles" else real_get_platform_adapter(platform)

    with patch.object(client.tavily, "search", return_value=fake_items), \
         patch.object(adapter.parser, "parse", return_value=parsed), \
         patch("app.analysis.pipeline.get_platform_adapter", side_effect=_fake_get_adapter):
        coverage, creators, confirmed, organic, manual_review, publishers = stage_discover_and_extract(
            "articles", _brand("Nike"), "comp1", AnalysisConfig(min_integration_confidence=0.0), EvidenceStore(),
        )

    assert coverage.status == "ok"
    assert coverage.search_provider == "tavily"
    # Пример из раздела 6 задания: {"articles": {"status": "live"/"ok", "items_found": N, "search_provider": "tavily"}}
    assert coverage.items_collected == len(confirmed) + len(organic) + len(manual_review) or coverage.items_collected >= 1


# ---------------------------------------------------------------------------
# 8. Обычный Analyze всё равно никогда не использует demo (регрессия)
# ---------------------------------------------------------------------------


def test_8_normal_analyze_still_never_uses_demo_with_tavily_wired_in():
    from fastapi.testclient import TestClient

    from app.api.server import app

    client = TestClient(app)
    # В этой тестовой среде ни TAVILY_API_KEY, ни SERPAPI_KEY не заданы -
    # articles должен честно быть unavailable, а НЕ подмениться demo-данными.
    resp = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["youtube", "articles"]})
    assert resp.status_code == 200
    body = client.get(f"/api/analysis/{resp.json()['analysis_id']}").json()

    for platform_cov in body["coverage"]["platforms"]:
        assert platform_cov["source_mode"] != "demo"
        if platform_cov["platform"] == "articles":
            assert platform_cov["status"] == "unavailable"
            assert platform_cov.get("search_provider") is None
