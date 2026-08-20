"""
Potential creator detection (раздел 2 доработки).

Если бренд явно присутствует (has_brand_evidence=True), но НИ ОДНОГО hard
commercial signal (app/hard_signals.py) не найдено - контент не выбрасывается.
Если при этом видна органическая brand affinity ("я ношу BRAND", "рекомендую
PRODUCT", "мой любимый PRODUCT", повторяющееся упоминание и т.п.) - это
potential_creator, а НЕ confirmed integration и НЕ обычный organic_mention,
который тихо пропадает - создаётся PotentialCreatorSignal (см. app/models.py),
который попадает в creator universe/candidate pool, но никогда не увеличивает
число confirmed integrations (раздел 2, 11).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.models import Evidence, PotentialCreatorSignal
from app.promotion_patterns import PromotionPatternSet, generate_promotion_patterns

# Только "органические" семьи (раздел 2 примеры: "я ношу", "я пользуюсь",
# "мой любимый", "рекомендую", positive first-person experience). "relationship"/
# "ambassador"/"gifted"/"promo_code"/"affiliate"/"commercial_cta" сюда НЕ входят -
# это уже область hard commercial signals (app/hard_signals.py), не organic affinity.
AFFINITY_FAMILIES = ["first_person_use", "recommendation", "visual_product_presence"]

REPEATED_MENTION_THRESHOLD = 2


def _count_ci(haystack: Optional[str], needle: str) -> int:
    if not haystack or not needle:
        return 0
    return haystack.lower().count(needle.lower())


def detect_brand_affinity_signals(
    text: str, brand_terms: Optional[list[str]] = None, patterns: Optional[PromotionPatternSet] = None,
    language: str = "ru",
) -> list[str]:
    """Возвращает список реально найденных affinity-фраз (raw matches), []
    если ничего не найдено - никогда не выдумывает сигнал."""
    if not text:
        return []
    patterns = patterns or generate_promotion_patterns(brand=(brand_terms or [""])[0], language=language)
    phrases = patterns.phrases_for(*AFFINITY_FAMILIES)
    text_l = text.lower()
    matched = [p for p in phrases if p.lower() in text_l]

    if brand_terms:
        mention_count = sum(_count_ci(text, term) for term in brand_terms)
        if mention_count >= REPEATED_MENTION_THRESHOLD and "repeated_brand_mention" not in matched:
            matched.append("repeated_brand_mention")

    return matched


def build_potential_creator_signal(
    platform: str, potential_reason: str, brand_affinity_signals: list[str],
    creator_id: Optional[str] = None, creator_name: Optional[str] = None,
    source_url: Optional[str] = None, observed_at: Optional[datetime] = None,
    evidence: Optional[list[Evidence]] = None,
) -> PotentialCreatorSignal:
    return PotentialCreatorSignal(
        creator_id=creator_id, creator_name=creator_name, platform=platform,
        source_url=source_url, observed_at=observed_at, potential_reason=potential_reason,
        brand_affinity_signals=brand_affinity_signals, evidence=evidence or [],
    )
