from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.analysis.pipeline import stage_discover_and_extract
from app.evidence import EvidenceStore
from app.platforms import REGISTRY
from app.platforms.base import PlatformDiscoveryResult


def test_real_instagram_connector_urls_survive_even_without_creator(monkeypatch):
    adapter = REGISTRY["instagram"]()
    raw = [
        {
            "username": None,
            "profile_url": None,
            "post_url": f"https://www.instagram.com/p/REAL{i}/",
            "caption": None,
            "brand_mention": False,
            "paid_partnership_label": False,
            "collaboration_label": False,
            "discovery_context": "brand_post",
            "links": [],
            "hashtags": [],
        }
        for i in range(12)
    ]
    monkeypatch.setattr(
        adapter,
        "discover_brand_content",
        lambda brand, config: PlatformDiscoveryResult(
            platform="instagram", status="ok", source_mode="live", raw_items=raw
        ),
    )
    monkeypatch.setattr("app.analysis.pipeline.get_platform_adapter", lambda platform: adapter)
    brand = ResolvedBrand(
        brand_name="syntx_ai", canonical_name="syntx_ai", aliases=[], input_type="name", detected_platform="instagram",
        normalized_handle="syntx_ai", source_url="https://www.instagram.com/syntx_ai/",
    )
    coverage, creators, confirmed, organic, extra, publishers = stage_discover_and_extract(
        "instagram", brand, "comp_syntx", AnalysisConfig(), EvidenceStore()
    )
    assert coverage.items_collected == 12
    assert len(extra) == 12
    assert all(item["status"] == "content_finding" for item in extra)
    assert not creators
    assert not confirmed
    assert not organic
    assert not publishers
