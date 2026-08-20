"""
Утилиты для evidence-системы.

Каждый нетривиальный вывод (COMPUTED или AI_INFERENCE) должен ссылаться
на evidence_id. Этот модуль даёт единый способ их создавать и позже
разрешать (resolve) в человекочитаемый вид для UI ("Why / Evidence").
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from app.models import Evidence, EvidenceType


def make_evidence_id(*parts: str) -> str:
    raw = "|".join(str(p) for p in parts)
    return "ev_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def fact(field: str, value: Any, source_url: str | None, observed_at: datetime | None,
         raw_fragment: str | None = None) -> Evidence:
    return Evidence(
        evidence_id=make_evidence_id("fact", field, str(value), source_url or ""),
        source_url=source_url,
        observed_at=observed_at,
        type=EvidenceType.FACT,
        field=field,
        value=value,
        confidence=1.0,
        raw_fragment=raw_fragment,
    )


def computed(field: str, value: Any, supporting_note: str = "") -> Evidence:
    return Evidence(
        evidence_id=make_evidence_id("computed", field, str(value), supporting_note),
        source_url=None,
        observed_at=datetime.now(timezone.utc),
        type=EvidenceType.COMPUTED,
        field=field,
        value=value,
        confidence=1.0,
        raw_fragment=supporting_note or None,
    )


def ai_inference(field: str, value: Any, confidence: float, raw_fragment: str | None = None) -> Evidence:
    return Evidence(
        evidence_id=make_evidence_id("ai_inference", field, str(value)),
        source_url=None,
        observed_at=datetime.now(timezone.utc),
        type=EvidenceType.AI_INFERENCE,
        field=field,
        value=value,
        confidence=confidence,
        raw_fragment=raw_fragment,
    )


class EvidenceStore:
    """Держит все evidence-объекты сгенерированные за один прогон pipeline,
    чтобы UI мог резолвить evidence_id -> полный объект по кнопке Why/Evidence."""

    def __init__(self) -> None:
        self._items: dict[str, Evidence] = {}

    def add(self, ev: Evidence) -> str:
        self._items[ev.evidence_id] = ev
        return ev.evidence_id

    def add_many(self, evs: list[Evidence]) -> list[str]:
        return [self.add(e) for e in evs]

    def resolve(self, evidence_id: str) -> Optional[Evidence]:
        return self._items.get(evidence_id)

    def as_dict(self) -> dict[str, dict]:
        return {k: v.model_dump(mode="json") for k, v in self._items.items()}
