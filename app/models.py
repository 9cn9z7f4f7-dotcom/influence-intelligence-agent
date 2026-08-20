"""
Единая модель данных проекта.

Все числовые поля могут быть None (недостающие данные - это нормально,
а не повод додумывать).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceType(str, Enum):
    FACT = "fact"
    COMPUTED = "computed"
    AI_INFERENCE = "ai_inference"


class SourceStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class SourceMode(str, Enum):
    """Явная маркировка происхождения объекта - никогда не смешивать demo и
    live/imported без разметки (раздел I требования по live ingestion)."""

    DEMO = "demo"
    LIVE = "live"
    IMPORTED = "imported"


class Evidence(BaseModel):
    evidence_id: str
    source_url: Optional[str] = None
    observed_at: Optional[datetime] = None
    type: EvidenceType
    field: str
    value: Any = None
    confidence: Optional[float] = None
    raw_fragment: Optional[str] = None

    @field_validator("confidence")
    @classmethod
    def _clamp_confidence(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        return max(0.0, min(1.0, v))


class Creator(BaseModel):
    creator_id: str
    name: str
    canonical_url: Optional[str] = None
    platform: str
    followers: Optional[int] = None
    avg_views: Optional[float] = None
    median_views: Optional[float] = None
    engagement_rate: Optional[float] = None
    topic_tags: list[str] = Field(default_factory=list)
    audience_tags: list[str] = Field(default_factory=list)
    geo: Optional[str] = None
    language: Optional[str] = None
    created_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    source_refs: list[str] = Field(default_factory=list)
    is_synthetic: bool = False  # явная маркировка demo/synthetic данных
    source_mode: SourceMode = SourceMode.DEMO  # demo | live | imported


class Competitor(BaseModel):
    competitor_id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    source_mode: SourceMode = SourceMode.DEMO  # demo | live | imported


class Integration(BaseModel):
    integration_id: str
    competitor_id: str
    creator_id: str
    platform: str
    content_url: Optional[str] = None
    published_at: Optional[datetime] = None
    content_type: Optional[str] = None
    detected_offer: Optional[str] = None
    detected_cta: Optional[str] = None
    detected_mechanic: Optional[str] = None
    campaign_tags: list[str] = Field(default_factory=list)
    raw_text: Optional[str] = None
    evidence: list[Evidence] = Field(default_factory=list)
    is_synthetic: bool = False
    source_mode: SourceMode = SourceMode.DEMO  # demo | live | imported
    confidence: Optional[float] = None  # общий confidence детектора интеграции (live/imported)
    ingestion_source: Optional[str] = None  # напр. "demo_dataset" | "youtube_api_v3" | "csv_import"
    # confirmed (brand+commercial evidence) | manual_review | organic_mention (brand только) | rejected
    # По умолчанию "confirmed" - для обратной совместимости с demo/import данными,
    # которые всегда были детерминированно подтверждены (раздел 10 требований).
    category: str = "confirmed"

    @field_validator("confidence")
    @classmethod
    def _clamp_integration_confidence(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        return max(0.0, min(1.0, v))


class CreatorSegment(BaseModel):
    """Сегмент = platform x topic x followers_bucket x views_bucket x geo x content_type."""

    platform: Optional[str] = None
    topic: Optional[str] = None
    followers_bucket: Optional[str] = None
    views_bucket: Optional[str] = None
    geo: Optional[str] = None
    content_type: Optional[str] = None

    def key(self) -> str:
        return "|".join(
            [
                self.platform or "-",
                self.topic or "-",
                self.followers_bucket or "-",
                self.views_bucket or "-",
                self.geo or "-",
                self.content_type or "-",
            ]
        )

    def label(self) -> str:
        parts = [p for p in [self.topic, self.platform, self.followers_bucket] if p]
        return " / ".join(parts) if parts else self.key()


class OurProfile(BaseModel):
    preferred_topics: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    creator_size: list[str] = Field(default_factory=list)  # nano/micro/mid/macro
    geo: list[str] = Field(default_factory=list)
    minimum_views: Optional[float] = None
    excluded_topics: list[str] = Field(default_factory=list)


class SourceHealth(BaseModel):
    source: str
    status: SourceStatus
    detail: Optional[str] = None
    last_checked_at: Optional[datetime] = None
