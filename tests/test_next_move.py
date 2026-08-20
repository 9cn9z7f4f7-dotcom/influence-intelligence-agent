from __future__ import annotations

from app.analytics.next_move import NextMoveBuilder


def test_excludes_already_used_creators(sample_creators, sample_integrations, sample_competitors, settings):
    builder = NextMoveBuilder(sample_creators, sample_integrations, settings)
    comp1 = next(c for c in sample_competitors if c.competitor_id == "comp1")
    result = builder.build_for_competitor(comp1)
    candidate_ids = {c["creator_id"] for c in result["candidates"]}
    assert "c1" not in candidate_ids  # comp1 уже использовал c1
    assert "c2" not in candidate_ids  # и c2


def test_similarity_score_is_deterministic(sample_creators, sample_integrations, sample_competitors, settings):
    builder1 = NextMoveBuilder(sample_creators, sample_integrations, settings)
    builder2 = NextMoveBuilder(sample_creators, sample_integrations, settings)
    comp1 = next(c for c in sample_competitors if c.competitor_id == "comp1")
    r1 = builder1.build_for_competitor(comp1)
    r2 = builder2.build_for_competitor(comp1)
    assert r1["candidates"] == r2["candidates"]


def test_similarity_score_within_bounds(sample_creators, sample_integrations, sample_competitors, settings):
    builder = NextMoveBuilder(sample_creators, sample_integrations, settings)
    comp1 = next(c for c in sample_competitors if c.competitor_id == "comp1")
    result = builder.build_for_competitor(comp1)
    for cand in result["candidates"]:
        assert 0 <= cand["similarity_score"] <= 100
        assert cand["evidence_ids"]


def test_no_integrations_returns_insufficient_data(sample_creators, sample_competitors, settings):
    builder = NextMoveBuilder(sample_creators, [], settings)
    comp1 = next(c for c in sample_competitors if c.competitor_id == "comp1")
    result = builder.build_for_competitor(comp1)
    assert result["candidates"] == []
    assert result["insufficient_data"]
