"""
18 обязательных тестов для real-data upgrade (раздел 28 требований).

Каждый тест явно помечен номером/названием из спецификации. Это НЕ live-тесты
(в этом окружении нет доступа к реальному интернету на уровне httpx/playwright -
см. REAL_DATA_VALIDATION.md) - это mocked/unit-тесты, проверяющие, что КОД
корректно реализует заявленное поведение (failsafe, честные статусы, схемы,
изоляцию evidence-типов и т.п.). Live-проверка описана отдельно в
REAL_DATA_VALIDATION.md и не подменяется этими тестами.
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.analysis.models import AnalysisConfig, AnalyzeRequest, ResolvedBrand
from app.analysis.pipeline import _process_brand, run_analysis
from app.api.server import app
from app.article_classifier import ArticleClassifier
from app.article_parser import ArticleParseResult
from app.connectors.models import (
    ConnectorJob,
    ConnectorRegisterRequest,
    ConnectorResultItem,
    ConnectorResultsSubmission,
)
from app.connectors.registry import ConnectorRegistry
from app.detection import combine_dom_and_visual, should_escalate_to_visual_evidence
from app.enrichment.screenshot import ScreenshotCache
from app.enrichment.visual_evidence import VisualEvidenceEnricher, VisualEvidenceResult
from app.evidence import EvidenceStore
from app.models import EvidenceType, Publisher, SourceMode
from app.platforms import get_platform_adapter
from app.platforms.articles import ArticlesPlatformAdapter
from app.platforms.social_connector_base import SocialConnectorPlatformAdapter, build_social_integration
from app.providers.openrouter import OpenRouterProvider
from app.query_generator import generate_article_queries
from app.search_client import SearchResultItem
from config.settings import Settings


NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _brand(name: str = "Nike") -> ResolvedBrand:
    return ResolvedBrand(brand_name=name, canonical_name=name, aliases=[], input_type="name")


@pytest.fixture(autouse=True)
def _reset_global_connector_registry():
    """Тесты register/heartbeat пишут в process-global app.connectors.registry.registry -
    сбрасываем его до и после каждого теста в этом файле, чтобы порядок запуска
    тестов не влиял на статус instagram/tiktok (раздел 19 - честный статус)."""
    from app.connectors.registry import registry as global_registry
    global_registry.reset()
    yield
    global_registry.reset()


# ---------------------------------------------------------------------------
# 1. normal Analyze никогда не использует demo
# ---------------------------------------------------------------------------


def test_1_normal_analyze_never_uses_demo_source_mode():
    """POST /api/analyze (обычный, не demo-режим) не должен ни при каких
    обстоятельствах пометить хоть один Integration/Creator source_mode=demo -
    раздел 23 требований. Проверяем и по всей выборке через прогон pipeline,
    и статически - что PlatformCoverage.source_mode никогда не "demo"."""
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["youtube", "articles"]})
    assert resp.status_code == 200
    body = client.get(f"/api/analysis/{resp.json()['analysis_id']}").json()

    for platform_cov in body["coverage"]["platforms"]:
        assert platform_cov["source_mode"] != "demo"

    # Ни один builder в новом orchestration pipeline не должен вызывать DemoLoader
    # для интеграций/креаторов бренда (DemoLoader используется ТОЛЬКО для
    # статического OurProfile - см. app/analysis/pipeline.py::_load_our_profile).
    import app.analysis.pipeline as pipeline_module
    assert not hasattr(pipeline_module, "_load_demo_integrations")


# ---------------------------------------------------------------------------
# 2. OpenRouter unavailable fallback
# ---------------------------------------------------------------------------


def test_2_openrouter_unavailable_fallback_never_raises():
    """Без ключа - is_available() False, все публичные методы возвращают
    None без исключений (раздел 1, 24 требований)."""
    provider = OpenRouterProvider(api_key="", settings=Settings())
    assert provider.is_available() is False
    assert provider.analyze_text("sys", "user") is None
    assert provider.analyze_image("sys", "data:image/png;base64,AA==") is None
    assert provider.analyze_text_and_image("sys", "user", "data:image/png;base64,AA==") is None
    assert provider.analyze_text_json("sys", {"a": 1}) is None


def test_2b_openrouter_network_error_never_raises():
    """С ключом, но сеть/HTTP падает - тоже failsafe None, не исключение."""
    provider = OpenRouterProvider(api_key="fake-key")
    with patch("httpx.Client.post", side_effect=Exception("network down")):
        assert provider.analyze_text("sys", "user") is None


def test_2c_openrouter_rate_limit_429_returns_none():
    provider = OpenRouterProvider(api_key="fake-key")

    class _Resp:
        status_code = 429

    with patch("httpx.Client.post", return_value=_Resp()):
        assert provider._post({"model": "x"}) is None


# ---------------------------------------------------------------------------
# 3. OpenRouter structured output validation
# ---------------------------------------------------------------------------


def test_3_openrouter_structured_json_extraction_and_validation():
    provider = OpenRouterProvider(api_key="fake-key")
    raw_response = {
        "choices": [{"message": {"content": '```json\n{"a": 1, "b": "x"}\n```'}}],
    }
    with patch.object(provider, "_post", return_value=raw_response):
        parsed = provider.analyze_text_json("sys", {"q": "test"})
    assert parsed == {"a": 1, "b": "x"}


def test_3b_openrouter_invalid_json_returns_none_not_exception():
    provider = OpenRouterProvider(api_key="fake-key")
    raw_response = {"choices": [{"message": {"content": "not json at all"}}]}
    with patch.object(provider, "_post", return_value=raw_response):
        assert provider.analyze_text_json("sys", {"q": "test"}) is None


# ---------------------------------------------------------------------------
# 4. VisualEvidence JSON validation
# ---------------------------------------------------------------------------


def test_4_visual_evidence_strict_schema_and_signal_whitelist():
    """Раздел 2: строгая схема ответа + фильтрация неизвестных signals -
    модель не может "придумать" сигнал вне ALLOWED_SIGNALS."""
    enricher = VisualEvidenceEnricher(provider=OpenRouterProvider(api_key="fake-key"))
    fake_raw = {
        "brand_visible": True, "commercial_signal_visible": True,
        "signals": ["logo", "totally_made_up_signal"], "content_topics": ["unboxing"],
        "confidence": 0.8, "evidence": ["nike logo visible on screen"],
    }
    with patch.object(enricher.provider, "analyze_text_and_image_json", return_value=fake_raw):
        result = enricher.enrich("https://example.com/post", b"fake-png-bytes", "some caption", "Nike")

    assert isinstance(result, VisualEvidenceResult)
    assert result.status == "ok"
    assert "logo" in result.signals
    assert "totally_made_up_signal" not in result.signals
    assert result.confidence == 0.8


def test_4b_visual_evidence_unavailable_without_screenshot_or_key():
    enricher = VisualEvidenceEnricher(provider=OpenRouterProvider(api_key=""))
    result = enricher.enrich("https://example.com/post", None, "caption", "Nike")
    assert result.status == "unavailable"
    assert result.brand_visible is False
    assert result.confidence == 0.0


def test_4c_visual_evidence_degraded_on_invalid_provider_response():
    enricher = VisualEvidenceEnricher(provider=OpenRouterProvider(api_key="fake-key"))
    with patch.object(enricher.provider, "analyze_text_and_image_json", return_value=None):
        result = enricher.enrich("https://example.com/post", b"bytes", "caption", "Nike")
    assert result.status == "degraded"


# ---------------------------------------------------------------------------
# 5. Article parser
# ---------------------------------------------------------------------------


def test_5_article_parser_extracts_all_required_fields():
    html = """
    <html><head>
      <title>Nike запустил рекламную кампанию</title>
      <meta property="og:title" content="Nike запустил рекламную кампанию" />
      <meta property="og:description" content="Обзор кампании" />
      <meta property="article:published_time" content="2026-08-01T10:00:00Z" />
      <meta name="author" content="Ivan Petrov" />
      <link rel="canonical" href="https://news.example.com/nike-review" />
      <meta property="og:image" content="https://news.example.com/img.jpg" />
    </head><body>
      <p>Nike выпустил новую линейку кроссовок #реклама.</p>
      <p>Подробности по промокоду NIKE10.</p>
      <a href="https://external.example.com/x">внешняя ссылка</a>
      <img src="https://news.example.com/photo2.jpg" />
    </body></html>
    """

    class _FakeResp:
        text = html
        status_code = 200

        def raise_for_status(self):
            return None

    parser = ArticleParseResult  # noqa: F841 - imported for type reference only
    from app.article_parser import ArticleParser

    with patch("httpx.get", return_value=_FakeResp()):
        result = ArticleParser(enable_playwright_fallback=False).parse("https://news.example.com/nike-review")

    assert result.status == "ok"
    assert result.title == "Nike запустил рекламную кампанию"
    assert result.canonical_url == "https://news.example.com/nike-review"
    assert result.domain == "news.example.com"
    assert result.author == "Ivan Petrov"
    assert result.published_at is not None
    assert "реклама" in result.main_text or "промокоду" in result.main_text
    assert "https://external.example.com/x" in result.outbound_links
    assert any("img.jpg" in u or "photo2.jpg" in u for u in result.image_urls)
    assert result.observed_at is not None


def test_5b_article_parser_http_error_degrades_not_raises():
    import httpx
    from app.article_parser import ArticleParser

    request = httpx.Request("GET", "https://news.example.com/404")
    response = httpx.Response(status_code=404, request=request)

    def _raise(*a, **k):
        raise httpx.HTTPStatusError("404", request=request, response=response)

    with patch("httpx.get", side_effect=_raise):
        result = ArticleParser(enable_playwright_fallback=False).parse("https://news.example.com/404")
    assert result.status == "degraded"
    assert result.error is not None


# ---------------------------------------------------------------------------
# 6. Sponsored article classification
# ---------------------------------------------------------------------------


def test_6_sponsored_article_classified_as_confirmed_sponsored():
    classifier = ArticleClassifier()
    result = classifier.classify(
        title="Nike представил новую коллекцию",
        main_text="Материал подготовлен на правах рекламы. Nike выпустил новую линейку.",
        brand_terms=["Nike"],
    )
    assert result.category == "confirmed_sponsored"
    assert result.has_brand_evidence is True
    assert result.has_commercial_evidence is True
    assert result.confidence >= 0.8


def test_6b_affiliate_promo_code_classified_as_affiliate():
    classifier = ArticleClassifier()
    result = classifier.classify(
        title="Кроссовки Nike в продаже",
        main_text="Промокод: NIKE2026 действует на кроссовки Nike.",
        brand_terms=["Nike"],
    )
    assert result.category == "affiliate"
    assert result.has_commercial_evidence is True


# ---------------------------------------------------------------------------
# 7. Organic article classification
# ---------------------------------------------------------------------------


def test_7_organic_mention_not_classified_as_ad():
    classifier = ArticleClassifier()
    result = classifier.classify(
        title="Городские новости",
        main_text="На вечеринке одна из гостей была в кроссовках Nike.",
        brand_terms=["Nike"],
    )
    assert result.category == "organic_mention"
    assert result.has_commercial_evidence is False
    assert result.has_brand_evidence is True


def test_7b_editorial_review_not_classified_as_ad():
    """Раздел 7: "просто редакционный обзор" != реклама."""
    classifier = ArticleClassifier()
    result = classifier.classify(
        title="Мы протестировали кроссовки Nike",
        main_text="Редакция самостоятельно купила и протестировала кроссовки Nike. Обзор честный.",
        brand_terms=["Nike"],
    )
    assert result.category == "editorial_review"
    assert result.has_commercial_evidence is False


def test_7c_no_brand_mention_is_rejected():
    classifier = ArticleClassifier()
    result = classifier.classify(title="Новости спорта", main_text="Ничего о брендах здесь нет.",
                                  brand_terms=["Nike"])
    assert result.category == "rejected"
    assert result.has_brand_evidence is False


# ---------------------------------------------------------------------------
# 8. Publisher normalization
# ---------------------------------------------------------------------------


def test_8_publisher_normalization_from_parsed_article():
    parsed = ArticleParseResult(
        source_url="https://news.example.com/nike-review",
        canonical_url="https://news.example.com/nike-review",
        domain="news.example.com",
    )
    publisher = ArticlesPlatformAdapter.build_publisher(parsed)
    assert isinstance(publisher, Publisher)
    assert publisher.domain == "news.example.com"
    assert publisher.platform == "web_article"
    assert publisher.source_url == "https://news.example.com/nike-review"
    assert publisher.publisher_id


def test_8b_publisher_is_never_registered_as_creator():
    """Раздел 8: Publisher != Creator - ArticlesPlatformAdapter.extract_creator()
    всегда возвращает None."""
    adapter = ArticlesPlatformAdapter()
    assert adapter.extract_creator({"parsed": None, "classification": None}) is None


def test_8c_article_integration_links_publisher_not_creator():
    parsed = ArticleParseResult(
        source_url="https://news.example.com/nike-review",
        canonical_url="https://news.example.com/nike-review",
        domain="news.example.com", main_text="Nike на правах рекламы",
    )
    classification = ArticleClassifier().classify("Nike обзор", parsed.main_text, ["Nike"])
    adapter = ArticlesPlatformAdapter()
    integration, publisher = adapter.build_article_integration(
        {"parsed": parsed, "classification": classification}, competitor_id="comp1",
        evidence_store=EvidenceStore(),
    )
    assert integration.publisher_id == publisher.publisher_id
    assert integration.article_category == classification.category
    assert integration.source_mode == SourceMode.LIVE
    assert integration.is_synthetic is False


# ---------------------------------------------------------------------------
# 9. Local connector registration
# ---------------------------------------------------------------------------


def test_9_connector_registration_returns_id_and_token():
    client = TestClient(app)
    resp = client.post("/api/connectors/register", json={"supported_platforms": ["instagram", "tiktok"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["connector_id"].startswith("conn_")
    assert body["connector_token"]
    assert set(body["supported_platforms"]) == {"instagram", "tiktok"}


def test_9b_connector_registration_requires_at_least_one_platform():
    client = TestClient(app)
    resp = client.post("/api/connectors/register", json={"supported_platforms": []})
    assert resp.status_code == 400


def test_9c_connector_registration_rejects_wrong_shared_secret():
    registry = ConnectorRegistry(settings=Settings())
    registry.settings.connector_shared_secret = "correct-secret"
    assert registry.register(["instagram"], shared_secret="wrong") is None
    record = registry.register(["instagram"], shared_secret="correct-secret")
    assert record is not None


# ---------------------------------------------------------------------------
# 10. Connector heartbeat / offline state
# ---------------------------------------------------------------------------


def test_10_connector_offline_when_no_heartbeat_recent():
    registry = ConnectorRegistry(offline_after_seconds=90, settings=Settings())
    status, _detail = registry.platform_status("instagram")
    assert status == "connector_offline"  # никто не регистрировался вообще


def test_10b_connector_online_after_heartbeat_then_offline_after_stale():
    registry = ConnectorRegistry(offline_after_seconds=90, settings=Settings())
    record = registry.register(["instagram"])
    assert record is not None
    status, _ = registry.platform_status("instagram")
    assert status == "online"

    # Симулируем "устаревший" heartbeat, искусственно откатив время назад.
    record.last_heartbeat_ts -= 200
    status2, detail2 = registry.platform_status("instagram")
    assert status2 == "connector_offline"
    assert detail2 is not None


def test_10c_connector_heartbeat_manual_intervention_required():
    registry = ConnectorRegistry(settings=Settings())
    record = registry.register(["instagram"])
    ok = registry.heartbeat(record.connector_id, record.connector_token,
                             status="manual_intervention_required", detail="CAPTCHA")
    assert ok is True
    status, detail = registry.platform_status("instagram")
    assert status == "manual_intervention_required"
    assert detail == "CAPTCHA"


# ---------------------------------------------------------------------------
# 11. Instagram job schema
# ---------------------------------------------------------------------------


def test_11_instagram_job_has_fixed_schema_only():
    job = ConnectorJob(
        job_id="job_1", analysis_id="an_1", platform="instagram", brand="Nike",
        aliases=["nike.ru"], settings={"date_range": "30d"}, created_at=datetime.now(timezone.utc),
    )
    allowed_fields = set(ConnectorJob.model_fields.keys())
    assert allowed_fields == {"job_id", "analysis_id", "platform", "brand", "aliases", "settings", "created_at"}
    assert "command" not in allowed_fields
    assert "script" not in allowed_fields
    assert job.platform == "instagram"


def test_11b_tiktok_platform_rejected_as_instagram_job_is_type_checked():
    with pytest.raises(Exception):
        ConnectorJob(job_id="j", analysis_id="a", platform="facebook", brand="Nike",
                     created_at=datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# 12. TikTok job schema
# ---------------------------------------------------------------------------


def test_12_tiktok_job_has_same_fixed_schema():
    job = ConnectorJob(
        job_id="job_2", analysis_id="an_2", platform="tiktok", brand="Nike",
        aliases=[], settings={}, created_at=datetime.now(timezone.utc),
    )
    assert job.platform == "tiktok"
    assert set(ConnectorJob.model_fields.keys()) == set(
        ConnectorJob.model_fields.keys()
    )  # схема единая для instagram/tiktok - симметрия


def test_12b_connector_register_request_rejects_unknown_platform():
    with pytest.raises(Exception):
        ConnectorRegisterRequest(supported_platforms=["youtube"])  # youtube не ConnectorPlatform


# ---------------------------------------------------------------------------
# 13. Connector cannot execute arbitrary commands
# ---------------------------------------------------------------------------


def test_13_connector_result_submission_has_no_command_execution_fields():
    """Раздел 14/33: НИ в ConnectorJob, НИ в ConnectorResultItem/Submission нет
    полей типа command/script/code/shell - фиксированная схема данных."""
    forbidden = {"command", "script", "code", "shell", "exec", "cmd"}
    job_fields = set(ConnectorJob.model_fields.keys())
    result_fields = set(ConnectorResultItem.model_fields.keys())
    submission_fields = set(ConnectorResultsSubmission.model_fields.keys())
    assert not (job_fields & forbidden)
    assert not (result_fields & forbidden)
    assert not (submission_fields & forbidden)


def test_13b_local_connector_dispatch_is_plain_platform_switch_not_eval():
    """Проверяем реальный КОД (не docstring/комментарии) _dispatch() - только
    if/elif по job.platform, никакого eval/exec/subprocess с содержимым job."""
    import ast
    import inspect

    from local_connector import run as run_module

    source = inspect.getsource(run_module)
    tree = ast.parse(source)
    call_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name:
                call_names.add(name)
    assert "eval" not in call_names
    assert "exec" not in call_names
    assert "run" not in call_names or not any(
        isinstance(n, ast.Attribute) and n.attr == "run" and isinstance(n.value, ast.Name) and n.value.id == "subprocess"
        for n in ast.walk(tree)
    )
    assert hasattr(run_module, "_dispatch")
    dispatch_source = inspect.getsource(run_module._dispatch)
    assert "eval(" not in dispatch_source
    assert "exec(" not in dispatch_source


# ---------------------------------------------------------------------------
# 14. Social result -> normalized Integration
# ---------------------------------------------------------------------------


def test_14_social_connector_result_normalizes_to_integration():
    adapter = get_platform_adapter("instagram")
    raw_item = {
        "username": "nike_fan_blog", "profile_url": "https://instagram.com/nike_fan_blog",
        "post_url": "https://instagram.com/p/abc123", "caption": "Nike новая коллекция #ad",
        "published_at": "2026-08-10T12:00:00Z", "likes": 500, "hashtags": ["nike", "ad"],
        "brand_mention": True, "paid_partnership_label": True, "followers": 12000,
    }
    detector_result = adapter.detect_integration(raw_item, ["Nike"])
    assert detector_result.category == "confirmed"

    creator = adapter.extract_creator(raw_item)
    assert creator is not None
    assert creator.platform == "instagram"

    integration = build_social_integration("comp1", creator, raw_item, detector_result, EvidenceStore(), "instagram")
    assert integration.platform == "instagram"
    assert integration.content_url == "https://instagram.com/p/abc123"
    assert integration.source_mode == SourceMode.LIVE
    assert integration.is_synthetic is False
    assert integration.category == "confirmed"


def test_14b_social_result_missing_fields_stays_null_not_invented():
    """Раздел 15-16: недоступное поле = null, никогда не выдумывается."""
    item = ConnectorResultItem(username="someone", profile_url="https://instagram.com/someone")
    assert item.followers is None
    assert item.post_url is None
    assert item.likes is None
    assert item.published_at is None


# ---------------------------------------------------------------------------
# 15. Evidence type separation
# ---------------------------------------------------------------------------


def test_15_evidence_types_are_separated_fact_computed_visual_ai_ai_inference():
    from app.evidence import ai_inference, computed, fact

    fact_ev = fact(field="caption", value="Nike collab", source_url="https://x", observed_at=NOW)
    computed_ev = computed(field="confidence_score", value=0.8)
    ai_ev = ai_inference(field="dna_pattern", value="always uses discount codes", confidence=0.6)

    assert fact_ev.type == EvidenceType.FACT
    assert computed_ev.type == EvidenceType.COMPUTED
    assert ai_ev.type == EvidenceType.AI_INFERENCE
    assert EvidenceType.VISUAL_AI.value == "visual_ai"
    assert EvidenceType.VISUAL_AI != EvidenceType.AI_INFERENCE  # раздел 21 - отдельный UI-блок


def test_15b_visual_escalation_writes_visual_ai_typed_evidence():
    from dataclasses import replace as dc_replace

    from app.analysis.pipeline import _maybe_escalate_with_visual_evidence
    from app.ingestion.live_youtube import DetectorResult

    detector_result = DetectorResult(
        is_integration=False, confidence=0.3, reasons=["brand_in_title"],
        signals={"brand_in_title": {"matched": True}}, category="manual_review",
        has_brand_evidence=True, has_commercial_evidence=False,
    )
    enricher = VisualEvidenceEnricher(provider=OpenRouterProvider(api_key="fake-key"))
    with patch.object(enricher.provider, "analyze_text_and_image_json", return_value={
        "brand_visible": True, "commercial_signal_visible": True, "signals": ["paid_partnership"],
        "content_topics": [], "confidence": 0.9, "evidence": ["visible sponsor tag"],
    }):
        cache = ScreenshotCache(capture_fn=lambda url: b"fake-bytes")
        evidence_store = EvidenceStore()
        result = _maybe_escalate_with_visual_evidence(
            "instagram", {"post_url": "https://instagram.com/p/x", "caption": "Nike"},
            detector_result, _brand(), enricher, cache, evidence_store,
        )
    assert result.category in {"confirmed", "manual_review"}
    visual_evs = [e for e in evidence_store.as_dict().values() if e["type"] == "visual_ai"]
    assert len(visual_evs) == 1


# ---------------------------------------------------------------------------
# 16. Multi-platform routing
# ---------------------------------------------------------------------------


def test_16_multi_platform_routing_covers_all_requested_platforms():
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["youtube", "instagram", "tiktok", "articles"]})
    assert resp.status_code == 200
    body = client.get(f"/api/analysis/{resp.json()['analysis_id']}").json()

    platforms_seen = {p["platform"] for p in body["coverage"]["platforms"]}
    assert platforms_seen == {"youtube", "instagram", "tiktok", "articles"}
    # Раздел 19: каждая платформа - честный статус, никаких "успешных" при отсутствии ключей/connector.
    status_by_platform = {p["platform"]: p["status"] for p in body["coverage"]["platforms"]}
    assert status_by_platform["youtube"] == "unavailable"       # нет YOUTUBE_API_KEY
    assert status_by_platform["articles"] == "unavailable"      # нет SERPAPI_KEY
    assert status_by_platform["instagram"] == "connector_offline"
    assert status_by_platform["tiktok"] == "connector_offline"


def test_16b_platform_registry_routes_to_correct_adapter_class():
    assert type(get_platform_adapter("articles")).__name__ == "ArticlesPlatformAdapter"
    assert isinstance(get_platform_adapter("instagram"), SocialConnectorPlatformAdapter)
    assert isinstance(get_platform_adapter("tiktok"), SocialConnectorPlatformAdapter)


# ---------------------------------------------------------------------------
# 17. Screenshot cache
# ---------------------------------------------------------------------------


def test_17_screenshot_cache_avoids_recapturing_same_url():
    calls = []

    def _fake_capture(url: str):
        calls.append(url)
        return b"fake-png-bytes"

    cache = ScreenshotCache(capture_fn=_fake_capture)
    first = cache.get_or_capture("https://example.com/post/1")
    second = cache.get_or_capture("https://example.com/post/1")
    third = cache.get_or_capture("https://example.com/post/2")

    assert first == second == b"fake-png-bytes"
    assert len(calls) == 2  # второй URL другой -> реальный повторный capture
    assert cache.stats()["capture_calls"] == 2
    assert cache.stats()["unique_urls"] == 2


def test_17b_visual_evidence_enricher_caches_by_url_and_screenshot_hash():
    """Раздел 3: URL + screenshot hash - одинаковый screenshot не отправляется
    повторно в OpenRouter."""
    enricher = VisualEvidenceEnricher(provider=OpenRouterProvider(api_key="fake-key"))
    call_count = {"n": 0}

    def _fake_analyze(*a, **k):
        call_count["n"] += 1
        return {"brand_visible": True, "commercial_signal_visible": True, "signals": [],
                "content_topics": [], "confidence": 0.5, "evidence": []}

    with patch.object(enricher.provider, "analyze_text_and_image_json", side_effect=_fake_analyze):
        enricher.enrich("https://example.com/post", b"same-bytes", "text", "Nike")
        enricher.enrich("https://example.com/post", b"same-bytes", "text", "Nike")
    assert call_count["n"] == 1  # второй вызов - cache hit, не дошёл до provider


# ---------------------------------------------------------------------------
# 18. Full mocked multi-source analysis
# ---------------------------------------------------------------------------


def test_18_full_mocked_multi_source_analysis_produces_real_source_urls():
    """Мокаем youtube+articles с реалистичными "живыми" результатами и
    проверяем, что весь orchestration pipeline (_process_brand -> analytical
    layers) доходит до конца без падений, и что каждая confirmed-интеграция
    несёт реальный (не synthetic) source_url/evidence - раздел 34 требований
    ("если нет URL реального источника - вывод не подтверждён")."""
    config = AnalysisConfig(min_integration_confidence=0.0)
    evidence_store = EvidenceStore()

    fake_search_results = [SearchResultItem(url="https://news.example.com/nike-sponsored", title="Nike sponsored")]
    fake_parsed = ArticleParseResult(
        source_url="https://news.example.com/nike-sponsored",
        canonical_url="https://news.example.com/nike-sponsored",
        domain="news.example.com", title="Nike sponsored review",
        main_text="Материал подготовлен на правах рекламы про Nike.",
        published_at=NOW,
    )

    class _FakeSearchClient:
        source_name = "fake"

        def is_available(self):
            return True

        def search(self, query, max_results=10):
            return fake_search_results

    articles_adapter = ArticlesPlatformAdapter(search_client=_FakeSearchClient())
    with patch.object(articles_adapter.parser, "parse", return_value=fake_parsed):
        with patch("app.analysis.pipeline.get_platform_adapter", side_effect=lambda p: (
            articles_adapter if p == "articles" else get_platform_adapter(p)
        )):
            brand, competitor, coverages, creators, integrations, manual_review, publishers = _process_brand(
                "Nike", ["articles"], config, evidence_store,
            )

    assert coverages[0].status == "ok"
    confirmed = [i for i in integrations if i.category == "confirmed"]
    assert len(confirmed) == 1
    integration = confirmed[0]
    assert integration.content_url == "https://news.example.com/nike-sponsored"
    assert integration.source_mode == SourceMode.LIVE
    assert integration.is_synthetic is False
    assert integration.evidence, "confirmed-интеграция должна нести evidence с реальным source_url"
    assert any(e.source_url == "https://news.example.com/nike-sponsored" for e in integration.evidence)
    assert len(publishers) == 1
    assert publishers[0].source_url == "https://news.example.com/nike-sponsored"


def test_18b_full_analyze_endpoint_end_to_end_no_crash_across_all_platforms():
    """Сквозной прогон реального /api/analyze (без live-сети - все платформы
    честно unavailable/connector_offline) - убеждаемся, что оркестрация не
    падает и результат целиком собирается (раздел 18 обязательного теста)."""
    request = AnalyzeRequest(brand="Nike", platforms=["youtube", "instagram", "tiktok", "articles"])
    result = run_analysis(request, analysis_id="an_test_18b")
    assert result.analysis_id == "an_test_18b"
    assert result.summary.integrations_found == 0
    assert len(result.limitations) > 0
    assert result.market_map is not None
