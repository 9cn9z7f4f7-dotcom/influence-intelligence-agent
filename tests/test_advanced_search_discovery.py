from datetime import datetime, timezone

from app.analysis.models import AnalysisConfig
from app.analysis.pipeline import stage_apply_config_filters
from app.models import Integration, SourceMode
from app.query_generator import generate_article_queries, generate_discovery_queries


def _article(text: str, url: str = "https://example.com/blog/post") -> Integration:
    return Integration(
        integration_id="article_1",
        competitor_id="comp_1",
        creator_id="pub_1",
        platform="articles",
        content_url=url,
        published_at=datetime.now(timezone.utc),
        content_type="article",
        raw_text=text,
        category="organic_mention",
        article_category="editorial_review",
        source_mode=SourceMode.LIVE,
    )


def test_article_queries_use_include_exclude_and_date_settings():
    queries = generate_article_queries(
        "Nike",
        include_topics=["running"],
        exclude_topics=["beauty"],
        date_range="30d",
        max_queries=6,
    )
    assert queries
    assert any("nike running" in q.lower() for q in queries)
    assert all('-"beauty"' in q.lower() for q in queries)
    assert all("after:" in q and "before:" in q for q in queries)


def test_unknown_explicit_topic_is_not_dropped_from_creator_discovery():
    queries = generate_discovery_queries(include_topics=["streetwear"], observed_topics=["fitness"])
    assert queries
    assert any("streetwear" in q.lower() for q in queries)
    assert not any("fitness" in q.lower() for q in queries)


def test_article_topic_settings_are_rechecked_after_discovery():
    config = AnalysisConfig(include_topics=["running"], exclude_topics=["beauty"], include_organic=True)
    good = _article("Nike running shoe review for marathon training")
    bad = _article("Nike beauty collaboration and makeup launch", "https://example.com/blog/beauty")
    creators, integrations = stage_apply_config_filters([], [good, bad], config)
    assert creators == []
    assert [i.integration_id for i in integrations] == ["article_1"]
    assert integrations[0].content_url == good.content_url
