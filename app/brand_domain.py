"""
Brand Domain Understanding (раздел 4 доработки "поведенческие и коммерческие сигналы").

Перед discovery/detection нужно явно определить, с какими доменами и терминами
реально связан бренд - иначе "brand domain in a link" невозможно детерминированно
проверить (см. app/links_extractor.py::classify_link).

ВАЖНО (раздел 4): AI может генерировать ГИПОТЕЗЫ доменов/алиасов (см.
ai_domain_hypotheses ниже), но AI-гипотеза САМА ПО СЕБЕ никогда не считается
подтверждённым official_domain - только подтверждается детерминированной
(строковой) сверкой с реально наблюдаемыми фактами: canonical URL, из которого
резолвился бренд (app.analysis.brand_resolver), или домен, который буквально
содержит brand slug. Это тот же принцип, что и для visual evidence в
app/detection.py::combine_dom_and_visual - "AI не создаёт evidence из ничего".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlparse

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def _slugify(name: str) -> str:
    return _NON_ALNUM_RE.sub("", (name or "").lower())


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc or urlparse(f"//{url}").netloc
    except ValueError:
        return ""
    return netloc.lower().split(":")[0].removeprefix("www.")


@dataclass
class BrandDomainProfile:
    """brand_name / official_domains[] / known_product_domains[] / product_terms[] /
    brand_aliases[] (раздел 4) + hypothesis_domains[] - AI/heuristic гипотезы,
    которые НЕ являются evidence сами по себе (см. модуль docstring)."""

    brand_name: str
    official_domains: list[str] = field(default_factory=list)
    known_product_domains: list[str] = field(default_factory=list)
    product_terms: list[str] = field(default_factory=list)
    brand_aliases: list[str] = field(default_factory=list)
    hypothesis_domains: list[str] = field(default_factory=list)

    def all_confirmed_domains(self) -> set[str]:
        return set(self.official_domains) | set(self.known_product_domains)

    def matches_domain(self, domain: str) -> bool:
        domain = (domain or "").lower().removeprefix("www.")
        if not domain:
            return False
        if domain in self.all_confirmed_domains():
            return True
        # Детерминированная эвристика (не AI): домен буквально содержит brand slug -
        # напр. "shop.nike.com" для brand "Nike" - тоже считается подтверждённым,
        # даже если не входит явно в official_domains[].
        slug = _slugify(self.brand_name)
        return bool(slug) and slug in domain.replace(".", "").replace("-", "")


def build_brand_domain_profile(
    brand_name: str,
    aliases: Optional[list[str]] = None,
    source_url: Optional[str] = None,
    known_product_domains: Optional[list[str]] = None,
    product_terms: Optional[list[str]] = None,
    ai_domain_hypotheses: Optional[list[str]] = None,
) -> BrandDomainProfile:
    """Строит BrandDomainProfile для одного бренда (раздел 4).

    source_url - опциональный canonical URL, из которого резолвился бренд
    (например ResolvedBrand.source_url) - если его домен детерминированно
    содержит brand slug, он подтверждается как official domain (реальное
    web-evidence, а не AI-гипотеза).
    """
    aliases = [a for a in (aliases or []) if a and a.strip()]
    slug = _slugify(brand_name)

    official: list[str] = []
    if source_url:
        d = domain_of(source_url)
        if d and (slug in d.replace(".", "").replace("-", "") or not slug):
            official.append(d)

    hypotheses = list(dict.fromkeys(ai_domain_hypotheses or []))
    if slug:
        for tld in (".com", ".ru"):
            guess = f"{slug}{tld}"
            if guess not in hypotheses:
                hypotheses.append(guess)

    return BrandDomainProfile(
        brand_name=brand_name,
        official_domains=list(dict.fromkeys(official)),
        known_product_domains=list(dict.fromkeys(known_product_domains or [])),
        product_terms=list(dict.fromkeys(product_terms or [])),
        brand_aliases=aliases,
        hypothesis_domains=hypotheses,
    )


def build_brand_domain_profile_from_terms(brand_terms: list[str]) -> BrandDomainProfile:
    """Упрощённый конструктор, когда доступны только brand_terms (canonical + aliases),
    без ResolvedBrand.source_url - используется platform-адаптерами, у которых
    сигнатура detect_integration(raw_item, brand_terms) не прокидывает весь
    ResolvedBrand (раздел 4, чтобы не менять публичный ABC-контракт)."""
    if not brand_terms:
        return BrandDomainProfile(brand_name="")
    return build_brand_domain_profile(brand_terms[0], aliases=list(brand_terms[1:]))
