from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.platforms.social_connector_base import SocialConnectorPlatformAdapter


def test_instagram_official_url_is_valid_config_hint():
    cfg = AnalysisConfig(instagram_brand_url="https://www.instagram.com/nike/")
    assert cfg.instagram_brand_url == "https://www.instagram.com/nike/"


def test_frontend_contains_instagram_official_url_field():
    html = open("static/index.html", encoding="utf-8").read()
    js = open("static/analyze.js", encoding="utf-8").read()
    assert 'id="instagram-official-group"' in html
    assert 'id="cfg-instagram-url"' in html
    assert 'instagram_brand_url' in js
    assert 'updateInstagramOfficialField' in js
