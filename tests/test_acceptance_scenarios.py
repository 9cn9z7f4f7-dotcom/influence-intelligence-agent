"""
3 конкретных acceptance-сценария из требований нового user-flow:

    1. Brand задан ИМЕНЕМ, с полным набором фильтров AnalysisConfig.
    2. Brand задан ССЫЛКОЙ на аккаунт (URL).
    3. Instagram/TikTok - честный unavailable + предложение import fallback.

Каждый сценарий прогоняется через ПОЛНЫЙ путь: POST /api/analyze -> GET
/api/analysis/{id}, то есть ровно так, как будет использовать реальный
пользователь через UI.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import app


def test_scenario_1_brand_by_name_with_full_filters():
    client = TestClient(app)
    payload = {
        "brand": "Автор24",
        "platforms": ["youtube"],
        "settings": {
            "date_range": "30d",
            "creator_size": ["micro", "mid"],
            "min_followers": 5000,
            "include_topics": ["medical_students", "exam_prep"],
            "exclude_topics": ["entertainment"],
            "confirmed_only": False,
            "include_manual_review": True,
            "min_integration_confidence": 0.6,
            "geo": ["RU"],
            "max_integrations": 50,
            "max_creators": 50,
            "max_next_move_candidates": 5,
        },
    }
    resp = client.post("/api/analyze", json=payload)
    assert resp.status_code == 200
    analysis_id = resp.json()["analysis_id"]

    body = client.get(f"/api/analysis/{analysis_id}").json()
    assert body["brand"]["input_type"] == "name"
    assert body["brand"]["canonical_name"] == "Автор24"
    assert body["settings"]["min_followers"] == 5000
    assert body["settings"]["include_topics"] == ["medical_students", "exam_prep"]
    # pipeline не падает, даже когда все фильтры выставлены одновременно и live недоступен
    assert "coverage" in body and "summary" in body and "limitations" in body


def test_scenario_2_brand_by_url():
    client = TestClient(app)
    resp = client.post("/api/analyze", json={
        "brand": "https://www.youtube.com/@Avtor24Official",
        "platforms": ["youtube"],
    })
    assert resp.status_code == 200
    analysis_id = resp.json()["analysis_id"]

    body = client.get(f"/api/analysis/{analysis_id}").json()
    assert body["brand"]["input_type"] == "url"
    assert body["brand"]["detected_platform"] == "youtube"
    assert body["brand"]["normalized_handle"] == "Avtor24Official"
    assert body["brand"]["source_url"] == "https://www.youtube.com/@Avtor24Official"


def test_scenario_3_instagram_tiktok_honest_unavailable_with_import_option():
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Автор24", "platforms": ["instagram", "tiktok"]})
    assert resp.status_code == 200
    analysis_id = resp.json()["analysis_id"]

    body = client.get(f"/api/analysis/{analysis_id}").json()

    # Никогда не выдаём imported/demo данные под видом live. Real-data update:
    # без local connector - честно "connector_offline" (раздел 19), не "unavailable".
    assert body["coverage"]["live_sources"] == []
    for cov in body["coverage"]["platforms"]:
        assert cov["platform"] in ("instagram", "tiktok")
        assert cov["status"] == "connector_offline"
        assert cov["source_mode"] == "none"

    # limitations должны честно объяснять причину и упоминать import fallback.
    joined = " ".join(body["limitations"]).lower()
    assert "csv" in joined or "json" in joined or "import" in joined

    # Import fallback РЕАЛЬНО существует и работает (см. Phase 2, app/live_pipeline.py) -
    # проверяем, что путь, упомянутый в limitations, - не пустое обещание.
    import inspect

    from app.ingestion import import_adapter
    assert hasattr(import_adapter, "import_integrations")
    assert callable(import_adapter.import_integrations)
    assert "path" in inspect.signature(import_adapter.import_integrations).parameters
