from __future__ import annotations

import json

from app.models import Creator, Evidence, EvidenceType, Integration


def test_creator_allows_null_numeric_fields():
    c = Creator(creator_id="c1", name="X", platform="youtube")
    assert c.followers is None
    assert c.avg_views is None
    assert c.engagement_rate is None


def test_creator_json_roundtrip():
    c = Creator(creator_id="c1", name="X", platform="youtube", followers=1000, topic_tags=["fitness"])
    raw = c.model_dump_json()
    restored = Creator.model_validate_json(raw)
    assert restored == c
    assert json.loads(raw)["followers"] == 1000


def test_evidence_confidence_clamped():
    ev = Evidence(evidence_id="e1", type=EvidenceType.AI_INFERENCE, field="x", value=1, confidence=1.5)
    assert ev.confidence == 1.0
    ev2 = Evidence(evidence_id="e2", type=EvidenceType.AI_INFERENCE, field="x", value=1, confidence=-0.5)
    assert ev2.confidence == 0.0


def test_integration_defaults_to_empty_evidence():
    i = Integration(integration_id="i1", competitor_id="comp1", creator_id="c1", platform="youtube")
    assert i.evidence == []
    assert i.is_synthetic is False
