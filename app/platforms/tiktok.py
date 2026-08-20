"""
TikTok - live-скрейпинг НЕ реализован принципиально (симметрично Instagram,
см. app/platforms/instagram.py). Официальный TikTok API также не даёт
доступа к произвольным чужим (конкурентным) аккаунтам без их авторизации,
а веб-скрейпинг TikTok защищён anti-bot механизмами - обходить их этот
проект осознанно не делает (раздел 3 требований).
"""
from __future__ import annotations

from typing import Optional

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.models import Creator, Integration
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult

UNAVAILABLE_REASON = (
    "TikTok защищён авторизацией/anti-bot механизмами; официальный API не даёт доступа "
    "к чужим (конкурентным) аккаунтам без их согласия. Live-discovery для TikTok осознанно "
    "не реализован, чтобы не обходить защиту платформы."
)
IMPORT_HINT = "manage.py import-integrations --file <csv|json> (platform=tiktok)"


class TikTokPlatformAdapter(PlatformAdapter):
    platform_name = "tiktok"

    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        return PlatformDiscoveryResult(
            platform="tiktok",
            status="unavailable",
            source_mode="none",
            reason=UNAVAILABLE_REASON,
            import_hint=IMPORT_HINT,
        )

    def detect_integration(self, raw_item: dict, brand_terms: list[str]):
        raise NotImplementedError(
            "TikTok live-детекция не реализована - discover_brand_content() всегда unavailable"
        )

    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        raise NotImplementedError(
            "TikTok live-экстракция creator не реализована - используйте import fallback"
        )

    def normalize_creator(self, creator: Creator) -> Creator:
        creator.platform = "tiktok"
        return creator

    def normalize_integration(self, integration: Integration) -> Integration:
        integration.platform = "tiktok"
        return integration
