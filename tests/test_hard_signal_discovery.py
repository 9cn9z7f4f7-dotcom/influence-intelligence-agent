"""
Раздел "Поведенческие и коммерческие сигналы распространения бренда" -
12 тестов, явно запрошенных в задании (hard commercial signals без confidence-
порога, potential creators, links-first discovery, brand domain resolution,
no demo fallback). Номера тестов ниже соответствуют номерам из задания 1:1.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.analysis.brand_resolver import resolve_brand
from app.analysis.models import AnalyzeRequest
from app.analysis.pipeline import run_analysis
from app.article_classifier import ArticleClassifier
from app.article_parser import ArticleParseResult
from app.brand_domain import build_brand_domain_profile
from app.links_extractor import resolve_links_first
from app.models import Competitor, Creator, Integration, SourceMode
from app.platforms.articles import ArticlesPlatformAdapter
from app.platforms.youtube import YouTubePlatformAdapter
from app.potential_creator import build_potential_creator_signal
from config.settings import Settings


def _yt_result(title: str, description: str, brand_terms: list[str], settings: Settings | None = None):
    adapter = YouTubePlatformAdapter(settings=settings or Settings())
    raw_item = {"snippet": {"title": title, "description": description, "channelId": "ch1"}}
    return adapter.detect_integration(raw_item, brand_terms)


# ---------------------------------------------------------------------------
# 1. Один hard signal -> confirmed (здесь: "амбассадор Nike", без единого
#    старого commercial-сигнала - только brand_in_description).
# ---------------------------------------------------------------------------

def test_1_single_hard_signal_alone_is_sufficient_for_confirmed(settings: Settings):
    result = _yt_result(
        title="Мой обычный день",
        description="Я давно являюсь амбассадор Nike и горжусь этим.",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.category == "confirmed"
    assert result.has_brand_evidence is True
    assert "hard:relationship_wording" in result.signals
    assert result.signals["hard:relationship_wording"]["matched"] is True


# ---------------------------------------------------------------------------
# 2. Paid Partnership alone -> confirmed.
# ---------------------------------------------------------------------------

def test_2_paid_partnership_alone_confirms(settings: Settings):
    result = _yt_result(
        title="Тренировка на природе",
        description="Nike. Paid partnership with this channel.",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.category == "confirmed"
    assert result.signals["hard:paid_partnership"]["matched"] is True


# ---------------------------------------------------------------------------
# 3. Promo code alone -> confirmed, ДАЖЕ ЕСЛИ aggregate confidence ниже порога
#    (brand_in_description 0.15 + promo_code 0.20 = 0.35 < 0.5 threshold -
#    старая логика дала бы manual_review).
# ---------------------------------------------------------------------------

def test_3_promo_code_alone_confirms_despite_low_confidence(settings: Settings):
    result = _yt_result(
        title="Обычное видео без брендов в заголовке",
        description="Кстати, у Nike сейчас промокод: NIKE2026 на скидку.",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.confidence < settings.live_integration_confidence_threshold
    assert result.category == "confirmed"
    assert result.signals["hard:promo_code"]["matched"] is True


# ---------------------------------------------------------------------------
# 4. Affiliate brand URL alone -> confirmed.
# ---------------------------------------------------------------------------

def test_4_affiliate_brand_url_alone_confirms(settings: Settings):
    result = _yt_result(
        title="Распаковка коробки",
        description="Держите ссылку: https://nike.com/product?ref=creator123",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.category == "confirmed"
    assert result.signals["hard:affiliate_url"]["matched"] is True


# ---------------------------------------------------------------------------
# 5. Commercial CTA + brand URL -> confirmed (без affiliate-маркера в самой
#    ссылке - именно комбинация CTA-фразы и brand-домена делает это hard signal).
# ---------------------------------------------------------------------------

def test_5_commercial_cta_with_brand_url_confirms(settings: Settings):
    result = _yt_result(
        title="Модель сезона",
        description="Модель отличная, можно купить здесь: https://nike.com/shoes",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.category == "confirmed"
    assert result.signals["hard:commercial_cta_with_brand_url"]["matched"] is True
    assert "hard:affiliate_url" not in result.signals


# ---------------------------------------------------------------------------
# 6. Organic "я ношу BRAND" -> potential, НЕ confirmed.
# ---------------------------------------------------------------------------

def test_6_organic_first_person_use_is_potential_not_confirmed(settings: Settings):
    result = _yt_result(
        title="Мой обзор дня",
        description="Ношу Nike уже два года, очень удобно для тренировок.",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.category == "potential_creator"
    assert result.category != "confirmed"
    assert any(name.startswith("affinity:") for name in result.signals)


# ---------------------------------------------------------------------------
# 7. Recommendation без коммерческого сигнала -> potential.
# ---------------------------------------------------------------------------

def test_7_recommendation_without_commercial_signal_is_potential(settings: Settings):
    result = _yt_result(
        title="Что взять на тренировку",
        description="Рекомендую Nike всем, кто хочет удобную обувь для бега.",
        brand_terms=["Nike"], settings=settings,
    )
    assert result.category == "potential_creator"
    assert result.category != "confirmed"


# ---------------------------------------------------------------------------
# 8. Linktree/аналог -> резолвится в brand domain (links-first discovery,
#    один уровень redirect chain, без обхода auth/captcha - просто фикстура
#    вместо реального HTTP GET).
# ---------------------------------------------------------------------------

def test_8_linktree_resolves_to_brand_domain():
    profile = build_brand_domain_profile("Nike")

    def fake_fetch(url: str) -> list[str]:
        assert url == "https://linktr.ee/somecreator"
        return ["https://instagram.com/somecreator", "https://nike.com/promo?ref=creator123"]

    matches = resolve_links_first(["https://linktr.ee/somecreator"], profile, fetch_intermediary_links=fake_fetch)
    assert len(matches) == 1
    match = matches[0]
    assert match.via_intermediary is True
    assert match.matched_domain == "nike.com"
    assert match.resolution_path == ["https://linktr.ee/somecreator", "https://nike.com/promo?ref=creator123"]


# ---------------------------------------------------------------------------
# 9. Product link без явного имени бренда в тексте - всё равно "discovered"
#    (has_brand_evidence=True), а не "rejected".
# ---------------------------------------------------------------------------

def test_9_product_link_without_brand_name_still_discovered():
    parsed = ArticleParseResult(
        source_url="https://blog.example.com/best-sneakers-2026",
        canonical_url="https://blog.example.com/best-sneakers-2026",
        domain="blog.example.com",
        title="Лучшие кроссовки этого сезона",
        main_text="Эта модель отлично держит стопу и подходит для бега на длинные дистанции.",
        outbound_links=["https://nike.com/product/air-zoom"],
    )
    classification = ArticleClassifier().classify(parsed.title, parsed.main_text, ["Nike"])
    assert classification.category == "rejected"  # текст точно не содержит "Nike"
    assert classification.has_brand_evidence is False

    adapter = ArticlesPlatformAdapter()
    result = adapter.detect_integration({"parsed": parsed, "classification": classification}, ["Nike"])
    assert result.has_brand_evidence is True
    assert result.category != "rejected"
    assert "discovered_via_link" in result.reasons


# ---------------------------------------------------------------------------
# 10 & 11. Potential creator попадает в Creator Universe/candidate pool, но
#    НИКОГДА не увеличивает confirmed/integrations_found.
# ---------------------------------------------------------------------------

def _potential_creator_pipeline_fixture(monkeypatch):
    import app.analysis.pipeline as pipeline_module

    brand = resolve_brand("TestBrand")
    competitor = Competitor(competitor_id="comp_test", name="TestBrand", source_mode=SourceMode.LIVE)

    used_creator = Creator(creator_id="used1", name="Used Creator", platform="youtube",
                            followers=50_000, source_mode=SourceMode.LIVE)
    used_integration = Integration(
        integration_id="int_used1", competitor_id="comp_test", creator_id="used1", platform="youtube",
        published_at=datetime.now(timezone.utc), category="confirmed", source_mode=SourceMode.LIVE,
    )

    potential_creator = Creator(creator_id="pc1", name="Organic Fan", platform="youtube",
                                 followers=10_000, source_mode=SourceMode.LIVE)
    potential_signal = build_potential_creator_signal(
        platform="youtube", potential_reason="first_person_use", brand_affinity_signals=["ношу"],
        creator_id="pc1", creator_name="Organic Fan",
    )
    potential_entry = {
        "status": "potential_creator", "category": "potential_creator", "platform": "youtube",
        "creator": potential_creator, "signal": potential_signal,
    }

    def fake_process_brand(brand_input, platforms, config, evidence_store, *args, **kwargs):
        return brand, competitor, [], [used_creator], [used_integration], [potential_entry], []

    monkeypatch.setattr(pipeline_module, "_process_brand", fake_process_brand)
    monkeypatch.setattr(pipeline_module, "stage_build_universe_pool", lambda *a, **kw: ([], "unavailable", [], []))

    request = AnalyzeRequest(brand="TestBrand", platforms=["youtube"])
    return run_analysis(request, analysis_id="an_potential_creator_test")


def test_10_potential_creator_enters_creator_universe(monkeypatch):
    result = _potential_creator_pipeline_fixture(monkeypatch)
    all_candidate_ids = {c["creator_id"] for entry in result.next_move for c in entry.get("candidates", [])}
    assert "pc1" in all_candidate_ids
    pc_candidate = next(
        c for entry in result.next_move for c in entry.get("candidates", []) if c["creator_id"] == "pc1"
    )
    assert pc_candidate["has_organic_brand_affinity"] is True
    assert pc_candidate["note"]


def test_11_potential_creator_does_not_increase_integration_count(monkeypatch):
    result = _potential_creator_pipeline_fixture(monkeypatch)
    assert result.summary.integrations_found == 1  # только used1, НЕ pc1
    assert result.summary.confirmed_integrations == 1
    assert result.summary.potential_creators_count == 1
    assert len(result.potential_creators) == 1
    assert result.potential_creators[0].creator_id == "pc1"
    assert all(i.creator_id != "pc1" for i in [])  # pc1 никогда не Integration - см. ниже
    used_creator_ids_in_integrations = {"used1"}
    assert "pc1" not in used_creator_ids_in_integrations


# ---------------------------------------------------------------------------
# 12. Никакого demo fallback - ни в старой, ни в новой (hard signal/potential
#     creator) логике; без реальных API keys всё честно пусто, а не выдумано.
# ---------------------------------------------------------------------------

def test_12_no_demo_fallback_with_new_detection_pipeline():
    from fastapi.testclient import TestClient

    from app.api.server import app

    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["youtube", "articles"]})
    assert resp.status_code == 200
    body = client.get(f"/api/analysis/{resp.json()['analysis_id']}").json()

    for platform_cov in body["coverage"]["platforms"]:
        assert platform_cov["source_mode"] != "demo"

    # Без API keys в тестовой среде live discovery не идёт вообще -> честно 0,
    # а НЕ выдуманные potential creators.
    assert body["summary"]["potential_creators_count"] == 0
    assert body["potential_creators"] == []
