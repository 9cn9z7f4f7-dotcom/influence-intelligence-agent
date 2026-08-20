from __future__ import annotations

from fastapi.testclient import TestClient

from app.api.server import app, get_result


def test_all_endpoints_return_200():
    client = TestClient(app)
    for path in ["/api/overview", "/api/market-map", "/api/competitor-dna",
                 "/api/next-moves", "/api/white-space", "/api/our-move", "/api/health"]:
        resp = client.get(path)
        assert resp.status_code == 200, f"{path} вернул {resp.status_code}"


def test_evidence_endpoint_resolves_known_id():
    client = TestClient(app)
    result = get_result()
    evidence_ids = list(result["evidence"].keys())
    assert evidence_ids, "должны быть evidence-записи"
    resp = client.get(f"/api/evidence/{evidence_ids[0]}")
    assert resp.status_code == 200
    body = resp.json()
    assert "type" in body and "field" in body


def test_evidence_endpoint_404_for_unknown_id():
    client = TestClient(app)
    resp = client.get("/api/evidence/does_not_exist")
    assert resp.status_code == 404


def test_pipeline_run_endpoint_refreshes_overview():
    client = TestClient(app)
    resp = client.post("/api/pipeline/run")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_ui_is_served_at_root():
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Influence Intelligence Agent" in resp.text or "<!doctype html>" in resp.text.lower()
