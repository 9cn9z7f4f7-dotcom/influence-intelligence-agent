from datetime import datetime, timezone

import httpx

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.analysis.pipeline import _build_potential_creator_entry, stage_build_findings
from app.analytics.competitor_dna import CompetitorDnaBuilder
from app.analytics.next_move import NextMoveBuilder
from app.ingestion.live_youtube import DetectorResult
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import Competitor, Creator, Integration, Publisher, SourceMode
from app.platforms.youtube import YouTubePlatformAdapter
from config.settings import settings


def test_articles_never_build_potential_creator_signal():
    detector = DetectorResult(
        is_integration=False, confidence=0.4, reasons=["affinity:recommendation"], signals={},
        category="potential_creator", has_brand_evidence=True, has_commercial_evidence=False,
    )
    assert _build_potential_creator_entry("articles", {"parsed": object()}, detector, object()) is None


def test_brand_owned_article_is_typed_and_not_creator():
    publisher = Publisher(
        publisher_id="pub-nike", name="about.nike.com", domain="about.nike.com",
        source_url="https://about.nike.com/story",
    )
    integration = Integration(
        integration_id="a1", competitor_id="nike", creator_id="pub-nike", publisher_id="pub-nike",
        platform="articles", content_url="https://about.nike.com/story", article_category="organic_mention",
        category="organic_mention", source_mode=SourceMode.LIVE,
    )
    finding = stage_build_findings([integration], [], [publisher], [], brand_name="Nike")[0]
    assert finding["entity_type"] == "brand_owned"
    assert finding["classification"] == "brand_owned"


def test_dna_n1_has_no_percentage_strategy_pattern():
    competitor = Competitor(competitor_id="nike", name="Nike", source_mode=SourceMode.LIVE)
    creator = Creator(creator_id="c1", name="Creator", platform="youtube", source_mode=SourceMode.LIVE)
    integration = Integration(
        integration_id="i1", competitor_id="nike", creator_id="c1", platform="youtube",
        published_at=datetime.now(timezone.utc), category="confirmed", source_mode=SourceMode.LIVE,
    )
    result = CompetitorDnaBuilder([creator], [integration], settings).build(competitor)
    assert result["confirmed_creator_integrations"] == 1
    assert result["observed_patterns"]
    assert all("%" not in p["statement"] for p in result["observed_patterns"])
    assert all("наблюдаемой выборке" in p["statement"] for p in result["observed_patterns"])
    assert result["recent_shifts"] == []
    assert "устойчивый рекламный паттерн пока не подтверждён" in result["strategy_message"].lower()


def test_potential_creator_surfaces_without_fabricated_match_score():
    competitor = Competitor(competitor_id="nike", name="Nike", source_mode=SourceMode.LIVE)
    creator = Creator(
        creator_id="yt_c1", name="Runner", platform="youtube", canonical_url="https://youtube.com/@runner",
        topic_tags=["running"], source_mode=SourceMode.LIVE,
    )
    result = NextMoveBuilder(
        [creator], [], settings, potential_creator_ids={"yt_c1"}, top_n=10,
    ).build_for_competitor(competitor)
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["canonical_url"] == "https://youtube.com/@runner"
    assert candidate["similarity_score"] is None
    assert candidate["match_label"] == "Недостаточно метрик"
    assert candidate["has_organic_brand_affinity"] is True


def test_youtube_quota_switches_to_web_search_without_retry(monkeypatch):
    raw = YouTubeAdapter(api_key="fake")
    calls = {"search": 0}

    def quota_search(query, max_results):
        calls["search"] += 1
        request = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/search")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("quota", request=request, response=response)

    monkeypatch.setattr(raw, "search_videos", quota_search)

    class FakeWeb:
        last_used_provider = "tavily"
        source_name = "tavily"
        def is_available(self): return True
        def search(self, query, max_results=10):
            from app.search_client import SearchResultItem
            return [SearchResultItem(
                url="https://www.youtube.com/watch?v=real123",
                title="Runner reviews Nike Pegasus",
                snippet="I use Nike Pegasus and recommend it",
                source_provider="tavily",
            )]

    monkeypatch.setattr("app.platforms.youtube.get_default_search_client", lambda _settings: FakeWeb())
    adapter = YouTubePlatformAdapter(adapter=raw)
    brand = ResolvedBrand(
        brand_name="Nike", canonical_name="Nike", aliases=[], input_type="name",
    )
    result = adapter.discover_brand_content(brand, AnalysisConfig())
    assert calls["search"] == 1
    assert result.search_provider == "tavily"
    assert result.raw_items
    assert result.raw_items[0]["_web_source_url"].startswith("https://www.youtube.com/")


def test_frontend_hides_technical_labels_and_has_hunting_copy():
    source = open("static/analyze.js", encoding="utf-8").read()
    assert "low_sample_size_overall" not in source
    assert "no_historical_window_data" not in source
    assert "Кого можно схантить" in source
    assert "Пока недостаточно авторов для карты сегментов." in source
    assert "/api/analysis/${encodeURIComponent(analysisId)}/evidence/" in source


def _quota_web_result(monkeypatch, *, enrich_video):
    raw = YouTubeAdapter(api_key="fake")

    def quota_search(query, max_results):
        request = httpx.Request("GET", "https://www.googleapis.com/youtube/v3/search")
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("quota", request=request, response=response)

    monkeypatch.setattr(raw, "search_videos", quota_search)
    monkeypatch.setattr(raw, "get_video_stats", enrich_video)

    class FakeWeb:
        last_used_provider = "tavily"
        source_name = "tavily"
        def is_available(self): return True
        def search(self, query, max_results=10):
            from app.search_client import SearchResultItem
            return [SearchResultItem(
                url="https://www.youtube.com/watch?v=real123",
                title="Nike Pegasus 41 Review - Great Daily Trainer",
                snippet="Nike Pegasus review",
                source_provider="tavily",
            )]

    monkeypatch.setattr("app.platforms.youtube.get_default_search_client", lambda _settings: FakeWeb())
    adapter = YouTubePlatformAdapter(adapter=raw)
    brand = ResolvedBrand(brand_name="Nike", canonical_name="Nike", aliases=[], input_type="name")
    result = adapter.discover_brand_content(brand, AnalysisConfig())
    return adapter, result


def test_youtube_tavily_watch_url_uses_real_channel_identity(monkeypatch):
    def enrich(video_id):
        assert video_id == "real123"
        return {
            "id": video_id,
            "snippet": {"channelId": "UC_REAL", "channelTitle": "Real Running Channel"},
            "statistics": {"viewCount": "12345"},
        }

    adapter, result = _quota_web_result(monkeypatch, enrich_video=enrich)
    monkeypatch.setattr(adapter.adapter, "get_channel_stats", lambda channel_id: None)

    raw_item = result.raw_items[0]
    creator = adapter.extract_creator(raw_item)

    assert creator is not None
    assert creator.name == "Real Running Channel"
    assert creator.canonical_url == "https://www.youtube.com/channel/UC_REAL"
    assert raw_item["_web_source_url"] == "https://www.youtube.com/watch?v=real123"
    assert raw_item["_web_source_url"] in creator.source_refs
    assert creator.name != raw_item["snippet"]["title"]


def test_youtube_tavily_watch_url_without_enrichment_creates_no_creator(monkeypatch):
    def unavailable(_video_id):
        raise RuntimeError("videos.list unavailable")

    adapter, result = _quota_web_result(monkeypatch, enrich_video=unavailable)
    raw_item = result.raw_items[0]

    assert raw_item["_web_source_url"] == "https://www.youtube.com/watch?v=real123"
    assert raw_item["snippet"]["title"] == "Nike Pegasus 41 Review - Great Daily Trainer"
    assert adapter.extract_creator(raw_item) is None
    assert "_web_creator_name" not in raw_item


def test_next_move_does_not_receive_video_title_fake_creator(monkeypatch):
    def unavailable(_video_id):
        raise RuntimeError("videos.list unavailable")

    adapter, result = _quota_web_result(monkeypatch, enrich_video=unavailable)
    creator = adapter.extract_creator(result.raw_items[0])
    creators = [creator] if creator is not None else []
    competitor = Competitor(competitor_id="nike", name="Nike", source_mode=SourceMode.LIVE)

    next_move = NextMoveBuilder(creators, [], settings, potential_creator_ids=set()).build_for_competitor(competitor)
    names = [item["candidate"] for item in next_move["candidates"]]
    assert "Nike Pegasus 41 Review - Great Daily Trainer" not in names


def test_white_space_does_not_receive_video_title_fake_creator(monkeypatch):
    from app.analytics.white_space import WhiteSpaceBuilder
    from app.models import OurProfile

    def unavailable(_video_id):
        raise RuntimeError("videos.list unavailable")

    adapter, result = _quota_web_result(monkeypatch, enrich_video=unavailable)
    creator = adapter.extract_creator(result.raw_items[0])
    creators = [creator] if creator is not None else []

    white_space = WhiteSpaceBuilder(
        creators=creators,
        competitors=[Competitor(competitor_id="nike", name="Nike", source_mode=SourceMode.LIVE)],
        integrations=[],
        our_profile=OurProfile(),
        settings=settings,
    ).build()
    names = [c["name"] for segment in white_space["segments"] for c in segment["top_creators"]]
    assert "Nike Pegasus 41 Review - Great Daily Trainer" not in names


def test_article_content_gate_rejects_product_page_and_accepts_article():
    from app.article_parser import ArticleParseResult
    from app.platforms.articles import _is_article_like
    from app.search_client import SearchResultItem

    product = ArticleParseResult(
        source_url="https://shop.example.com/product/nike-shoe",
        canonical_url="https://shop.example.com/product/nike-shoe",
        title="Nike Shoe",
        main_text="Add to cart. Select size. Buy now. " * 20,
    )
    article = ArticleParseResult(
        source_url="https://news.example.com/review/nike-pegasus",
        canonical_url="https://news.example.com/review/nike-pegasus",
        title="Nike Pegasus review",
        main_text="Detailed running review with testing notes and context. " * 20,
    )
    assert _is_article_like(product, SearchResultItem(url=product.source_url, title=product.title)) is False
    assert _is_article_like(article, SearchResultItem(url=article.source_url, title=article.title)) is True


def test_youtube_tavily_discovery_works_without_youtube_api_key(monkeypatch):
    from app.search_client import SearchResultItem

    raw = YouTubeAdapter(api_key="")

    class FakeWeb:
        last_used_provider = "tavily"
        source_name = "tavily"
        def is_available(self): return True
        def search(self, query, max_results=10):
            return [SearchResultItem(
                url="https://www.youtube.com/watch?v=web123",
                title="Nike running review",
                snippet="Nike running shoe recommendation",
                source_provider="tavily",
            )]

    monkeypatch.setattr("app.platforms.youtube.get_default_search_client", lambda _settings: FakeWeb())
    adapter = YouTubePlatformAdapter(adapter=raw)
    brand = ResolvedBrand(brand_name="Nike", canonical_name="Nike", aliases=[], input_type="name")
    result = adapter.discover_brand_content(brand, AnalysisConfig())
    assert result.status == "ok"
    assert result.source_mode == "live"
    assert result.search_provider == "tavily"
    assert result.raw_items[0]["_web_source_url"] == "https://www.youtube.com/watch?v=web123"
    assert adapter.extract_creator(result.raw_items[0]) is None


def test_one_platform_failure_does_not_fail_process_brand(monkeypatch):
    from app.analysis.pipeline import _process_brand
    from app.evidence import EvidenceStore

    class BrokenAdapter:
        def discover_brand_content(self, brand, config):
            raise RuntimeError("source down")

    monkeypatch.setattr("app.analysis.pipeline.get_platform_adapter", lambda _platform: BrokenAdapter())
    _brand, _competitor, coverages, creators, integrations, _manual, publishers = _process_brand(
        "Nike", ["articles"], AnalysisConfig(), EvidenceStore(),
    )
    assert coverages[0].status == "degraded"
    assert creators == []
    assert integrations == []
    assert publishers == []


def test_runtime_budget_is_bounded_and_clearable():
    from app.runtime_budget import budget_exhausted, clear_budget, remaining_seconds, start_budget

    start_budget(295)
    remaining = remaining_seconds()
    assert remaining is not None and 0 < remaining <= 295
    assert budget_exhausted(300) is True
    clear_budget()
    assert remaining_seconds() is None
