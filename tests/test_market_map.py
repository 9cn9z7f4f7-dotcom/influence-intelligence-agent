from __future__ import annotations

from app.analytics.market_map import MarketMapBuilder


def test_market_map_competitor_aggregates(sample_creators, sample_competitors, sample_integrations, settings):
    builder = MarketMapBuilder(sample_creators, sample_competitors, sample_integrations, settings)
    result = builder.build()

    comp1_stats = next(c for c in result["competitors"] if c["competitor_id"] == "comp1")
    assert comp1_stats["total_integrations"] == 3
    assert comp1_stats["unique_creators"] == 2
    # c1 использован дважды -> repeat_creator_rate = 1/2
    assert comp1_stats["repeat_creator_rate"] == 0.5
    assert comp1_stats["platform_distribution"] == {"youtube": 3}
    assert "evidence_ids" in comp1_stats and len(comp1_stats["evidence_ids"]) == 1


def test_market_map_segment_saturation_present(sample_creators, sample_competitors, sample_integrations, settings):
    builder = MarketMapBuilder(sample_creators, sample_competitors, sample_integrations, settings)
    result = builder.build()
    sat = result["market"]["segment_saturation"]
    assert len(sat) > 0
    for seg in sat:
        assert 0 <= seg["saturation_score"] <= 100
        assert seg["evidence_ids"]


def test_market_map_handles_empty_integrations(sample_creators, sample_competitors, settings):
    builder = MarketMapBuilder(sample_creators, sample_competitors, [], settings)
    result = builder.build()
    for c in result["competitors"]:
        assert c["total_integrations"] == 0
        assert c["repeat_creator_rate"] == 0.0
