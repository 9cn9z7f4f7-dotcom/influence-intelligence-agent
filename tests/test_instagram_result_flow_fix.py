from app.analysis.models import AnalysisConfig
from app.analysis.pipeline import stage_apply_date_filter, stage_build_universe_pool
from app.models import Integration, SourceMode
from app.platforms.instagram import InstagramPlatformAdapter


def _ig_integration():
    return Integration(
        integration_id="ig1",
        competitor_id="comp1",
        creator_id="creator1",
        platform="instagram",
        content_url="https://www.instagram.com/p/abc/",
        published_at=None,
        content_type="post",
        raw_text="Nike running shoes marathon training",
        category="organic_mention",
        source_mode=SourceMode.LIVE,
    )


def test_undated_instagram_observation_survives_date_filter():
    kept = stage_apply_date_filter([_ig_integration()], AnalysisConfig())
    assert len(kept) == 1
    assert kept[0].platform == "instagram"


def test_social_only_analysis_does_not_claim_universe_is_youtube_only():
    creators, status, notes, queries = stage_build_universe_pool(["instagram"], AnalysisConfig(), ["sports"])
    assert creators == []
    assert status == "ok"
    assert notes == []
    assert queries == []


def test_instagram_creator_gets_topic_from_observed_caption():
    creator = InstagramPlatformAdapter().extract_creator({
        "username": "runner_demo",
        "profile_url": "https://www.instagram.com/runner_demo/",
        "caption": "Nike running shoes for marathon training and race day",
    })
    assert creator is not None
    assert creator.platform == "instagram"
    assert creator.topic_tags
    assert "sports" in creator.topic_tags
