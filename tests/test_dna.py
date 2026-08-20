from __future__ import annotations

from app.analytics.competitor_dna import CompetitorDnaBuilder


def test_dna_fallback_without_llm(monkeypatch, sample_creators, sample_integrations, sample_competitors, settings):
    # Форсируем отсутствие LLM, чтобы проверить "computed patterns без красивого текста".
    monkeypatch.setattr("app.analytics.competitor_dna.generate_patterns", lambda *a, **k: None)

    builder = CompetitorDnaBuilder(sample_creators, sample_integrations, settings)
    comp1 = next(c for c in sample_competitors if c.competitor_id == "comp1")
    result = builder.build(comp1)

    assert result["observed_patterns"], "должны быть паттерны даже без LLM"
    for pattern in result["observed_patterns"]:
        assert pattern["type"] == "computed"
        assert 0.0 <= pattern["confidence"] <= 1.0
        assert pattern["evidence_ids"]


def test_dna_insufficient_data_flags(monkeypatch, sample_creators, sample_integrations, sample_competitors, settings):
    monkeypatch.setattr("app.analytics.competitor_dna.generate_patterns", lambda *a, **k: None)
    builder = CompetitorDnaBuilder(sample_creators, sample_integrations, settings)
    comp2 = next(c for c in sample_competitors if c.competitor_id == "comp2")
    result = builder.build(comp2)

    assert "low_sample_size_overall" in result["insufficient_data"]
    assert "no_historical_window_data" in result["insufficient_data"]


def test_dna_no_integrations_returns_insufficient_data(sample_creators, sample_competitors, settings):
    builder = CompetitorDnaBuilder(sample_creators, [], settings)
    comp1 = next(c for c in sample_competitors if c.competitor_id == "comp1")
    result = builder.build(comp1)
    assert result["observed_patterns"] == []
    assert "no_integrations_observed" in result["insufficient_data"]


def test_dna_low_confidence_below_min_observations(monkeypatch, sample_creators, sample_integrations,
                                                      sample_competitors, settings):
    monkeypatch.setattr("app.analytics.competitor_dna.generate_patterns", lambda *a, **k: None)
    builder = CompetitorDnaBuilder(sample_creators, sample_integrations, settings)
    comp2 = next(c for c in sample_competitors if c.competitor_id == "comp2")
    result = builder.build(comp2)
    # comp2 has a single integration - каждый паттерн подтверждён только 1 наблюдением (< min 2)
    for pattern in result["observed_patterns"]:
        if pattern["supporting_metrics"][0]["supporting_observations"] < settings.min_hypothesis_observations:
            assert pattern["confidence"] < 1.0
