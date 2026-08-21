from datetime import datetime, timezone

from app.analysis.models import AnalysisConfig
from app.analysis.pipeline import stage_apply_config_filters
from app.models import Creator, Integration, SourceMode


def test_article_placement_survives_creator_filter_in_mixed_analysis():
    creator = Creator(
        creator_id="yt_creator", name="Creator", platform="youtube",
        canonical_url="https://youtube.com/channel/creator", followers=10000,
        source_mode=SourceMode.LIVE,
    )
    published = datetime.now(timezone.utc)
    youtube = Integration(
        integration_id="yt_int", competitor_id="brand", creator_id="yt_creator",
        platform="youtube", content_url="https://youtube.com/watch?v=1",
        published_at=published, category="confirmed", source_mode=SourceMode.LIVE,
    )
    article = Integration(
        integration_id="art_int", competitor_id="brand", creator_id="pub_example",
        publisher_id="pub_example", platform="articles",
        content_url="https://example.com/review/brand", published_at=published,
        category="organic_mention", article_category="editorial_review",
        source_mode=SourceMode.LIVE,
    )

    creators, integrations = stage_apply_config_filters(
        [creator], [youtube, article], AnalysisConfig(include_organic=True)
    )

    assert [c.creator_id for c in creators] == ["yt_creator"]
    assert {i.integration_id for i in integrations} == {"yt_int", "art_int"}
