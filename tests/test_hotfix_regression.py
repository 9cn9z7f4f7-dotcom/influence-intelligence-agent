"""
Critical regression tests for the "1 hour to deadline" hotfix:

  1. dynamic queries differ between Автор24-like and Nike-like brands
  2. universe creators are enriched (multi-video avg/median + topic_tags)
  3. Next Move candidate pool comes from the independent universe
  4. White Space supply comes from the independent universe (not just brand integrations)
  5. date_range filter actually excludes out-of-window integrations
  6. min_strategy_match actually filters Next Move candidates
  7. min_white_space_opportunity actually filters White Space segments
  8. include_topics changes the Creator Universe discovery queries
  9. URL brand resolution fetches the real channel title when the API is available
  10. full mocked end-to-end /api/analyze -> /api/analysis/{id}
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.models import AnalysisConfig, AnalyzeRequest, ResolvedBrand
from app.analysis.pipeline import run_analysis, stage_resolve_youtube_channel
from app.models import Competitor, Creator, Integration, SourceMode
from app.query_generator import generate_discovery_queries


def _creator(cid, topic, followers=20000, source_mode=SourceMode.LIVE) -> Creator:
    return Creator(creator_id=cid, name=cid, platform="youtube", followers=followers,
                    topic_tags=[topic], source_mode=source_mode)


def _integration(iid, competitor_id, creator_id, published_at, category="confirmed", confidence=0.8) -> Integration:
    return Integration(integration_id=iid, competitor_id=competitor_id, creator_id=creator_id,
                        platform="youtube", published_at=published_at, category=category,
                        confidence=confidence, source_mode=SourceMode.LIVE)


# ---------------------------------------------------------------------------
# 1. Dynamic queries differ between brands
# ---------------------------------------------------------------------------

def test_dynamic_queries_differ_between_avtor24_and_nike():
    avtor24_queries = generate_discovery_queries(observed_topics=["education", "student"])
    nike_queries = generate_discovery_queries(observed_topics=["fitness", "sports"])

    assert avtor24_queries != nike_queries
    assert set(avtor24_queries).isdisjoint(set(nike_queries))
    assert not any("sneaker" in q.lower() or "fitness" in q.lower() or "running" in q.lower() for q in avtor24_queries)
    assert not any("education" in q.lower() or "student" in q.lower() for q in nike_queries)


# ---------------------------------------------------------------------------
# 8. include_topics changes universe discovery queries
# ---------------------------------------------------------------------------

def test_include_topics_changes_universe_queries():
    default_queries = generate_discovery_queries(observed_topics=[])
    beauty_queries = generate_discovery_queries(observed_topics=[], include_topics=["beauty"])
    assert beauty_queries != default_queries
    assert any("beauty" in q.lower() or "makeup" in q.lower() for q in beauty_queries)

    # include_topics побеждает observed_topics, если пользователь явно его задал.
    overridden = generate_discovery_queries(observed_topics=["fitness"], include_topics=["beauty"])
    assert overridden == beauty_queries


# ---------------------------------------------------------------------------
# 2. Universe creators are enriched (via mocked YouTubePlatformAdapter)
# ---------------------------------------------------------------------------

def test_universe_creators_are_enriched_with_metrics_and_topics(monkeypatch):
    from app.creator_universe import build_creator_universe
    from app.ingestion.youtube_adapter import YouTubeAdapter
    from app.platforms.youtube import YouTubePlatformAdapter

    raw_adapter = YouTubeAdapter(api_key="fake-key")
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [{
        "id": {"videoId": "v1"},
        "snippet": {"title": "fitness workout", "description": "sneakers review", "channelId": "chA",
                    "channelTitle": "Channel", "publishedAt": "2026-07-01T00:00:00Z"},
    }])
    monkeypatch.setattr(raw_adapter, "get_channel_stats", lambda channel_id: {
        "id": channel_id, "snippet": {"title": "Ch", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "30000"},
    })
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [
        {"id": {"videoId": "v1"}, "snippet": {"title": "fitness", "description": "workout"}},
        {"id": {"videoId": "v2"}, "snippet": {"title": "running", "description": "sneakers"}},
    ])
    monkeypatch.setattr(raw_adapter, "get_video_stats", lambda video_id: {
        "id": video_id, "snippet": {"publishedAt": "2026-07-01T00:00:00Z"},
        "statistics": {"viewCount": "1000" if video_id == "v1" else "3000"},
    })

    adapter = YouTubePlatformAdapter(adapter=raw_adapter)
    universe = build_creator_universe(AnalysisConfig(include_topics=["fitness"]), platform_adapter=adapter)

    assert len(universe.creators) == 1
    creator = universe.creators[0]
    assert creator.avg_views == 2000.0  # mean(1000, 3000) - НЕ views одного ролика
    assert creator.topic_tags and creator.topic_tags[0] in ("fitness", "sports")


# ---------------------------------------------------------------------------
# 3 & 4. Next Move / White Space use the INDEPENDENT universe (not just brand integrations)
# ---------------------------------------------------------------------------

def test_next_move_and_white_space_use_independent_universe(monkeypatch):
    """Точный сценарий из требований: brand used creators A/B/C; universe содержит
    A..H; segment X (topic=beauty) содержит D..H; у бренда почти нет интеграций в X.
    Next Move должен предлагать кандидатов из D..H. White Space должен обнаружить X."""
    import app.analysis.pipeline as pipeline_module

    brand = ResolvedBrand(brand_name="TestBrand", canonical_name="TestBrand", input_type="name")
    competitor = Competitor(competitor_id="comp_test", name="TestBrand", source_mode=SourceMode.LIVE)

    used_creators = [_creator(c, "fitness") for c in ["A", "B", "C"]]
    now = datetime.now(timezone.utc)
    used_integrations = [_integration(f"int_{c}", "comp_test", c, now) for c in ["A", "B", "C"]]

    universe_creators = used_creators + [_creator(c, "beauty") for c in ["D", "E", "F", "G", "H"]]

    def fake_process_brand(brand_input, platforms, config, evidence_store):
        return brand, competitor, [], used_creators, used_integrations, []

    def fake_build_universe_pool(platforms, config, observed_topics):
        return universe_creators, "ok", [], ["beauty blog"]

    monkeypatch.setattr(pipeline_module, "_process_brand", fake_process_brand)
    monkeypatch.setattr(pipeline_module, "stage_build_universe_pool", fake_build_universe_pool)

    request = AnalyzeRequest(brand="TestBrand", platforms=["youtube"])
    result = run_analysis(request, analysis_id="an_regression_1")

    # Next Move: кандидаты должны быть из D-H (universe minus used), НЕ A/B/C.
    all_candidates = [c["candidate"] for entry in result.next_move for c in entry.get("candidates", [])]
    assert all_candidates, "next_move не вернул кандидатов из universe"
    assert set(all_candidates).issubset({"D", "E", "F", "G", "H"})
    assert not set(all_candidates) & {"A", "B", "C"}

    # White Space: сегмент beauty (D-H) должен быть обнаружен, с available_creators=5
    # и почти нулевой saturation (у бренда там нет интеграций).
    beauty_segments = [s for s in result.white_space["segments"] if s["segment"]["topic"] == "beauty"]
    assert beauty_segments, "White Space не обнаружил сегмент X (beauty) из независимого universe"
    assert beauty_segments[0]["available_creators"] == 5
    assert beauty_segments[0]["competitor_integrations"] == 0


# ---------------------------------------------------------------------------
# 5. Date range filter
# ---------------------------------------------------------------------------

def test_date_range_filter_excludes_out_of_window_integrations(monkeypatch):
    import app.analysis.pipeline as pipeline_module

    brand = ResolvedBrand(brand_name="TestBrand", canonical_name="TestBrand", input_type="name")
    competitor = Competitor(competitor_id="comp_test", name="TestBrand", source_mode=SourceMode.LIVE)

    now = datetime.now(timezone.utc)
    creators = [_creator("A", "fitness")]
    integrations = [
        _integration("int_recent", "comp_test", "A", now - timedelta(days=5)),
        _integration("int_old", "comp_test", "A", now - timedelta(days=200)),
    ]

    monkeypatch.setattr(pipeline_module, "_process_brand",
                         lambda *a, **kw: (brand, competitor, [], creators, integrations, []))
    monkeypatch.setattr(pipeline_module, "stage_build_universe_pool",
                         lambda *a, **kw: ([], "unavailable", [], []))

    request = AnalyzeRequest(brand="TestBrand", platforms=["youtube"], settings=AnalysisConfig(date_range="30d"))
    result = run_analysis(request, analysis_id="an_regression_2")
    assert result.summary.integrations_found == 1  # только int_recent - int_old вне окна 30d


# ---------------------------------------------------------------------------
# 6. min_strategy_match filters Next Move
# ---------------------------------------------------------------------------

def test_min_strategy_match_filters_next_move_candidates(monkeypatch):
    import app.analysis.pipeline as pipeline_module

    brand = ResolvedBrand(brand_name="TestBrand", canonical_name="TestBrand", input_type="name")
    competitor = Competitor(competitor_id="comp_test", name="TestBrand", source_mode=SourceMode.LIVE)
    now = datetime.now(timezone.utc)
    used_creators = [_creator("A", "fitness")]
    used_integrations = [_integration("int_A", "comp_test", "A", now)]
    universe_creators = used_creators + [_creator("D", "fitness")]

    monkeypatch.setattr(pipeline_module, "_process_brand",
                         lambda *a, **kw: (brand, competitor, [], used_creators, used_integrations, []))
    monkeypatch.setattr(pipeline_module, "stage_build_universe_pool",
                         lambda *a, **kw: (universe_creators, "ok", [], []))

    lenient = run_analysis(AnalyzeRequest(brand="TestBrand", platforms=["youtube"],
                                           settings=AnalysisConfig(min_strategy_match=0.0)), "an_r3a")
    strict = run_analysis(AnalyzeRequest(brand="TestBrand", platforms=["youtube"],
                                          settings=AnalysisConfig(min_strategy_match=99.9)), "an_r3b")

    lenient_candidates = [c for entry in lenient.next_move for c in entry.get("candidates", [])]
    strict_candidates = [c for entry in strict.next_move for c in entry.get("candidates", [])]
    assert len(lenient_candidates) >= len(strict_candidates)
    assert strict_candidates == []


# ---------------------------------------------------------------------------
# 7. min_white_space_opportunity filters White Space segments
# ---------------------------------------------------------------------------

def test_min_white_space_opportunity_filters_segments(monkeypatch):
    import app.analysis.pipeline as pipeline_module

    brand = ResolvedBrand(brand_name="TestBrand", canonical_name="TestBrand", input_type="name")
    competitor = Competitor(competitor_id="comp_test", name="TestBrand", source_mode=SourceMode.LIVE)
    now = datetime.now(timezone.utc)
    used_creators = [_creator("A", "fitness")]
    used_integrations = [_integration("int_A", "comp_test", "A", now)]
    universe_creators = used_creators + [_creator(c, "beauty") for c in ["D", "E", "F", "G", "H"]]

    monkeypatch.setattr(pipeline_module, "_process_brand",
                         lambda *a, **kw: (brand, competitor, [], used_creators, used_integrations, []))
    monkeypatch.setattr(pipeline_module, "stage_build_universe_pool",
                         lambda *a, **kw: (universe_creators, "ok", [], []))

    lenient = run_analysis(AnalyzeRequest(brand="TestBrand", platforms=["youtube"],
                                           settings=AnalysisConfig(min_white_space_opportunity=0.0)), "an_r4a")
    strict = run_analysis(AnalyzeRequest(brand="TestBrand", platforms=["youtube"],
                                          settings=AnalysisConfig(min_white_space_opportunity=99.9)), "an_r4b")

    assert len(lenient.white_space["segments"]) >= len(strict.white_space["segments"])


# ---------------------------------------------------------------------------
# 9. URL brand resolution -> real channel title
# ---------------------------------------------------------------------------

def test_url_brand_resolution_fetches_real_channel_title(monkeypatch):
    from app.ingestion.youtube_adapter import YouTubeAdapter

    brand = ResolvedBrand(brand_name="Avtor24Official", canonical_name="Avtor24Official",
                           input_type="url", source_url="https://www.youtube.com/@Avtor24Official",
                           detected_platform="youtube", normalized_handle="Avtor24Official")

    monkeypatch.setattr(YouTubeAdapter, "is_available", lambda self: True)
    monkeypatch.setattr(YouTubeAdapter, "resolve_channel_by_handle", lambda self, handle: {
        "id": "UC_REAL_ID", "snippet": {"title": "Автор24 — образовательная платформа"},
    })

    resolved = stage_resolve_youtube_channel(brand, ["youtube"])
    assert resolved.canonical_name == "Автор24 — образовательная платформа"
    assert resolved.source_url == "https://www.youtube.com/channel/UC_REAL_ID"
    assert "Avtor24Official" in resolved.aliases  # handle сохранён как alias, не потерян


def test_url_brand_resolution_gracefully_falls_back_without_api_key():
    brand = ResolvedBrand(brand_name="Avtor24Official", canonical_name="Avtor24Official",
                           input_type="url", source_url="https://www.youtube.com/@Avtor24Official",
                           detected_platform="youtube", normalized_handle="Avtor24Official")
    resolved = stage_resolve_youtube_channel(brand, ["youtube"])
    assert resolved == brand  # без API key - как раньше, никакого падения


# ---------------------------------------------------------------------------
# 10. Full mocked end-to-end /api/analyze
# ---------------------------------------------------------------------------

def test_full_mocked_end_to_end_analyze(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api.server import app
    from app.ingestion.youtube_adapter import YouTubeAdapter
    from app.platforms.youtube import YouTubePlatformAdapter

    raw_adapter = YouTubeAdapter(api_key="fake-key")
    monkeypatch.setattr(raw_adapter, "search_videos", lambda query, max_results: [{
        "id": {"videoId": "v1"},
        "snippet": {"title": "Nike обзор кроссовок, промокод SPORT10", "description": "На правах рекламы.",
                    "channelId": "ch1", "channelTitle": "Channel", "publishedAt": "2026-07-01T00:00:00Z"},
    }])
    monkeypatch.setattr(raw_adapter, "get_channel_stats", lambda channel_id: {
        "id": channel_id, "snippet": {"title": "Ch", "country": "RU", "publishedAt": "2020-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "50000"},
    })
    monkeypatch.setattr(raw_adapter, "list_channel_recent_videos", lambda channel_id, max_results: [
        {"id": {"videoId": "v1"}, "snippet": {"title": "running", "description": "sneakers"}},
        {"id": {"videoId": "v2"}, "snippet": {"title": "fitness", "description": "workout"}},
    ])
    monkeypatch.setattr(raw_adapter, "get_video_stats", lambda video_id: {
        "id": video_id, "snippet": {"publishedAt": "2026-07-01T00:00:00Z"},
        "statistics": {"viewCount": "2000" if video_id == "v1" else "4000"},
    })

    adapter_instance = YouTubePlatformAdapter(adapter=raw_adapter)
    import app.analysis.pipeline as pipeline_module
    monkeypatch.setattr(pipeline_module, "get_platform_adapter", lambda platform: adapter_instance)

    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["youtube"]})
    assert resp.status_code == 200
    analysis_id = resp.json()["analysis_id"]

    body = client.get(f"/api/analysis/{analysis_id}").json()
    assert body["summary"]["integrations_found"] == 1
    assert body["market_map"]["competitors"]
    assert isinstance(body["competitor_dna"], list) and body["competitor_dna"]
    assert isinstance(body["next_move"], list)
    assert "segments" in body["white_space"]
    assert "opportunities" in body["our_move"]
