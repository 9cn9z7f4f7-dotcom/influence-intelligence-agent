"""
Deterministic hard commercial signals (раздел 1 доработки).

"Убрать confidence как порог confirmed": если найден ХОТЯ БЫ ОДИН однозначный
hard commercial signal - confirmed_integration = true, без комбинации
нескольких сигналов и без confidence-порога.

Это ДОПОЛНЯЕТ (не заменяет) app/detection.py::categorize_signals - см.
escalate_with_hard_signals() там же: hard signal может поднять существующую
категорию до "confirmed" ТОЛЬКО если has_brand_evidence уже True (тот же
принцип "AI/новый сигнал никогда не создаёт brand evidence из ничего", что и
для visual evidence в app/detection.py::combine_dom_and_visual).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from app.links_extractor import LinkClassification

PAID_PARTNERSHIP_PATTERNS = [
    "paid partnership", "paid partnership with", "оплаченное партнёрство", "платное партнёрство",
]

AD_DISCLOSURE_PATTERNS = [
    "#ad", "#sponsored", "#спонсировано", "#реклама", "рекламная интеграция",
    "на правах рекламы", "sponsored by", "спонсор этого видео", "спонсор этого материала",
]

PROMO_CODE_PATTERN = re.compile(
    r"(промо[\s-]?код|промокод|discount\s*code|promo\s*code)\s*[:\-]?\s*([A-ZА-Я0-9_]{3,20})",
    re.IGNORECASE,
)

RELATIONSHIP_TEMPLATES = [
    "в партнёрстве с {brand}", "партнёрство с {brand}", "партнер {brand}", "партнёр {brand}",
    "амбассадор {brand}", "посол бренда {brand}", "при поддержке {brand}",
    "in partnership with {brand}", "ambassador for {brand}", "ambassador of {brand}",
    "sponsored by {brand}", "with the support of {brand}",
]

CTA_WORDS = [
    "купить", "заказать", "перейти по ссылке", "переходи по ссылке", "ссылка ниже", "ссылка в профиле",
    "buy now", "order now", "shop now", "link below", "link in bio", "get it here",
]


def _find_ci(haystack: Optional[str], needle: str) -> Optional[str]:
    if not haystack or not needle:
        return None
    return needle if needle.lower() in haystack.lower() else None


@dataclass
class HardSignalResult:
    matched: bool
    signals: dict[str, dict] = field(default_factory=dict)  # name -> {matched, raw_fragment}
    reasons: list[str] = field(default_factory=list)


def detect_hard_commercial_signals(
    text: str,
    brand_name: str = "",
    brand_aliases: Optional[list[str]] = None,
    links: Optional[list[LinkClassification]] = None,
    bio_links: Optional[list[LinkClassification]] = None,
) -> HardSignalResult:
    """Раздел 1: любой ОДИН matched signal => .matched=True.

    text        - title+description/caption/main_text (весь доступный текст).
    links       - уже классифицированные ссылки (app.links_extractor.classify_link),
                  найденные в контенте (description/main_text) - НЕ bio.
    bio_links   - ссылки из bio/profile creator-а (раздел 1: "direct commercial
                  brand/product link in creator bio/profile when URL destination
                  can be deterministically attributed to the analyzed brand").
    """
    links = links or []
    bio_links = bio_links or []
    brand_names = [brand_name] + [a for a in (brand_aliases or []) if a]
    signals: dict[str, dict] = {}

    hit = next((p for p in PAID_PARTNERSHIP_PATTERNS if _find_ci(text, p)), None)
    signals["paid_partnership"] = {"matched": bool(hit), "raw_fragment": hit}

    hit = next((p for p in AD_DISCLOSURE_PATTERNS if _find_ci(text, p)), None)
    signals["ad_disclosure"] = {"matched": bool(hit), "raw_fragment": hit}

    promo_match = PROMO_CODE_PATTERN.search(text or "")
    signals["promo_code"] = {"matched": bool(promo_match), "raw_fragment": promo_match.group(0) if promo_match else None}

    affiliate_link = next((l for l in links if l.is_affiliate), None)
    signals["affiliate_url"] = {
        "matched": bool(affiliate_link), "raw_fragment": affiliate_link.url if affiliate_link else None,
    }

    bio_commercial_link = next((l for l in bio_links if l.is_brand_or_product), None)
    signals["creator_commercial_bio_url"] = {
        "matched": bool(bio_commercial_link), "raw_fragment": bio_commercial_link.url if bio_commercial_link else None,
    }

    brand_url_link = next((l for l in links if l.is_brand_or_product), None)
    cta_hit = next((p for p in CTA_WORDS if _find_ci(text, p)), None)
    cta_with_brand_url = bool(cta_hit and brand_url_link)
    signals["commercial_cta_with_brand_url"] = {
        "matched": cta_with_brand_url,
        "raw_fragment": f"{cta_hit} + {brand_url_link.url}" if cta_with_brand_url else None,
    }
    # Прямая коммерческая ссылка на бренд/товар в самом контенте (не CTA-фразе) -
    # раздел 1: "affiliate/referral URL бренда" / "direct commercial ... link" -
    # тоже hard signal сама по себе, даже без сопутствующей CTA-фразы, если это
    # явно brand/product domain (не просто "любая ссылка").
    signals["brand_or_product_url"] = {
        "matched": bool(brand_url_link) and not cta_with_brand_url,
        "raw_fragment": brand_url_link.url if brand_url_link else None,
    }

    relationship_hit = None
    for name in brand_names:
        if not name:
            continue
        for template in RELATIONSHIP_TEMPLATES:
            phrase = template.format(brand=name)
            if _find_ci(text, phrase):
                relationship_hit = phrase
                break
        if relationship_hit:
            break
    signals["relationship_wording"] = {"matched": bool(relationship_hit), "raw_fragment": relationship_hit}

    reasons = [name for name, sig in signals.items() if sig["matched"]]
    matched = bool(reasons)
    return HardSignalResult(matched=matched, signals=signals, reasons=reasons)
