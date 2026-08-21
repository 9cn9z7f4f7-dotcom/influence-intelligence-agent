"""
Модели для нового user-flow: Brand -> Platforms -> Advanced Settings -> Analyze.

AnalysisConfig собирает ВСЕ пользовательские настройки в одном месте - и,
что важно, они реально применяются в pipeline (app/analysis/pipeline.py),
а не просто хранятся для UI (раздел 11 требований).
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models import PotentialCreatorSignal

Platform = Literal["youtube", "instagram", "tiktok", "articles"]
CreatorSizeBucket = Literal["nano", "micro", "mid", "macro"]
DateRangePreset = Literal["7d", "30d", "90d", "custom"]
SearchLevel = Literal["light", "standard", "deep"]
IntegrationCategory = Literal["confirmed", "manual_review", "organic_mention", "potential_creator", "rejected"]


def _normalize_topic(topic: str) -> str:
    return topic.strip().lower().replace(" ", "_").replace("-", "_")


class AnalysisConfig(BaseModel):
    # --- Search depth --------------------------------------------------
    search_level: SearchLevel = "light"

    # --- Date range -----------------------------------------------------
    date_range: DateRangePreset = "90d"
    custom_start: Optional[date] = None
    custom_end: Optional[date] = None

    # --- Creator size -----------------------------------------------------
    creator_size: list[CreatorSizeBucket] = Field(default_factory=list)  # пусто = все размеры
    custom_min_followers: Optional[int] = None
    custom_max_followers: Optional[int] = None

    # --- Minimum metrics ----------------------------------------------------
    min_followers: Optional[int] = None
    max_followers: Optional[int] = None
    min_median_views: Optional[float] = None
    min_avg_views: Optional[float] = None
    min_engagement_rate: Optional[float] = None

    # --- Topics -----------------------------------------------------------
    include_topics: list[str] = Field(default_factory=list)
    exclude_topics: list[str] = Field(default_factory=list)

    # --- Content filters ----------------------------------------------------
    sponsored_only: bool = False
    include_organic: bool = True
    confirmed_only: bool = False
    include_manual_review: bool = False

    # --- Geography ----------------------------------------------------------
    geo: list[str] = Field(default_factory=list)
    language: list[str] = Field(default_factory=list)

    # --- Result limits --------------------------------------------------------
    max_integrations: int = 200
    max_creators: int = 200
    max_next_move_candidates: int = 20
    max_white_space_segments: int = 20
    max_results: Optional[int] = None  # generic overall cap, если задан клиентом

    # --- Confidence -----------------------------------------------------------
    min_integration_confidence: float = 0.5
    min_strategy_match: float = 0.0       # 0..100, шкала similarity_score
    min_white_space_opportunity: float = 0.0  # 0..100

    @field_validator("include_topics", "exclude_topics")
    @classmethod
    def _normalize_topics(cls, v: list[str]) -> list[str]:
        return [_normalize_topic(t) for t in v if t and t.strip()]

    @field_validator("geo", "language")
    @classmethod
    def _normalize_strings(cls, v: list[str]) -> list[str]:
        return [s.strip() for s in v if s and s.strip()]

    @model_validator(mode="after")
    def _validate_custom_range(self) -> "AnalysisConfig":
        if self.date_range == "custom" and (self.custom_start is None or self.custom_end is None):
            raise ValueError("date_range='custom' требует custom_start и custom_end")
        if self.custom_start and self.custom_end and self.custom_start > self.custom_end:
            raise ValueError("custom_start не может быть позже custom_end")
        if self.min_followers is not None and self.max_followers is not None and self.min_followers > self.max_followers:
            raise ValueError("min_followers не может быть больше max_followers")
        return self


    def sample_target(self) -> int:
        return {"light": 30, "standard": 60, "deep": 100}[self.search_level]

    def hunting_target(self) -> int:
        return {"light": 15, "standard": 25, "deep": 40}[self.search_level]

    def discovery_pool_target(self) -> int:
        # Candidate pool is intentionally larger than the visible sample so
        # filtering/dedup still leaves enough real findings.
        return {"light": 60, "standard": 120, "deep": 180}[self.search_level]

    def date_range_days(self) -> int:
        return {"7d": 7, "30d": 30, "90d": 90}.get(self.date_range, 90)

    def allowed_integration_categories(self) -> set[str]:
        """Какие категории интеграций пропускать в итоговую выборку (раздел 11)."""
        if self.confirmed_only:
            return {"confirmed"}
        allowed = {"confirmed"}
        if self.include_manual_review:
            allowed.add("manual_review")
        if self.include_organic and not self.sponsored_only:
            allowed.add("organic_mention")
        return allowed

    def matches_creator_size(self, bucket: Optional[str]) -> bool:
        if not self.creator_size:
            return True
        return bucket in self.creator_size

    def matches_followers(self, followers: Optional[int]) -> bool:
        lo = self.custom_min_followers if self.custom_min_followers is not None else self.min_followers
        hi = self.custom_max_followers if self.custom_max_followers is not None else self.max_followers
        if followers is None:
            return lo is None and hi is None  # недостающие данные не додумываем - не проходят строгий фильтр
        if lo is not None and followers < lo:
            return False
        if hi is not None and followers > hi:
            return False
        return True

    def matches_metrics(self, median_views: Optional[float], avg_views: Optional[float],
                         engagement_rate: Optional[float]) -> bool:
        if self.min_median_views is not None:
            if median_views is None or median_views < self.min_median_views:
                return False
        if self.min_avg_views is not None:
            if avg_views is None or avg_views < self.min_avg_views:
                return False
        if self.min_engagement_rate is not None:
            if engagement_rate is None or engagement_rate < self.min_engagement_rate:
                return False
        return True

    def matches_topics(self, topic_tags: list[str]) -> bool:
        tags = {_normalize_topic(t) for t in (topic_tags or [])}
        if self.exclude_topics and tags & set(self.exclude_topics):
            return False
        if self.include_topics and not (tags & set(self.include_topics)):
            return False
        return True

    def matches_geo(self, geo: Optional[str], language: Optional[str]) -> bool:
        if self.geo and geo not in self.geo:
            return False
        if self.language and language not in self.language:
            return False
        return True


class ResolvedBrand(BaseModel):
    """Результат BrandResolver - см. app/analysis/brand_resolver.py."""

    brand_name: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    input_type: Literal["name", "url"]
    source_url: Optional[str] = None
    detected_platform: Optional[Platform] = None
    normalized_handle: Optional[str] = None


class AnalyzeRequest(BaseModel):
    brand: str
    platforms: list[Platform] = Field(default_factory=lambda: ["youtube"])
    settings: AnalysisConfig = Field(default_factory=AnalysisConfig)
    # Optional (раздел 6 hotfix): основной UX - один бренд; если заданы,
    # анализируются вместе с основным брендом (Market Map/White Space по всем,
    # DNA - по каждому). Пусто по умолчанию - single-brand mode.
    competitor_brands: list[str] = Field(default_factory=list)

    @field_validator("platforms")
    @classmethod
    def _at_least_one_platform(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("нужно выбрать хотя бы одну платформу")
        # убираем дубликаты, сохраняя порядок
        seen: list[str] = []
        for p in v:
            if p not in seen:
                seen.append(p)
        return seen


# connector_offline / manual_intervention_required - специфичны для локальных
# authenticated-коннекторов (Instagram/TikTok, раздел 10-19 real-data требований):
#   connector_offline           - local_connector/run.py не зарегистрирован или
#                                  не присылал heartbeat дольше порога;
#   manual_intervention_required - коннектор online, но словил CAPTCHA/challenge
#                                  и не может продолжить без ручного шага пользователя.
PlatformSourceStatus = Literal[
    "ok", "degraded", "unavailable", "connector_offline", "manual_intervention_required",
]


class PlatformCoverage(BaseModel):
    """Честный статус по каждой запрошенной платформе (раздел 3, 12)."""

    platform: Platform
    source_mode: Literal["live", "imported", "demo", "none"] = "none"
    status: PlatformSourceStatus = "unavailable"
    reason: Optional[str] = None
    items_collected: int = 0
    items_checked: int = 0
    # Число подтверждённых коммерческих интеграций, полученных именно на этой
    # платформе во время discovery/classification. Это additive metadata для
    # честной source funnel в UI; общий summary и аналитические расчёты не
    # меняет.
    confirmed_integrations: int = 0
    organic_mentions: int = 0
    potential_creators: int = 0
    # Точечная доработка (Tavily primary / SerpAPI fallback): для platform=
    # "articles" честно показывает, какой search provider реально использовался
    # ("tavily"/"serpapi"). None для остальных платформ (не относится к ним).
    search_provider: Optional[str] = None


class AnalysisCoverage(BaseModel):
    sources: list[str] = Field(default_factory=list)
    live_sources: list[str] = Field(default_factory=list)
    imported_sources: list[str] = Field(default_factory=list)
    degraded_sources: list[str] = Field(default_factory=list)
    platforms: list[PlatformCoverage] = Field(default_factory=list)


class AnalysisSummary(BaseModel):
    integrations_found: int = 0
    creators_used: int = 0
    creator_universe_size: int = 0
    # Раздел 10 доработки: "Подтверждённые интеграции" и "Авторы с органическим
    # brand affinity" - два отдельных числа, которые UI не должен смешивать.
    # integrations_found не меняет смысл (обратная совместимость) - это два
    # ДОПОЛНИТЕЛЬНЫХ, additive поля.
    confirmed_integrations: int = 0
    potential_creators_count: int = 0


class AnalysisResult(BaseModel):
    """Итоговая схема GET /api/analysis/{analysis_id} (раздел 12 требований).

    Все под-секции (market_map/competitor_dna/next_move/white_space/our_move)
    переиспользуют существующие builder-и из app/analytics/* без изменений -
    это просьба итоговый JSON собранный orchestration pipeline-ом.
    """

    analysis_id: str
    created_at: str
    brand: ResolvedBrand
    platforms: list[Platform]
    settings: AnalysisConfig
    coverage: AnalysisCoverage
    summary: AnalysisSummary
    market_map: dict = Field(default_factory=dict)
    competitor_dna: list = Field(default_factory=list)
    next_move: list = Field(default_factory=list)
    white_space: dict = Field(default_factory=dict)
    our_move: dict = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    # Раздел 2/9/10 доработки: авторы/страницы с органической brand affinity,
    # но без подтверждённого коммерческого сигнала - НЕ Integration, никогда не
    # входят в summary.integrations_found/confirmed_integrations.
    potential_creators: list[PotentialCreatorSignal] = Field(default_factory=list)
    # Presentation-only real findings for the data-dense UI. Every row is
    # assembled from normalized Integration/PotentialCreatorSignal objects;
    # no synthetic fallback is added here.
    findings: list[dict] = Field(default_factory=list)
