from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import Competitor, Creator, Integration, OurProfile
from config.settings import Settings

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def sample_creators() -> list[Creator]:
    return [
        Creator(creator_id="c1", name="Fit Creator", platform="youtube", followers=80_000,
                avg_views=20_000, engagement_rate=0.05, topic_tags=["fitness"], geo="RU",
                last_seen_at=NOW - timedelta(days=2)),
        Creator(creator_id="c2", name="Fit Creator 2", platform="youtube", followers=90_000,
                avg_views=22_000, engagement_rate=0.04, topic_tags=["fitness"], geo="RU",
                last_seen_at=NOW - timedelta(days=3)),
        Creator(creator_id="c3", name="Med Student Nano", platform="telegram", followers=5_000,
                avg_views=1_000, engagement_rate=0.08, topic_tags=["medical_students"], geo="RU",
                last_seen_at=NOW - timedelta(days=1)),
        Creator(creator_id="c4", name="Med Student Nano 2", platform="telegram", followers=6_000,
                avg_views=1_200, engagement_rate=0.07, topic_tags=["medical_students"], geo="RU",
                last_seen_at=NOW - timedelta(days=1)),
        Creator(creator_id="c5", name="No Followers Creator", platform="telegram",
                followers=None, avg_views=None, topic_tags=["medical_students"], geo="RU"),
    ]


@pytest.fixture
def sample_competitors() -> list[Competitor]:
    return [Competitor(competitor_id="comp1", name="Comp One"), Competitor(competitor_id="comp2", name="Comp Two")]


@pytest.fixture
def sample_integrations() -> list[Integration]:
    return [
        Integration(integration_id="i1", competitor_id="comp1", creator_id="c1", platform="youtube",
                    published_at=NOW - timedelta(days=5), content_type="review", detected_offer="discount_code",
                    detected_mechanic="dedicated_video"),
        Integration(integration_id="i2", competitor_id="comp1", creator_id="c1", platform="youtube",
                    published_at=NOW - timedelta(days=40), content_type="review", detected_offer="discount_code",
                    detected_mechanic="dedicated_video"),
        Integration(integration_id="i3", competitor_id="comp1", creator_id="c2", platform="youtube",
                    published_at=NOW - timedelta(days=45), content_type="review", detected_offer="discount_code",
                    detected_mechanic="dedicated_video"),
        Integration(integration_id="i4", competitor_id="comp2", creator_id="c3", platform="telegram",
                    published_at=NOW - timedelta(days=10), content_type="tutorial", detected_offer="free_trial",
                    detected_mechanic="pinned_post"),
    ]


@pytest.fixture
def sample_our_profile() -> OurProfile:
    return OurProfile(
        preferred_topics=["medical_students"], platforms=["telegram"], creator_size=["nano"],
        geo=["RU"], minimum_views=100, excluded_topics=["fitness"],
    )
