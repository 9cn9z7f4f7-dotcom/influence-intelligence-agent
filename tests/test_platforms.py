from __future__ import annotations

import pytest

from app.analysis.brand_resolver import resolve_brand
from app.analysis.models import AnalysisConfig
from app.platforms import REGISTRY, get_platform_adapter
from app.platforms.instagram import InstagramPlatformAdapter
from app.platforms.tiktok import TikTokPlatformAdapter
from app.platforms.youtube import YouTubePlatformAdapter


def test_registry_has_all_three_platforms():
    assert set(REGISTRY.keys()) == {"youtube", "instagram", "tiktok"}


def test_get_platform_adapter_unknown_raises():
    with pytest.raises(ValueError):
        get_platform_adapter("facebook")


@pytest.mark.parametrize("platform,adapter_cls", [
    ("youtube", YouTubePlatformAdapter),
    ("instagram", InstagramPlatformAdapter),
    ("tiktok", TikTokPlatformAdapter),
])
def test_all_adapters_implement_full_interface(platform, adapter_cls):
    adapter = adapter_cls()
    assert adapter.platform_name == platform
    for method in ("discover_brand_content", "detect_integration", "extract_creator",
                   "normalize_creator", "normalize_integration"):
        assert hasattr(adapter, method)


def test_youtube_adapter_reports_unavailable_without_api_key(monkeypatch):
    from app.ingestion.youtube_adapter import YouTubeAdapter

    adapter = YouTubePlatformAdapter(adapter=YouTubeAdapter(api_key=""))
    brand = resolve_brand("Автор24")
    result = adapter.discover_brand_content(brand, AnalysisConfig())
    assert result.status == "unavailable"
    assert result.source_mode == "none"
    assert "YOUTUBE_API_KEY" in result.reason
    assert result.import_hint


@pytest.mark.parametrize("adapter_cls", [InstagramPlatformAdapter, TikTokPlatformAdapter])
def test_instagram_and_tiktok_never_claim_live_data(adapter_cls):
    """Требование раздела 3: никогда не подделывать live для Instagram/TikTok."""
    adapter = adapter_cls()
    brand = resolve_brand("Автор24")
    result = adapter.discover_brand_content(brand, AnalysisConfig())
    assert result.status == "unavailable"
    assert result.source_mode == "none"
    assert result.raw_items == []
    assert result.import_hint is not None
    assert "csv" in result.import_hint.lower() or "json" in result.import_hint.lower()

    # detect_integration/extract_creator для этих платформ явно не реализованы -
    # они не должны молча возвращать выдуманные данные.
    with pytest.raises(NotImplementedError):
        adapter.detect_integration({}, ["Автор24"])
    with pytest.raises(NotImplementedError):
        adapter.extract_creator({})


def test_youtube_extract_creator_uses_multi_video_average_not_single_video(monkeypatch):
    """Раздел 8: avg_views должен считаться по нескольким видео канала, а не по
    единственному видео, которое вызвало детекцию интеграции."""
    from app.ingestion.youtube_adapter import YouTubeAdapter

    raw_adapter = YouTubeAdapter(api_key="fake-key")
    monkeypatch.setattr(raw_adapter, "get_channel_stats", lambda channel_id: {
        "id": channel_id,
        "snippet": {"title": "Test Channel", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "20000"},
    })
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [
        {"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}, {"id": {"videoId": "v3"}},
    ])

    def fake_video_stats(video_id):
        views_by_id = {"v1": "1000", "v2": "3000", "v3": "2000"}
        return {
            "id": video_id,
            "snippet": {"publishedAt": "2026-07-01T00:00:00Z"},
            "statistics": {"viewCount": views_by_id[video_id]},
        }

    monkeypatch.setattr(raw_adapter, "get_video_stats", fake_video_stats)

    adapter = YouTubePlatformAdapter(adapter=raw_adapter)
    # Триггерное видео, которое вызвало детекцию, имеет свои собственные (не связанные) views -
    # если бы старая логика использовала его напрямую, avg_views был бы искажён.
    raw_trigger_item = {
        "id": {"videoId": "trigger_vid"},
        "snippet": {"channelId": "ch1", "title": "Автор24 обзор"},
    }
    creator = adapter.extract_creator(raw_trigger_item)
    assert creator is not None
    assert creator.avg_views == 2000.0  # mean(1000, 3000, 2000), НЕ views триггерного видео
    assert creator.median_views == 2000.0


def test_normalize_methods_set_platform_field():
    from app.models import Creator, Integration, SourceMode

    for platform, adapter_cls in [("youtube", YouTubePlatformAdapter),
                                   ("instagram", InstagramPlatformAdapter),
                                   ("tiktok", TikTokPlatformAdapter)]:
        adapter = adapter_cls()
        creator = Creator(creator_id="c1", name="X", platform="other", source_mode=SourceMode.IMPORTED)
        integration = Integration(integration_id="i1", competitor_id="comp1", creator_id="c1",
                                   platform="other", source_mode=SourceMode.IMPORTED)
        assert adapter.normalize_creator(creator).platform == platform
        assert adapter.normalize_integration(integration).platform == platform
