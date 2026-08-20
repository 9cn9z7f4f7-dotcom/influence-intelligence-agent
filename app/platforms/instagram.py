"""
Instagram - live-скрейпинг НЕ реализован принципиально.

Публичный официальный API Instagram (Graph API) требует бизнес-аккаунт и
OAuth-авторизацию владельца аккаунта - он не даёт доступа к произвольному
чужому конкурентному аккаунту. Любой другой способ получить данные о чужом
аккаунте means обход логина/CAPTCHA/anti-bot защиты, что этот проект
осознанно не делает (раздел 3 требований).

Поэтому discover_brand_content() всегда честно возвращает status="unavailable"
с понятной причиной и подсказкой на CSV/JSON import fallback - НИКОГДА не
имитирует "как будто" live-данные.
"""
from __future__ import annotations

from typing import Optional

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.models import Creator, Integration
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult

UNAVAILABLE_REASON = (
    "Instagram защищён авторизацией/CAPTCHA/anti-bot механизмами; официальный Graph API "
    "не даёт доступа к чужим (конкурентным) аккаунтам без их согласия. Live-discovery "
    "для Instagram осознанно не реализован, чтобы не обходить защиту платформы."
)
IMPORT_HINT = "manage.py import-integrations --file <csv|json> (platform=instagram)"


class InstagramPlatformAdapter(PlatformAdapter):
    platform_name = "instagram"

    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        return PlatformDiscoveryResult(
            platform="instagram",
            status="unavailable",
            source_mode="none",
            reason=UNAVAILABLE_REASON,
            import_hint=IMPORT_HINT,
        )

    def detect_integration(self, raw_item: dict, brand_terms: list[str]):
        raise NotImplementedError(
            "Instagram live-детекция не реализована - discover_brand_content() всегда unavailable"
        )

    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        raise NotImplementedError(
            "Instagram live-экстракция creator не реализована - используйте import fallback"
        )

    def normalize_creator(self, creator: Creator) -> Creator:
        creator.platform = "instagram"
        return creator

    def normalize_integration(self, integration: Integration) -> Integration:
        integration.platform = "instagram"
        return integration
