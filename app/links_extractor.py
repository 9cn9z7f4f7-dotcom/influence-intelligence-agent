"""
Links-first discovery (раздел 3 доработки).

Flow (раздел 3): page/video/profile -> extract outbound links -> resolve/
normalize URLs -> determine destination domain -> compare with brand/product
domains -> inspect text/context around link.

Также резолвит ОДИН уровень redirect chain для intermediary bio-страниц
(Linktree/Beacons/Taplink/...): creator -> intermediary link -> brand/product
domain. НЕ обходит authentication/captcha - если страницу не получилось
получить обычным GET (как ArticleParser), она просто не резолвится дальше -
никаких synthetic/imagined ссылок.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from app.brand_domain import BrandDomainProfile, domain_of

URL_PATTERN = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)

# Раздел 3: "Linktree / Beacons / Taplink / similar intermediary pages".
INTERMEDIARY_DOMAINS = {
    "linktr.ee", "linktree.com", "beacons.ai", "beacons.page", "taplink.cc",
    "taplink.at", "bio.link", "solo.to", "campsite.bio", "lnk.bio", "carrd.co",
    "msha.ke", "biolink.com", "shor.by",
}

AFFILIATE_MARKER_RE = re.compile(
    r"(\bref=|\baff(?:iliate)?[_-]?id=|/aff/|/ref/|utm_source=partner|utm_medium=affiliate|\baffiliate\b)",
    re.IGNORECASE,
)
UTM_RE = re.compile(r"utm_[a-z]+=", re.IGNORECASE)

DEFAULT_FETCH_TIMEOUT = 5.0


def extract_links(text: Optional[str]) -> list[str]:
    """Достаёт все http(s) URL из произвольного текста (описание видео, caption,
    bio, main_text статьи) - используется, когда нет структурированного
    outbound_links (в отличие от ArticleParser, который уже парсит <a href>)."""
    if not text:
        return []
    found = [m.rstrip(".,;:!?\"')]") for m in URL_PATTERN.findall(text)]
    seen: set[str] = set()
    unique: list[str] = []
    for url in found:
        if url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


def is_intermediary_domain(domain: str) -> bool:
    domain = (domain or "").lower().removeprefix("www.")
    return domain in INTERMEDIARY_DOMAINS


@dataclass
class LinkClassification:
    url: str
    domain: str
    is_intermediary: bool = False
    is_brand_domain: bool = False
    is_product_domain: bool = False
    has_utm: bool = False
    is_affiliate: bool = False

    @property
    def is_brand_or_product(self) -> bool:
        return self.is_brand_domain or self.is_product_domain


def classify_link(url: str, brand_profile: BrandDomainProfile) -> LinkClassification:
    domain = domain_of(url)
    is_brand = domain in set(brand_profile.official_domains) or (
        bool(domain) and brand_profile.matches_domain(domain)
    )
    is_product = domain in set(brand_profile.known_product_domains)
    return LinkClassification(
        url=url,
        domain=domain,
        is_intermediary=is_intermediary_domain(domain),
        is_brand_domain=is_brand and not is_product,
        is_product_domain=is_product,
        has_utm=bool(UTM_RE.search(url)),
        is_affiliate=bool(AFFILIATE_MARKER_RE.search(url)),
    )


def classify_links(urls: list[str], brand_profile: BrandDomainProfile) -> list[LinkClassification]:
    return [classify_link(u, brand_profile) for u in urls]


def default_fetch_intermediary_links(url: str, timeout: float = DEFAULT_FETCH_TIMEOUT) -> list[str]:
    """Best-effort публичный GET intermediary-страницы (Linktree и т.п.) - НЕ
    обходит authentication/captcha, просто обычный HTTP-запрос, как ArticleParser.
    Любая ошибка -> [] (никогда не роняет discovery, раздел 3/19)."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 - best effort, никогда не ронять discovery
        return []
    return extract_links(resp.text)


@dataclass
class LinksFirstMatch:
    source_url: str
    matched_domain: Optional[str] = None
    classification: Optional[LinkClassification] = None
    resolution_path: list[str] = field(default_factory=list)  # [source_url, (intermediary), final_url]
    via_intermediary: bool = False


def resolve_links_first(
    candidate_urls: list[str],
    brand_profile: BrandDomainProfile,
    fetch_intermediary_links: Optional[Callable[[str], list[str]]] = None,
    max_intermediary_fetches: int = 5,
) -> list[LinksFirstMatch]:
    """Раздел 3: для каждого candidate URL - если он сразу указывает на
    brand/product domain, это прямое совпадение. Если это intermediary
    (Linktree/Beacons/Taplink/...) - получает ссылки СО страницы intermediary
    (через fetch_intermediary_links, по умолчанию real HTTP GET) и ищет среди
    них brand/product domain - ОДИН уровень redirect chain:
    creator -> intermediary link -> brand/product domain.
    """
    fetcher = fetch_intermediary_links or default_fetch_intermediary_links
    results: list[LinksFirstMatch] = []
    fetch_budget = max_intermediary_fetches

    for url in candidate_urls:
        classification = classify_link(url, brand_profile)
        if classification.is_brand_or_product:
            results.append(LinksFirstMatch(
                source_url=url, matched_domain=classification.domain, classification=classification,
                resolution_path=[url], via_intermediary=False,
            ))
            continue

        if classification.is_intermediary and fetch_budget > 0:
            fetch_budget -= 1
            page_links = fetcher(url)
            resolved: Optional[LinkClassification] = None
            for inner_url in page_links:
                inner_cls = classify_link(inner_url, brand_profile)
                if inner_cls.is_brand_or_product:
                    resolved = inner_cls
                    break
            if resolved is not None:
                results.append(LinksFirstMatch(
                    source_url=url, matched_domain=resolved.domain, classification=resolved,
                    resolution_path=[url, resolved.url], via_intermediary=True,
                ))
                continue

        results.append(LinksFirstMatch(source_url=url, resolution_path=[url]))

    return results


def best_links_first_match(matches: list[LinksFirstMatch]) -> Optional[LinksFirstMatch]:
    return next((m for m in matches if m.matched_domain), None)
