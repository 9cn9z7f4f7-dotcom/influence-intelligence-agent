"""
BrandResolver - универсальный вход для ЛЮБОГО бренда: имя ИЛИ ссылка на аккаунт.

Никаких захардкоженных брендов и никакого заранее заданного competitor config -
любое имя создаёт новый Competitor на лету (см. app/ingestion/identifiers.py).
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from app.analysis.models import ResolvedBrand

URL_PATTERN = re.compile(r"^https?://", re.IGNORECASE)

# platform -> (host substrings, handle extraction patterns в порядке приоритета)
_YOUTUBE_HANDLE_PATTERNS = [
    re.compile(r"/@([\w.\-]+)"),
    re.compile(r"/channel/([\w\-]+)"),
    re.compile(r"/c/([\w.\-]+)"),
    re.compile(r"/user/([\w.\-]+)"),
]
_INSTAGRAM_HANDLE_PATTERN = re.compile(r"^/([\w.\-]+)/?")
_TIKTOK_HANDLE_PATTERN = re.compile(r"/@([\w.\-]+)")


def _detect_platform(host: str) -> str | None:
    host = host.lower()
    if "youtube.com" in host or host == "youtu.be" or host.endswith(".youtu.be"):
        return "youtube"
    if "instagram.com" in host:
        return "instagram"
    if "tiktok.com" in host:
        return "tiktok"
    return None


def _extract_handle(platform: str | None, path: str) -> str | None:
    if platform == "youtube":
        for pattern in _YOUTUBE_HANDLE_PATTERNS:
            m = pattern.search(path)
            if m:
                return m.group(1)
        return None
    if platform == "instagram":
        m = _INSTAGRAM_HANDLE_PATTERN.match(path)
        if m and m.group(1) not in ("p", "reel", "stories", "explore"):
            return m.group(1)
        return None
    if platform == "tiktok":
        m = _TIKTOK_HANDLE_PATTERN.search(path)
        return m.group(1) if m else None
    return None


def resolve_brand(brand_input: str) -> ResolvedBrand:
    """Определяет brand_name/canonical_name/aliases/input_type/source_url/
    detected_platform/normalized_handle из свободного текста или URL."""
    text = (brand_input or "").strip()
    if not text:
        raise ValueError("brand не может быть пустым")

    if URL_PATTERN.match(text):
        parsed = urlparse(text)
        platform = _detect_platform(parsed.netloc)
        handle = _extract_handle(platform, parsed.path)
        brand_name = handle or parsed.netloc.lower()
        aliases = [handle] if handle and handle.lower() != brand_name.lower() else []
        return ResolvedBrand(
            brand_name=brand_name,
            canonical_name=brand_name,
            aliases=aliases,
            input_type="url",
            source_url=text,
            detected_platform=platform,
            normalized_handle=handle,
        )

    # Голое название бренда - никаких заранее заданных конфигов не требуется.
    return ResolvedBrand(
        brand_name=text,
        canonical_name=text,
        aliases=[],
        input_type="name",
        source_url=None,
        detected_platform=None,
        normalized_handle=None,
    )
