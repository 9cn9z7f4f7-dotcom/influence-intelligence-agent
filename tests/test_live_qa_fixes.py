from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.analysis.store as analysis_store
import app.api.server as server
from app.analysis.models import (
    AnalysisCoverage,
    AnalysisResult,
    AnalysisSummary,
    PlatformCoverage,
    ResolvedBrand,
)
from app.evidence import fact


@pytest.fixture
def real_analysis_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Изолированный real-analysis flow без сети и legacy demo pipeline."""
    output_dir = tmp_path / "analyses"
    monkeypatch.setattr(analysis_store, "ANALYSIS_OUTPUT_DIR", output_dir)
    analysis_store._store.clear()
    analysis_store._evidence_store.clear()

    legacy_stub = {
        "overview": {}, "market_map": {}, "competitor_dna": [], "next_moves": [],
        "white_space": {"segments": []}, "our_move": {"opportunities": []},
        "evidence": {}, "health": [],
    }
    monkeypatch.setattr(server, "get_result", lambda force_refresh=False: legacy_stub)

    evidence = fact(
        field="article_signal:sponsor_wording",
        value=True,
        source_url="https://example.com/nike-sponsored",
        observed_at=None,
        raw_fragment="Sponsored by Nike",
    )

    def fake_run_analysis_with_evidence(request, analysis_id: str):
        result = AnalysisResult(
            analysis_id=analysis_id,
            created_at="2026-08-21T10:00:00+00:00",
            brand=ResolvedBrand(
                brand_name=request.brand,
                canonical_name=request.brand,
                input_type="name",
            ),
            platforms=request.platforms,
            settings=request.settings,
            coverage=AnalysisCoverage(
                sources=["articles"],
                live_sources=["articles"],
                platforms=[PlatformCoverage(
                    platform="articles",
                    source_mode="live",
                    status="ok",
                    items_collected=19,
                    confirmed_integrations=0,
                    search_provider="tavily",
                )],
            ),
            summary=AnalysisSummary(),
            market_map={},
            competitor_dna=[],
            next_move=[],
            white_space={"segments": []},
            our_move={"opportunities": []},
            limitations=[],
        )
        return result, {evidence.evidence_id: evidence.model_dump(mode="json")}

    monkeypatch.setattr(server, "run_analysis_with_evidence", fake_run_analysis_with_evidence)
    yield output_dir, evidence

    analysis_store._store.clear()
    analysis_store._evidence_store.clear()


def test_real_analyze_persists_analysis_scoped_evidence(real_analysis_api):
    output_dir, evidence = real_analysis_api
    with TestClient(server.app) as client:
        response = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["articles"]})

    assert response.status_code == 200
    analysis_id = response.json()["analysis_id"]
    evidence_path = output_dir / f"{analysis_id}.evidence.json"
    assert evidence_path.exists()
    persisted = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert persisted[evidence.evidence_id]["source_url"] == "https://example.com/nike-sponsored"


def test_analysis_scoped_evidence_endpoint_survives_cache_clear(real_analysis_api):
    _output_dir, evidence = real_analysis_api
    with TestClient(server.app) as client:
        create_response = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["articles"]})
        analysis_id = create_response.json()["analysis_id"]

        # Симулируем restart worker-а: endpoint обязан лениво поднять result и
        # evidence из persisted analysis files, а не из legacy demo cache.
        analysis_store._store.clear()
        analysis_store._evidence_store.clear()

        response = client.get(f"/api/analysis/{analysis_id}/evidence/{evidence.evidence_id}")

    assert response.status_code == 200
    assert response.json()["evidence_id"] == evidence.evidence_id
    assert response.json()["type"] == "fact"


def test_analysis_scoped_evidence_unknown_evidence_id_returns_404(real_analysis_api):
    with TestClient(server.app) as client:
        create_response = client.post("/api/analyze", json={"brand": "Nike", "platforms": ["articles"]})
        analysis_id = create_response.json()["analysis_id"]
        response = client.get(f"/api/analysis/{analysis_id}/evidence/ev_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "evidence_id не найден"


def test_analysis_scoped_evidence_unknown_analysis_id_returns_404(real_analysis_api):
    with TestClient(server.app) as client:
        response = client.get("/api/analysis/an_missing/evidence/ev_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "analysis_id не найден"


def test_new_frontend_uses_only_analysis_scoped_evidence_endpoint():
    source = Path("static/analyze.js").read_text(encoding="utf-8")
    assert "/api/analysis/${encodeURIComponent(analysisId)}/evidence/${encodeURIComponent(id)}" in source
    assert "/api/evidence/" not in source


def test_articles_ui_uses_real_candidate_confirmed_and_provider_fields():
    source = Path("static/analyze.js").read_text(encoding="utf-8")
    assert "Найдено материалов: <strong>${found}</strong>" in source
    assert "Реальных статей после фильтра: <strong>${checked}</strong>" in source
    assert "Подтверждено интеграций: <strong>${confirmed}</strong>" in source
    assert "row.items_collected" in source
    assert "row.items_checked" in source
    assert "row.confirmed_integrations" in source
    assert "row.organic_mentions" in source
    assert "row.potential_creators" in source
    assert "row.search_provider" in source
    assert "Источник поиска: ${escapeHtml(providerLabel)}" in source


def test_articles_ui_explains_candidates_without_confirmed_integrations():
    source = Path("static/analyze.js").read_text(encoding="utf-8")
    assert "found > 0 && confirmed === 0" in source
    assert (
        "Найдено ${found} материалов${throughProvider}, после проверки "
        "подтверждённых рекламных интеграций не найдено."
    ) in source
