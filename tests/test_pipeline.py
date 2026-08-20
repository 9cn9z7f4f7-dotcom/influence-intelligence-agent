from __future__ import annotations

from app.pipeline import run_pipeline
from config.settings import OUTPUT_DIR


def test_demo_pipeline_runs_end_to_end():
    result = run_pipeline(mode="demo", persist=True)

    assert result["overview"]["integrations_analyzed"] >= 150
    assert result["overview"]["creators_analyzed"] >= 80
    assert result["overview"]["competitors_analyzed"] >= 4
    assert result["overview"]["is_synthetic_data"] is True

    assert result["market_map"]["competitors"]
    assert result["competitor_dna"]
    assert result["next_moves"]
    assert result["white_space"]["segments"]
    assert 3 <= len(result["our_move"]["opportunities"]) <= 5


def test_demo_pipeline_is_deterministic_in_composition():
    """Демо не должна работать по-разному от запуска к запуску по составу источников
    (сами данные в SQLite пересчитываются, но dataset фиксирован)."""
    r1 = run_pipeline(mode="demo", persist=False)
    r2 = run_pipeline(mode="demo", persist=False)
    assert r1["overview"]["creators_analyzed"] == r2["overview"]["creators_analyzed"]
    assert r1["overview"]["integrations_analyzed"] == r2["overview"]["integrations_analyzed"]


def test_pipeline_persists_output_files():
    run_pipeline(mode="demo", persist=True)
    for filename in ["market_map.json", "competitor_dna.json", "next_moves.json",
                      "white_space.json", "our_move.json", "overview.json", "evidence.json", "health.json"]:
        assert (OUTPUT_DIR / filename).exists(), f"{filename} должен быть сохранён"


def test_pipeline_reports_degraded_sources_without_crashing():
    result = run_pipeline(mode="demo", persist=False)
    health = {h["source"]: h["status"] for h in result["health"]}
    # Telegram/Instagram всегда честно degraded/unavailable в этом MVP.
    assert health.get("telegram") == "degraded"
    assert health.get("instagram") == "unavailable"
    # Но pipeline не падает и продолжает отдавать полный результат.
    assert result["overview"]["integrations_analyzed"] > 0
