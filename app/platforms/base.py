"""
Единый интерфейс Platform Adapter ("Source Router", раздел 2 требований).

Любая платформа (YouTube/Instagram/TikTok) реализует ОДИН и тот же контракт:

    discover_brand_content(brand, config) -> PlatformDiscoveryResult
    detect_integration(raw_item, brand_terms) -> DetectorResult-подобный объект
    extract_creator(raw_item) -> Creator | None
    normalize_creator(creator) -> Creator
    normalize_integration(integration) -> Integration

Это позволяет orchestration pipeline (app/analysis/pipeline.py) работать со
всеми платформами одинаково, не зная деталей конкретного API, и честно
показывать статус каждой платформы (ok/degraded/unavailable) вместо того,
чтобы имитировать данные, которых на самом деле нет.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.models import Creator, Integration

PlatformStatus = str  # "ok" | "degraded" | "unavailable"
PlatformSourceMode = str  # "live" | "imported" | "none"


@dataclass
class PlatformDiscoveryResult:
    """Результат discover_brand_content() - честный статус + сырые кандидаты."""

    platform: str
    status: PlatformStatus
    source_mode: PlatformSourceMode
    reason: Optional[str] = None
    raw_items: list[dict] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    # Если live недоступен - конкретная подсказка пользователю, как импортировать
    # заранее собранные публичные данные (никогда не "симулируем" live за него).
    import_hint: Optional[str] = None
    # Точечная доработка (Tavily/SerpAPI): какой web-search провайдер реально
    # использовался для этого discovery ("tavily"/"serpapi"/None). Заполняется
    # только ArticlesPlatformAdapter - для остальных платформ остаётся None и
    # ни на что не влияет.
    search_provider: Optional[str] = None
    # Optional source-specific funnel metadata. For Articles this separates
    # web candidates from pages that passed the article-like content gate.
    candidate_count: Optional[int] = None
    accepted_count: Optional[int] = None


class PlatformAdapter(ABC):
    """Абстрактный базовый класс для всех platform-специфичных адаптеров."""

    platform_name: str

    @abstractmethod
    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        """Ищет публичный контент, потенциально связанный с брендом.

        Обязана честно возвращать status="unavailable" (с reason и import_hint),
        если платформа не поддерживает live-доступ без обхода защит - НИКОГДА
        не подделывает данные под видом live.
        """

    @abstractmethod
    def detect_integration(self, raw_item: dict, brand_terms: list[str]) -> Any:
        """Детерминированно классифицирует raw_item: confirmed/manual_review/
        organic_mention/rejected (см. app/detection.py::categorize_signals)."""

    @abstractmethod
    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        """Строит Creator из raw platform item, или None если данных недостаточно."""

    @abstractmethod
    def normalize_creator(self, creator: Creator) -> Creator:
        """Приводит Creator к общей схеме (platform, source_mode и т.п.)."""

    @abstractmethod
    def normalize_integration(self, integration: Integration) -> Integration:
        """Приводит Integration к общей схеме."""
