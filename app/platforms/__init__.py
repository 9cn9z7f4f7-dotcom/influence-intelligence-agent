"""Unified platform adapters (Source Router). См. app/platforms/base.py."""
from __future__ import annotations

from app.platforms.articles import ArticlesPlatformAdapter
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult
from app.platforms.instagram import InstagramPlatformAdapter
from app.platforms.tiktok import TikTokPlatformAdapter
from app.platforms.youtube import YouTubePlatformAdapter

REGISTRY: dict[str, type[PlatformAdapter]] = {
    "youtube": YouTubePlatformAdapter,
    "instagram": InstagramPlatformAdapter,
    "tiktok": TikTokPlatformAdapter,
    "articles": ArticlesPlatformAdapter,
}


def get_platform_adapter(platform: str) -> PlatformAdapter:
    cls = REGISTRY.get(platform)
    if cls is None:
        raise ValueError(f"неизвестная платформа: {platform}")
    return cls()


__all__ = [
    "PlatformAdapter",
    "PlatformDiscoveryResult",
    "YouTubePlatformAdapter",
    "InstagramPlatformAdapter",
    "TikTokPlatformAdapter",
    "ArticlesPlatformAdapter",
    "REGISTRY",
    "get_platform_adapter",
]
