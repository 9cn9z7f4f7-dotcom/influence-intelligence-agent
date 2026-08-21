from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.analysis.pipeline import stage_build_findings
from app.analytics.next_move import NextMoveBuilder
from app.analytics.white_space import WhiteSpaceBuilder
from app.evidence import EvidenceStore, fact
from app.models import Competitor, Creator, Integration, OurProfile, Publisher, SourceMode
from config.settings import Settings

NOW = datetime(2026, 8, 21, tzinfo=timezone.utc)


def test_findings_are_built_from_normalized_real_objects_with_source_url():
    creator = Creator(
        creator_id="creator-1",
        name="Creator One",
        platform="youtube",
        followers=42_000,
        median_views=12_000,
        avg_views=15_000,
        engagement_rate=0.04,
        topic_tags=["fitness"],
        source_mode=SourceMode.LIVE,
    )
    evidence = fact(
        field="live_signal:promo_code",
        value=True,
        source_url="https://youtube.com/watch?v=real123",
        observed_at=NOW,
        raw_fragment="Промокод NIKE20",
    )
    integration = Integration(
        integration_id="integration-1",
        competitor_id="brand-1",
        creator_id=creator.creator_id,
        platform="youtube",
        content_url="https://youtube.com/watch?v=real123",
        raw_text="Обзор Nike || используйте промокод NIKE20",
        published_at=NOW,
        content_type="review",
        category="confirmed",
        source_mode=SourceMode.LIVE,
        evidence=[evidence],
    )

    findings = stage_build_findings([integration], [creator], [], [])

    assert len(findings) == 1
    finding = findings[0]
    assert finding["entity_name"] == "Creator One"
    assert finding["source_url"] == "https://youtube.com/watch?v=real123"
    assert finding["classification"] == "confirmed"
    assert finding["detected_signals"] == ["promo_code"]
    assert finding["metrics"]["median_views"] == 12_000
    assert finding["evidence_ids"] == [evidence.evidence_id]


def test_article_finding_keeps_publisher_separate_from_creator():
    publisher = Publisher(
        publisher_id="publisher-1",
        name="Example Media",
        domain="media.example",
        source_url="https://media.example/nike",
    )
    integration = Integration(
        integration_id="article-1",
        competitor_id="brand-1",
        creator_id="article-placeholder",
        publisher_id=publisher.publisher_id,
        platform="articles",
        content_url="https://media.example/nike",
        raw_text="Партнёрский материал Nike",
        article_category="partner_content",
        category="confirmed",
        source_mode=SourceMode.LIVE,
    )

    finding = stage_build_findings([integration], [], [publisher], [])[0]

    assert finding["entity_type"] == "editorial_publisher"
    assert finding["entity_name"] == "Example Media"
    assert finding["classification"] == "editorial_publisher"


def test_next_move_candidates_expose_ranking_and_drawer_metrics():
    settings = Settings()
    used = Creator(
        creator_id="used",
        name="Used Creator",
        platform="youtube",
        followers=80_000,
        avg_views=20_000,
        median_views=18_000,
        engagement_rate=0.04,
        topic_tags=["fitness"],
    )
    candidate = Creator(
        creator_id="candidate",
        name="Candidate Creator",
        platform="youtube",
        canonical_url="https://youtube.com/@candidate",
        followers=75_000,
        avg_views=22_000,
        median_views=19_000,
        engagement_rate=0.05,
        topic_tags=["fitness"],
    )
    integration = Integration(
        integration_id="integration-1",
        competitor_id="brand-1",
        creator_id=used.creator_id,
        platform="youtube",
        published_at=NOW,
        content_type="review",
    )
    result = NextMoveBuilder(
        [used, candidate], [integration], settings, EvidenceStore(), potential_creator_ids={candidate.creator_id},
    ).build_for_competitor(Competitor(competitor_id="brand-1", name="Brand"))

    row = result["candidates"][0]
    assert row["creator_id"] == "candidate"
    assert row["platform"] == "youtube"
    assert row["followers"] == 75_000
    assert row["median_views"] == 19_000
    assert row["canonical_url"] == "https://youtube.com/@candidate"
    assert row["has_organic_brand_affinity"] is True
    assert row["not_used_by_brand"] is True
    assert 0 <= row["similarity_score"] <= 100


def test_white_space_segments_expose_clickable_matrix_details():
    settings = Settings()
    creator = Creator(
        creator_id="creator-1",
        name="Creator One",
        platform="youtube",
        canonical_url="https://youtube.com/@creator-one",
        followers=15_000,
        avg_views=8_000,
        median_views=7_000,
        engagement_rate=0.06,
        topic_tags=["fitness"],
        geo="RU",
        last_seen_at=NOW,
    )
    integration = Integration(
        integration_id="integration-1",
        competitor_id="brand-1",
        creator_id=creator.creator_id,
        platform="youtube",
        content_url="https://youtube.com/watch?v=source1",
        published_at=NOW,
        category="confirmed",
    )
    result = WhiteSpaceBuilder(
        [creator],
        [Competitor(competitor_id="brand-1", name="Brand")],
        [integration],
        OurProfile(preferred_topics=["fitness"], platforms=["youtube"], creator_size=["micro"], geo=["RU"]),
        settings,
        EvidenceStore(),
    ).build()

    segment = result["segments"][0]
    assert segment["segment"]["key"]
    assert segment["confirmed_integrations"] == 1
    assert segment["active_competitors"] == ["Brand"]
    assert segment["top_creators"][0]["canonical_url"] == "https://youtube.com/@creator-one"
    assert segment["top_creators"][0]["segment_match"] == 100
    assert segment["observed_sources"][0]["source_url"] == "https://youtube.com/watch?v=source1"


def test_new_ui_contains_dense_results_ranking_matrix_and_drawer():
    js = Path("static/analyze.js").read_text(encoding="utf-8")
    html = Path("static/index.html").read_text(encoding="utf-8")
    css = Path("static/analyze.css").read_text(encoding="utf-8")

    assert "Автор / издание" in js
    assert "Контент / источник" in js
    assert "data-finding-id" in js
    assert "rank-card" in js and "Соответствие показывает" in js
    assert "ws-matrix-grid" in js and "data-cell-index" in js
    assert "Кого можно схантить" in js
    assert "data-opportunity-index" in js
    assert 'id="detail-drawer"' in html
    assert 'id="mm-findings"' in html
    assert ".detail-drawer" in css
    assert ".hero-advanced-toggle" in css and "width: 100%" in css
    assert "Посмотреть демо на тестовых данных" not in html
