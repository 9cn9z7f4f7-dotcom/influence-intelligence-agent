from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import app


def test_analyze_returns_analysis_id_and_get_resolves_it():
    """POST /api/analyze -> analysis_id, GET /api/analysis/{id} -> полная схема
    (раздел 12 требований) - без сети/API key pipeline честно degrade-ится,
    но НЕ падает и НЕ возвращает 500."""
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Автор24", "platforms": ["youtube"]})
    assert resp.status_code == 200
    analysis_id = resp.json()["analysis_id"]
    assert analysis_id.startswith("an_")

    resp2 = client.get(f"/api/analysis/{analysis_id}")
    assert resp2.status_code == 200
    body = resp2.json()

    for key in ("brand", "platforms", "settings", "coverage", "summary",
                "market_map", "competitor_dna", "next_move", "white_space", "our_move", "limitations"):
        assert key in body, f"отсутствует обязательное поле {key}"

    assert body["coverage"]["platforms"][0]["platform"] == "youtube"
    assert body["coverage"]["platforms"][0]["status"] == "unavailable"  # нет YOUTUBE_API_KEY в тестовой среде
    assert body["limitations"]  # честно объясняет почему


def test_analyze_unknown_analysis_id_returns_404():
    client = TestClient(app)
    resp = client.get("/api/analysis/an_does_not_exist")
    assert resp.status_code == 404


def test_analyze_requires_at_least_one_platform():
    client = TestClient(app)
    resp = client.post("/api/analyze", json={"brand": "Автор24", "platforms": []})
    assert resp.status_code == 422  # pydantic validation error


def test_analyze_accepts_brand_url_and_multiple_platforms():
    client = TestClient(app)
    resp = client.post("/api/analyze", json={
        "brand": "https://www.instagram.com/avtor24/",
        "platforms": ["instagram", "tiktok"],
        "settings": {"date_range": "30d", "confirmed_only": True},
    })
    assert resp.status_code == 200
    analysis_id = resp.json()["analysis_id"]

    body = client.get(f"/api/analysis/{analysis_id}").json()
    assert body["brand"]["input_type"] == "url"
    assert body["brand"]["detected_platform"] == "instagram"
    assert set(body["platforms"]) == {"instagram", "tiktok"}
    # Real-data update: без зарегистрированного local connector Instagram/TikTok
    # честно "connector_offline" (не "unavailable") - раздел 19 требований.
    for platform_cov in body["coverage"]["platforms"]:
        assert platform_cov["status"] == "connector_offline"
