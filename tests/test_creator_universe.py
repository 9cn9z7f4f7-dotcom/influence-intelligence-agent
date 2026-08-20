from __future__ import annotations

from app.analysis.models import AnalysisConfig
from app.creator_universe import build_creator_universe, next_move_candidate_pool
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.platforms.youtube import YouTubePlatformAdapter


def _search_item(video_id: str, channel_id: str) -> dict:
    return {
        "id": {"videoId": video_id},
        "snippet": {"title": "блог", "description": "студенческая жизнь", "channelId": channel_id,
                    "channelTitle": "Channel", "publishedAt": "2026-07-01T00:00:00Z"},
    }


def test_universe_unavailable_without_api_key():
    adapter = YouTubePlatformAdapter(adapter=YouTubeAdapter(api_key=""))
    universe = build_creator_universe(AnalysisConfig(), platform_adapter=adapter)
    assert universe.status == "unavailable"
    assert universe.creators == []
    assert universe.notes


def test_universe_builds_independent_pool_from_topic_queries(monkeypatch):
    raw_adapter = YouTubeAdapter(api_key="fake-key")

    def fake_search(query, max_results):
        return [_search_item("v1", "chA"), _search_item("v2", "chB")]

    monkeypatch.setattr(raw_adapter, "search_videos", fake_search)
    monkeypatch.setattr(raw_adapter, "get_channel_stats", lambda channel_id: {
        "id": channel_id, "snippet": {"title": "Ch", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "20000"},
    })
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [])
    monkeypatch.setattr(raw_adapter, "get_video_stats", lambda video_id: None)

    adapter = YouTubePlatformAdapter(adapter=raw_adapter)
    universe = build_creator_universe(AnalysisConfig(include_topics=["student"]), platform_adapter=adapter)

    assert universe.status == "ok"
    assert len(universe.creators) == 2
    channel_ids = {c.creator_id for c in universe.creators}
    assert channel_ids == {"yt_chA", "yt_chB"}
    # Запросы построены по теме (DynamicQueryGenerator), а не по имени
    # какого-либо конкретного бренда/конкурента.
    from app.query_generator import TOPIC_QUERY_TEMPLATES
    assert set(universe.queries_used) == set(TOPIC_QUERY_TEMPLATES["student"])


def test_universe_deduplicates_by_channel(monkeypatch):
    raw_adapter = YouTubeAdapter(api_key="fake-key")
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [
        _search_item("v1", "chA"), _search_item("v2", "chA"),
    ])
    monkeypatch.setattr(raw_adapter, "get_channel_stats", lambda channel_id: {
        "id": channel_id, "snippet": {"title": "Ch", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "5000"},
    })
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [])
    monkeypatch.setattr(raw_adapter, "get_video_stats", lambda video_id: None)

    adapter = YouTubePlatformAdapter(adapter=raw_adapter)
    universe = build_creator_universe(AnalysisConfig(), platform_adapter=adapter)
    assert len(universe.creators) == 1


def test_next_move_candidate_pool_excludes_brand_used_creators():
    from app.creator_universe import CreatorUniverse
    from app.models import Creator, SourceMode

    universe = CreatorUniverse(creators=[
        Creator(creator_id="yt_a", name="A", platform="youtube", source_mode=SourceMode.LIVE),
        Creator(creator_id="yt_b", name="B", platform="youtube", source_mode=SourceMode.LIVE),
    ])
    pool = next_move_candidate_pool(universe, used_creator_ids={"yt_a"})
    assert [c.creator_id for c in pool] == ["yt_b"]


def test_universe_respects_followers_filter(monkeypatch):
    raw_adapter = YouTubeAdapter(api_key="fake-key")
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [
        _search_item("v1", "small"), _search_item("v2", "big"),
    ])

    def fake_channel_stats(channel_id):
        subs = "1000" if channel_id == "small" else "500000"
        return {"id": channel_id, "snippet": {"title": "Ch", "publishedAt": "2020-01-01T00:00:00Z"},
                "statistics": {"subscriberCount": subs}}

    monkeypatch.setattr(raw_adapter, "get_channel_stats", fake_channel_stats)
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [])
    monkeypatch.setattr(raw_adapter, "get_video_stats", lambda video_id: None)

    adapter = YouTubePlatformAdapter(adapter=raw_adapter)
    config = AnalysisConfig(min_followers=100_000)
    universe = build_creator_universe(config, platform_adapter=adapter)
    assert len(universe.creators) == 1
    assert universe.creators[0].creator_id == "yt_big"
