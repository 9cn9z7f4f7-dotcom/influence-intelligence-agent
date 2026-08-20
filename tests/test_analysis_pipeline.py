from __future__ import annotations

import pytest

from app.analysis.models import AnalysisConfig, AnalyzeRequest
from app.analysis.pipeline import run_analysis
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.platforms.youtube import YouTubePlatformAdapter


def _video(video_id: str, title: str, description: str, channel_id: str = "ch1") -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {"title": title, "description": description, "channelId": channel_id,
                    "channelTitle": "Channel", "publishedAt": "2026-07-01T00:00:00Z"},
    }


@pytest.fixture
def mocked_youtube_adapter(monkeypatch):
    """Патчит get_platform_adapter('youtube') так, чтобы pipeline получал
    мокнутый YouTubePlatformAdapter без сети/API-ключа."""
    raw_adapter = YouTubeAdapter(api_key="fake-key")
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [
        _video("v1", "Автор24 обзор, промокод AVT100", "На правах рекламы. https://avtor24.ru/promo"),
    ])
    monkeypatch.setattr(raw_adapter, "get_channel_stats", lambda channel_id: {
        "id": channel_id, "snippet": {"title": "Ch", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "20000"},
    })
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [
        {"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}},
    ])
    monkeypatch.setattr(raw_adapter, "get_video_stats", lambda video_id: {
        "id": video_id, "snippet": {"publishedAt": "2026-07-01T00:00:00Z"},
        "statistics": {"viewCount": "2000" if video_id == "v1" else "4000"},
    })

    adapter_instance = YouTubePlatformAdapter(adapter=raw_adapter)

    import app.analysis.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "get_platform_adapter", lambda platform: adapter_instance)
    # creator_universe строит СВОЙ собственный YouTubePlatformAdapter() внутри - подменяем и его,
    # чтобы universe тоже не требовал реального API key/сети.
    import app.creator_universe as universe_module
    monkeypatch.setattr(universe_module, "discover_videos", lambda adapter, queries, max_results_per_query: (
        __import__("app.ingestion.live_youtube", fromlist=["discover_videos"]).discover_videos(
            raw_adapter, queries, max_results_per_query,
        )
    ))
    monkeypatch.setattr(
        universe_module, "YouTubePlatformAdapter", lambda *a, **kw: YouTubePlatformAdapter(adapter=raw_adapter),
    )
    return raw_adapter


def test_no_api_key_degrades_gracefully_end_to_end():
    """Без YOUTUBE_API_KEY pipeline не падает - честно возвращает unavailable + limitations."""
    request = AnalyzeRequest(brand="НесуществующийБренд", platforms=["youtube"])
    result = run_analysis(request, analysis_id="an_1")

    assert result.coverage.platforms[0].status == "unavailable"
    assert result.summary.integrations_found == 0
    assert result.limitations  # честно объясняет, почему данных нет
    assert "market_map" not in result.limitations  # sanity: не мусор в limitations
    assert result.market_map is not None  # pipeline всё равно проходит все 5 слоёв (на пустых данных)


def test_live_mocked_data_flows_through_all_five_layers(mocked_youtube_adapter):
    request = AnalyzeRequest(brand="Автор24", platforms=["youtube"])
    result = run_analysis(request, analysis_id="an_2")

    assert result.coverage.platforms[0].status == "ok"
    assert result.coverage.live_sources == ["youtube"]
    assert result.summary.integrations_found == 1
    assert result.summary.creators_used == 1

    assert result.market_map["competitors"]
    assert result.competitor_dna and isinstance(result.competitor_dna, list)
    assert isinstance(result.next_move, list)
    assert "segments" in result.white_space or isinstance(result.white_space, dict)
    assert "opportunities" in result.our_move


def test_confirmed_only_filter_excludes_organic_mentions(monkeypatch, mocked_youtube_adapter):
    """AnalysisConfig.confirmed_only реально фильтрует, а не просто хранится (раздел 11)."""
    raw_adapter = mocked_youtube_adapter
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [
        _video("v_organic", "Автор24 в новостях", "Просто упоминание без деталей."),
    ])

    request = AnalyzeRequest(brand="Автор24", platforms=["youtube"], settings=AnalysisConfig(confirmed_only=True))
    result = run_analysis(request, analysis_id="an_3")
    assert result.summary.integrations_found == 0  # organic_mention отфильтрован confirmed_only


def test_organic_mention_included_when_include_organic_true(monkeypatch, mocked_youtube_adapter):
    raw_adapter = mocked_youtube_adapter
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [
        _video("v_organic", "Автор24 в новостях", "Просто упоминание без деталей."),
    ])

    request = AnalyzeRequest(
        brand="Автор24", platforms=["youtube"],
        settings=AnalysisConfig(confirmed_only=False, include_organic=True),
    )
    result = run_analysis(request, analysis_id="an_4")
    assert result.summary.integrations_found == 1


def test_instagram_and_tiktok_report_unavailable_with_limitations():
    request = AnalyzeRequest(brand="Автор24", platforms=["instagram", "tiktok"])
    result = run_analysis(request, analysis_id="an_5")

    assert result.coverage.live_sources == []
    for coverage in result.coverage.platforms:
        assert coverage.status == "unavailable"
        assert coverage.source_mode == "none"
    assert len(result.limitations) >= 2
    joined = " ".join(result.limitations).lower()
    assert "instagram" in joined and "tiktok" in joined
