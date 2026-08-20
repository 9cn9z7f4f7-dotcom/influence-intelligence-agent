"""Regression coverage for the post-live-QA fixes and result presentation.

The tests deliberately exercise the new /api/analyze flow, not the legacy
/demo evidence cache. Frontend assertions are source-level contract tests: they
protect the endpoint and the exact honest Articles funnel copy without pulling
browser-only dependencies into the pytest suite.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.analysis import store as analysis_store
from app.api.server import app
from app.analysis.pipeline import _build_findings
from app.models import Creator, Evidence, EvidenceType, Integration, Publisher, SourceMode

ROOT = Path(__file__).resolve().parents[1]
ANALYZE_JS = ROOT / "static" / "analyze.js"
INDEX_HTML = ROOT / "static" / "index.html"
ANALYZE_CSS = ROOT / "static" / "analyze.css"


@pytest.fixture(scope="module")
def persisted_real_analysis():
    """Create one ordinary real-flow analysis and clean its persisted file."""
    with TestClient(app) as client:
        response = client.post(
            "/api/analyze",
            json={"brand": "Evidence QA Brand", "platforms": ["instagram"]},
        )
        assert response.status_code == 200
        analysis_id = response.json()["analysis_id"]
        result = analysis_store.get_analysis(analysis_id)
        assert result is not None
        try:
            yield client, analysis_id, result
        finally:
            with analysis_store._lock:  # noqa: SLF001 - test cleanup only
                analysis_store._store.pop(analysis_id, None)  # noqa: SLF001
            (analysis_store.ANALYSIS_OUTPUT_DIR / f"{analysis_id}.json").unlink(missing_ok=True)


def test_real_analyze_persists_its_evidence(persisted_real_analysis):
    _client, analysis_id, result = persisted_real_analysis

    assert result.evidence, "new /api/analyze result must own a non-empty evidence map"
    persisted_path = analysis_store.ANALYSIS_OUTPUT_DIR / f"{analysis_id}.json"
    assert persisted_path.exists()
    persisted = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted["evidence"]
    assert set(result.evidence).issubset(persisted["evidence"])


def test_analysis_scoped_evidence_endpoint_returns_200(persisted_real_analysis):
    client, analysis_id, result = persisted_real_analysis
    evidence_id = next(iter(result.evidence))
    # Simulate a process-local cache miss: endpoint must reload the persisted
    # AnalysisResult (including evidence) from output/analyses.
    with analysis_store._lock:  # noqa: SLF001 - persistence regression test
        analysis_store._store.pop(analysis_id, None)  # noqa: SLF001

    response = client.get(f"/api/analysis/{analysis_id}/evidence/{evidence_id}")

    assert response.status_code == 200
    assert response.json()["evidence_id"] == evidence_id


def test_analysis_scoped_unknown_evidence_returns_404(persisted_real_analysis):
    client, analysis_id, _result = persisted_real_analysis

    response = client.get(f"/api/analysis/{analysis_id}/evidence/ev_does_not_exist")

    assert response.status_code == 404
    assert "evidence_id" in response.json()["detail"]


def test_analysis_scoped_unknown_analysis_returns_404():
    client = TestClient(app)

    response = client.get("/api/analysis/an_does_not_exist/evidence/ev_anything")

    assert response.status_code == 404
    assert "analysis_id" in response.json()["detail"]


def test_new_frontend_uses_only_analysis_scoped_evidence_endpoint():
    source = ANALYZE_JS.read_text(encoding="utf-8")

    assert "/api/analysis/${encodeURIComponent(analysisId)}/evidence/${encodeURIComponent(evidenceId)}" in source
    assert "/api/evidence/" not in source


def test_articles_funnel_renders_candidates_checked_and_confirmed_counts():
    source = ANALYZE_JS.read_text(encoding="utf-8")

    assert 'coverage.items_collected' in source
    assert 'coverage.items_checked' in source
    assert 'coverage.confirmed_integrations' in source
    assert '"Найдено кандидатов"' in source
    assert '"Проверено"' in source
    assert '"Подтверждено интеграций"' in source
    assert "coverage.searchProviders" in source
    assert "Источник поиска:" in source
    assert "const checked = coverage.items_checked;" in source
    assert 'if (potential > 0) stats.push([potential, "Потенциальные авторы"]);' in source
    assert 'if (organic > 0) stats.push([organic, "Органические упоминания"]);' in source


def test_real_source_links_require_absolute_http_urls():
    source = ANALYZE_JS.read_text(encoding="utf-8")

    assert "new URL(String(value))" in source
    assert "new URL(value, window.location.origin)" not in source


def test_articles_funnel_keeps_honest_zero_confirmed_copy():
    source = ANALYZE_JS.read_text(encoding="utf-8")

    assert "if (candidates > 0 && confirmed === 0)" in source
    assert (
        "но подтверждённых рекламных интеграций в выбранной выборке не найдено."
        in source
    )
    assert "через ${providerText}" in source


def test_real_results_ui_has_findings_table_and_right_side_drawer():
    html = INDEX_HTML.read_text(encoding="utf-8")
    source = ANALYZE_JS.read_text(encoding="utf-8")

    assert 'id="findings-table"' in html
    assert 'id="detail-drawer"' in html
    assert "finding.source_url" in source
    assert "Открыть источник ↗" in source
    assert "data-open-finding" in source


def test_findings_projection_keeps_real_source_urls_and_evidence_ids():
    observed_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    evidence = Evidence(
        evidence_id="ev_real_source",
        source_url="https://www.youtube.com/watch?v=real123",
        observed_at=observed_at,
        type=EvidenceType.FACT,
        field="live_signal:promo_code",
        value="RUN10",
        confidence=1.0,
    )
    creator = Creator(
        creator_id="creator_real",
        name="Real Runner",
        canonical_url="https://www.youtube.com/@realrunner",
        platform="youtube",
        followers=75_000,
        median_views=20_000,
        topic_tags=["running"],
        source_mode=SourceMode.LIVE,
    )
    integration = Integration(
        integration_id="int_real",
        competitor_id="comp_brand",
        creator_id=creator.creator_id,
        platform="youtube",
        content_url="https://www.youtube.com/watch?v=real123",
        published_at=observed_at,
        content_type="review",
        detected_offer="RUN10",
        raw_text="Тест кроссовок и промокод RUN10",
        evidence=[evidence],
        source_mode=SourceMode.LIVE,
        confidence=0.93,
        category="confirmed",
    )

    findings = _build_findings([integration], [creator], [], [])

    assert len(findings) == 1
    finding = findings[0]
    assert finding.source_url == integration.content_url
    assert finding.canonical_url == creator.canonical_url
    assert finding.evidence_ids == [evidence.evidence_id]
    assert finding.classification == "confirmed"
    assert finding.detected_signals


def test_article_finding_remains_a_publisher_not_a_creator():
    observed_at = datetime(2026, 8, 20, tzinfo=timezone.utc)
    publisher = Publisher(
        publisher_id="pub_real",
        name="Real News",
        domain="news.example.com",
        source_url="https://news.example.com/sponsored",
    )
    integration = Integration(
        integration_id="article_real",
        competitor_id="comp_brand",
        creator_id=publisher.publisher_id,
        publisher_id=publisher.publisher_id,
        platform="articles",
        content_url="https://news.example.com/sponsored",
        published_at=observed_at,
        content_type="article",
        raw_text="Партнёрский материал о бренде || Полный текст публикации",
        source_mode=SourceMode.LIVE,
        confidence=0.9,
        category="confirmed",
        article_category="confirmed_sponsored",
    )

    finding = _build_findings([integration], [], [publisher], [])[0]

    assert finding.entity_type == "publisher"
    assert finding.entity_name == "Real News"
    assert finding.content_title == "Партнёрский материал о бренде"
    assert finding.source_url == publisher.source_url


def test_mobile_results_layout_overrides_inline_flex_display():
    css = ANALYZE_CSS.read_text(encoding="utf-8")

    assert ".results-shell { display: block !important; }" in css
