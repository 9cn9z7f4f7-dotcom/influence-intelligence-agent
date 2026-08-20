from __future__ import annotations

import httpx
import pytest

from app.evidence import EvidenceStore
from app.health import health_registry
from app.ingestion.live_youtube import (
    CompetitorQueryBuilder,
    DiscoveryResult,
    IntegrationDetector,
    build_creator_from_channel,
    build_integration,
    discover_videos,
    run_youtube_ingestion,
)
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import SourceMode
from config.settings import Settings


# ---------------------------------------------------------------------------
# Fixtures: минимальные валидные YouTube API ответы (без сети).
# ---------------------------------------------------------------------------

def _search_item(video_id: str, title: str, description: str, channel_id: str = "ch1",
                  published_at: str = "2026-07-01T00:00:00Z") -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {
            "title": title, "description": description, "channelId": channel_id,
            "channelTitle": "Test Channel", "publishedAt": published_at,
        },
    }


def _channel_item(channel_id: str = "ch1", subscriber_count: str = "15000") -> dict:
    return {
        "id": channel_id,
        "snippet": {"title": "Test Channel", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": subscriber_count},
    }


def _video_stats_item(video_id: str = "vid1", view_count: str = "5000") -> dict:
    return {
        "id": video_id,
        "snippet": {"title": "x", "description": "y", "publishedAt": "2026-07-01T00:00:00Z"},
        "statistics": {"viewCount": view_count},
    }


@pytest.fixture
def fake_adapter(monkeypatch) -> YouTubeAdapter:
    adapter = YouTubeAdapter(api_key="fake-key-for-tests")
    return adapter


# ---------------------------------------------------------------------------
# 1. Query builder
# ---------------------------------------------------------------------------

def test_query_builder_includes_name_aliases_and_keywords():
    qb = CompetitorQueryBuilder("Автор24", aliases=["Author24"], brand_keywords=["промокод", "реклама"])
    queries = qb.build_queries()
    assert "Автор24" in queries
    assert "Author24" in queries
    assert "Автор24 промокод" in queries
    assert "Автор24 реклама" in queries
    assert "Author24 промокод" in queries


def test_query_builder_deduplicates_case_insensitively():
    qb = CompetitorQueryBuilder("Brand", aliases=["brand", "BRAND"], brand_keywords=[])
    queries = qb.build_queries()
    assert len(queries) == 1  # все варианты совпадают без учёта регистра


# ---------------------------------------------------------------------------
# 2 & 3. Detector positive / negative
# ---------------------------------------------------------------------------

def test_detector_positive_case(settings: Settings):
    detector = IntegrationDetector(settings)
    result = detector.detect(
        title="Автор24 - обзор сервиса, промокод внутри! #реклама",
        description="Ссылка: https://avtor24.ru/promo Используй промокод AVT2026. На правах рекламы.",
        brand_terms=["Автор24"],
    )
    assert result.is_integration is True
    assert result.confidence >= settings.live_integration_confidence_threshold
    assert "brand_in_title" in result.reasons
    assert "promo_code" in result.reasons
    assert result.category == "confirmed"
    assert result.has_brand_evidence is True
    assert result.has_commercial_evidence is True


def test_detector_negative_case(settings: Settings):
    detector = IntegrationDetector(settings)
    result = detector.detect(
        title="Как я сдавала сессию без стресса",
        description="Обычное мотивационное видео, без брендов.",
        brand_terms=["Автор24"],
    )
    assert result.is_integration is False
    assert result.confidence == 0.0
    assert result.reasons == []
    assert result.category == "rejected"
    assert result.has_brand_evidence is False


# ---------------------------------------------------------------------------
# 4. Brand evidence + commercial evidence, но низкий confidence -> manual_review
#    (не создаётся confirmed Integration автоматически)
# ---------------------------------------------------------------------------

def test_low_confidence_with_commercial_signal_goes_to_manual_review(monkeypatch, fake_adapter, settings: Settings):
    monkeypatch.setattr(
        fake_adapter, "search_videos",
        lambda query, max_results: [_search_item(
            "vid_weak", "Обзор дня",
            "Сегодня расскажу про Автор24, ссылка в описании чтобы узнать больше.",
        )],
    )
    report = run_youtube_ingestion(
        competitor_id="comp_test", competitor_name="Автор24", adapter=fake_adapter, settings=settings,
    )
    assert report.confirmed_integrations == []
    assert len(report.manual_review_candidates) == 1
    assert report.manual_review_candidates[0]["status"] == "candidate_manual_review"
    assert report.manual_review_candidates[0]["category"] == "manual_review"
    assert report.videos_filtered_out == 1


# ---------------------------------------------------------------------------
# 4b. Brand evidence БЕЗ commercial evidence -> organic_mention
#     (никогда не должно попадать в confirmed_integrations)
# ---------------------------------------------------------------------------

def test_brand_mention_without_commercial_signal_is_organic_mention(monkeypatch, fake_adapter, settings: Settings):
    monkeypatch.setattr(
        fake_adapter, "search_videos",
        lambda query, max_results: [_search_item("vid_organic", "Автор24 в новостях", "Просто упоминание без деталей.")],
    )
    monkeypatch.setattr(fake_adapter, "get_channel_stats", lambda channel_id: _channel_item(channel_id))
    monkeypatch.setattr(fake_adapter, "get_video_stats", lambda video_id: _video_stats_item(video_id))

    report = run_youtube_ingestion(
        competitor_id="comp_test", competitor_name="Автор24", adapter=fake_adapter, settings=settings,
    )
    assert report.confirmed_integrations == []
    assert report.manual_review_candidates == []
    assert len(report.organic_mentions) == 1
    assert report.organic_mentions[0].category == "organic_mention"


# ---------------------------------------------------------------------------
# 5. Creator creation
# ---------------------------------------------------------------------------

def test_creator_creation_from_channel():
    creator = build_creator_from_channel(_channel_item(), video_stats=_video_stats_item())
    assert creator.creator_id == "yt_ch1"
    assert creator.platform == "youtube"
    assert creator.followers == 15000
    assert creator.avg_views == 5000.0
    assert creator.source_mode == SourceMode.LIVE


# ---------------------------------------------------------------------------
# 6. Integration creation
# ---------------------------------------------------------------------------

def test_integration_creation_fields(settings: Settings):
    detector = IntegrationDetector(settings)
    video = _search_item("vid1", "Автор24 обзор, промокод AVT100", "На правах рекламы. https://avtor24.ru/x")
    detector_result = detector.detect(
        video["snippet"]["title"], video["snippet"]["description"], ["Автор24"],
    )
    creator = build_creator_from_channel(_channel_item())
    evidence_store = EvidenceStore()
    integration = build_integration("comp_test", creator, video, None, detector_result, evidence_store)

    assert integration.competitor_id == "comp_test"
    assert integration.creator_id == creator.creator_id
    assert integration.platform == "youtube"
    assert integration.content_url == "https://www.youtube.com/watch?v=vid1"
    assert integration.evidence, "должен быть непустой evidence"
    assert integration.confidence == detector_result.confidence
    assert integration.ingestion_source == "youtube_api_v3"
    assert integration.source_mode == SourceMode.LIVE
    assert integration.category == "confirmed"


# ---------------------------------------------------------------------------
# 7. Dedup
# ---------------------------------------------------------------------------

def test_dedup_same_video_id_produces_same_integration_id(monkeypatch, fake_adapter, settings: Settings):
    video = _search_item("vid_dup", "Автор24 обзор, промокод AVT100", "На правах рекламы, спонсор видео.")
    monkeypatch.setattr(fake_adapter, "search_videos", lambda query, max_results: [video])
    monkeypatch.setattr(fake_adapter, "get_channel_stats", lambda channel_id: _channel_item(channel_id))
    monkeypatch.setattr(fake_adapter, "get_video_stats", lambda video_id: _video_stats_item(video_id))

    report1 = run_youtube_ingestion(competitor_id="comp_test", competitor_name="Автор24", adapter=fake_adapter, settings=settings)
    report2 = run_youtube_ingestion(competitor_id="comp_test", competitor_name="Автор24", adapter=fake_adapter, settings=settings)

    assert len(report1.confirmed_integrations) == 1
    assert len(report2.confirmed_integrations) == 1
    assert report1.confirmed_integrations[0].integration_id == report2.confirmed_integrations[0].integration_id

    from app.storage import Storage
    import tempfile, os
    db_path = os.path.join(tempfile.mkdtemp(), "dedup_test.sqlite3")
    storage = Storage(db_path=db_path)
    storage.upsert_integration(report1.confirmed_integrations[0])
    storage.upsert_integration(report2.confirmed_integrations[0])
    assert storage.counts()["integrations"] == 1  # upsert - не дублируется


# ---------------------------------------------------------------------------
# 8. API failure / degraded, не роняя pipeline
# ---------------------------------------------------------------------------

def test_discover_videos_handles_quota_exceeded_gracefully(monkeypatch, fake_adapter):
    health_registry.reset()

    def raise_quota(query, max_results):
        request = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/search")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("quota exceeded", request=request, response=response)

    monkeypatch.setattr(fake_adapter, "search_videos", raise_quota)
    result = discover_videos(fake_adapter, ["Автор24", "Автор24 реклама"], max_results_per_query=5)

    assert isinstance(result, DiscoveryResult)
    assert result.quota_exceeded is True
    assert result.videos == []
    snapshot = health_registry.snapshot()
    assert any(h["source"] == "youtube" and h["status"] == "degraded" for h in snapshot)


def test_discover_videos_continues_after_single_query_error(monkeypatch, fake_adapter):
    health_registry.reset()

    def flaky(query, max_results):
        # query1 стабильно недоступен (даже после retries внутри адаптера) -
        # query2 при этом должен всё равно успешно отработать.
        if query == "query1":
            raise RuntimeError("постоянная ошибка на этом запросе")
        return [_search_item("vid_ok", "Автор24 обзор", "норм видео")]

    monkeypatch.setattr(fake_adapter, "search_videos", flaky)
    result = discover_videos(fake_adapter, ["query1", "query2"], max_results_per_query=5)
    assert len(result.videos) == 1
    assert "query1" in result.queries_failed
    assert "query2" in result.queries_run
