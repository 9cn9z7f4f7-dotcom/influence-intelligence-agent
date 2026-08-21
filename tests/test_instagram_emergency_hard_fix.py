from app.platforms.social_connector_base import SocialConnectorPlatformAdapter


class _InstagramAdapter(SocialConnectorPlatformAdapter):
    platform_name = "instagram"


def test_instagram_search_context_is_kept_as_observed_not_rejected():
    adapter = _InstagramAdapter()
    result = adapter.detect_integration({
        "username": "runner_creator",
        "profile_url": "https://www.instagram.com/runner_creator/",
        "post_url": "https://www.instagram.com/p/REALPOST/",
        "caption": "Morning run in new shoes",
        "brand_mention": False,
        "paid_partnership_label": False,
        "collaboration_label": False,
        "discovery_context": "search",
    }, ["nike"])
    assert result.category == "organic_mention"
    assert result.confidence == 0.25
    assert result.signals["platform_search_match"]["matched"] is True


def test_instagram_context_alone_never_confirms_sponsorship():
    adapter = _InstagramAdapter()
    result = adapter.detect_integration({
        "username": "runner_creator",
        "profile_url": "https://www.instagram.com/runner_creator/",
        "post_url": "https://www.instagram.com/p/REALPOST/",
        "caption": "Morning run",
        "brand_mention": False,
        "paid_partnership_label": False,
        "collaboration_label": False,
        "discovery_context": "brand_post",
    }, ["nike"])
    assert result.category == "organic_mention"
    assert result.category != "confirmed"


def test_instagram_tagged_context_with_collab_is_confirmed():
    adapter = _InstagramAdapter()
    result = adapter.detect_integration({
        "username": "runner_creator",
        "profile_url": "https://www.instagram.com/runner_creator/",
        "post_url": "https://www.instagram.com/p/REALPOST/",
        "caption": "",
        "brand_mention": True,
        "paid_partnership_label": False,
        "collaboration_label": True,
        "discovery_context": "tagged_brand",
    }, ["nike"])
    assert result.category == "confirmed"
