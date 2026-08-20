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
    # Отдельный тип для vision-сигналов (раздел 2, 17, 21 real-data требований) -
    # визуально распознанные сигналы (logo/CTA/sponsor disclosure на screenshot) -
    # это AI_INFERENCE, но UI должен показывать их отдельным блоком ("VISUAL AI"),
    # а не мешать с текстовыми AI_INFERENCE (напр. LLM-паттернами Competitor DNA).
    VISUAL_AI = "visual_ai"


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
    # --- Articles/Web платформа (real-data update, раздел 5-9) -----------------
    # Только для platform="articles": детальная категория статьи (confirmed_sponsored |
    # affiliate | partner_content | editorial_review | organic_mention | manual_review |
    # rejected) - НЕ путать с общим `category` выше, который используется существующим
    # pipeline-фильтром (AnalysisConfig.allowed_integration_categories) и хранит только
    # 4 "грубых" значения для обратной совместимости со всеми платформами.
    article_category: Optional[str] = None
    # Только для platform="articles": ссылка на Publisher (см. класс Publisher ниже).
    # Publisher НЕ является Creator - раздел 8 требований запрещает искусственно
    # превращать publisher в creator, поэтому это отдельное поле, а не creator_id.
    publisher_id: Optional[str] = None

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


class PotentialCreatorSignal(BaseModel):
    """Раздел 2 доработки ("не выбрасывать creator, если бренд явно присутствует,
    но hard commercial signal отсутствует") - НЕ является Integration и никогда
    не увеличивает confirmed_integrations/integrations_found. Хранит только то,
    что реально наблюдалось: тип affinity-сигнала, конкретные найденные фразы,
    и ссылку на creator/источник, чтобы UI мог честно показать "Почему?"."""

    creator_id: Optional[str] = None
    creator_name: Optional[str] = None
    platform: str
    source_url: Optional[str] = None
    observed_at: Optional[datetime] = None
    potential_reason: str
    brand_affinity_signals: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class Publisher(BaseModel):
    """Издание/сайт, опубликовавший статью про бренд (раздел 8 требований).

    ВАЖНО: Publisher - это отдельная сущность, НЕ Creator. Article Integration
    может связывать brand -> publisher (+ опционально author, см.
    Integration.raw_text/evidence), но публикация никогда не превращается в
    Creator искусственно - у издания нет followers/engagement_rate/topic_tags
    в том смысле, в каком они есть у influencer-а."""

    publisher_id: str
    name: str
    domain: str
    platform: str = "web_article"
    source_url: Optional[str] = None
