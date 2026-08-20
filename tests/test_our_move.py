from __future__ import annotations

from app.analytics.our_move import OurMoveBuilder
from app.models import OurProfile


def _fake_market_map():
    return {"market": {"segment_saturation": [
        {"segment_key": "s1", "label": "fitness / youtube / mid", "available_creators": 10,
         "competitor_integrations": 30, "unique_competitors": 4, "saturation_score": 95.0,
         "evidence_ids": ["ev_sat_1"]},
    ]}}


def _fake_dna():
    return [{
        "competitor": "Comp One",
        "observed_patterns": [],
        "recent_shifts": [{
            "dimension": "platform", "key": "telegram", "recent_share": 0.9, "historical_share": 0.1,
            "delta": 0.8, "statement": "За последние 30 наблюдений доля 'telegram' выросла с 10.0% до 90.0%.",
            "evidence_ids": ["ev_shift_1"],
        }],
        "insufficient_data": [],
    }]


def _fake_next_moves():
    return [{
        "competitor": "Comp One",
        "candidates": [
            {"candidate": "Creator A", "creator_id": "cA", "platform": "telegram", "topic": "medical_students",
             "followers_bucket": "nano", "similarity_score": 90, "why": [], "evidence_ids": ["ev_nm_1"]},
            {"candidate": "Creator B", "creator_id": "cB", "platform": "youtube", "topic": "fitness",
             "followers_bucket": "mid", "similarity_score": 85, "why": [], "evidence_ids": ["ev_nm_2"]},
        ],
    }]


def _fake_white_space():
    return {"segments": [
        {"segment": {"topic": "medical_students", "platform": "telegram", "followers_bucket": "nano",
                      "label": "medical_students / telegram / nano"},
         "available_creators": 30, "competitor_integrations": 2, "unique_competitors": 1,
         "our_relevance": 90.0, "our_relevance_notes": "topic match", "saturation_score": 15.0,
         "opportunity_score": 88.0,
         "top_creators": [{"creator_id": "cA", "name": "Creator A", "engagement_rate": 0.1,
                             "already_used_by_competitor": False}],
         "evidence_ids": ["ev_ws_1"]},
    ]}


def test_our_move_count_within_bounds(settings):
    our_profile = OurProfile(preferred_topics=["medical_students"], excluded_topics=["fitness"])
    builder = OurMoveBuilder(settings, our_profile)
    result = builder.build(_fake_market_map(), _fake_dna(), _fake_next_moves(), _fake_white_space())
    assert settings.our_move_min_items <= len(result["opportunities"]) <= settings.our_move_max_items


def test_our_move_only_references_evidence_from_prior_layers(settings):
    our_profile = OurProfile(preferred_topics=["medical_students"], excluded_topics=["fitness"])
    builder = OurMoveBuilder(settings, our_profile)
    result = builder.build(_fake_market_map(), _fake_dna(), _fake_next_moves(), _fake_white_space())
    all_known_evidence = {"ev_sat_1", "ev_shift_1", "ev_nm_1", "ev_nm_2", "ev_ws_1"}
    for op in result["opportunities"]:
        for ev_id in op["evidence"]:
            assert ev_id in all_known_evidence, f"Our Move ссылается на неизвестный evidence_id {ev_id}"


def test_our_move_excludes_topics_we_dont_want(settings):
    our_profile = OurProfile(preferred_topics=["medical_students"], excluded_topics=["fitness"])
    builder = OurMoveBuilder(settings, our_profile)
    result = builder.build(_fake_market_map(), _fake_dna(), _fake_next_moves(), _fake_white_space())
    titles = " ".join(op["title"] for op in result["opportunities"])
    assert "Creator B" not in titles  # fitness-креатор исключён из наших гипотез


def test_our_move_low_confidence_uses_cautious_wording(settings):
    our_profile = OurProfile()
    builder = OurMoveBuilder(settings, our_profile)
    weak_white_space = _fake_white_space()
    weak_white_space["segments"][0]["opportunity_score"] = 20.0
    result = builder.build(_fake_market_map(), _fake_dna(), _fake_next_moves(), weak_white_space)
    ws_op = next((op for op in result["opportunities"] if "White Space" in op["title"]), None)
    if ws_op and ws_op["confidence"] < settings.low_confidence_threshold:
        assert "стоит исследовать" in ws_op["why_now"]
        assert "нужно делать" not in ws_op["why_now"]
