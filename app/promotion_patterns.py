"""
PromotionPatternGenerator (раздел 5 доработки).

Расширяет discovery vocabulary структурированными "семьями" фраз, которыми
креаторы реально описывают отношения с брендом - не только "BRAND реклама".
Всегда работает БЕЗ AI (встроенный, language-aware словарь по умолчанию) -
AI (через app/providers/openrouter.py, если доступен) может ТОЛЬКО дополнить
словарь новыми вариантами, никогда не заменяет его и никогда не считается
evidence сам по себе (раздел 5: "не считать AI-generated phrase evidence
факта интеграции" - здесь это вообще не evidence, а только discovery-словарь,
который затем матчится детерминированно против реального текста).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

FAMILY_NAMES = [
    "first_person_use", "recommendation", "commercial_cta", "relationship", "gifted",
    "promo_code", "affiliate", "product_link", "ambassador", "visual_product_presence",
]

# language -> family -> phrases. Только generic, НЕ brand-specific шаблоны -
# конкретное имя бренда подставляется вызывающим кодом при матчинге по тексту,
# а не здесь (сам генератор не хардкодит ни одного бренда).
_DEFAULT_VOCABULARY_RU: dict[str, list[str]] = {
    "first_person_use": ["ношу", "хожу в", "бегаю в", "тренируюсь в", "пользуюсь", "использую", "юзаю"],
    "recommendation": ["советую", "рекомендую", "берите", "советую взять", "рекомендую попробовать"],
    "commercial_cta": ["ссылка ниже", "ссылка в профиле", "ссылка в описании", "купить", "заказать", "перейти по ссылке"],
    "relationship": ["вместе с", "партнёр", "партнерство с", "в партнёрстве с", "амбассадор", "при поддержке"],
    "gifted": ["подарили", "презентовали", "получил в подарок", "получила в подарок", "gifted"],
    "promo_code": ["промокод", "промо-код", "скидка по коду", "код на скидку"],
    "affiliate": ["партнёрская ссылка", "affiliate", "по моей ссылке", "реферальная ссылка"],
    "product_link": ["ссылка на товар", "модель здесь", "этот товар"],
    "ambassador": ["амбассадор", "посол бренда", "лицо бренда"],
    "visual_product_presence": ["мой любимый", "любимая модель", "ношу постоянно", "не снимаю"],
}

_DEFAULT_VOCABULARY_EN: dict[str, list[str]] = {
    "first_person_use": ["i wear", "i run in", "i train in", "i use", "wearing my"],
    "recommendation": ["i recommend", "highly recommend", "you should get", "go grab"],
    "commercial_cta": ["link below", "link in bio", "link in description", "buy now", "shop now", "order now"],
    "relationship": ["in partnership with", "partnered with", "teamed up with", "with the support of"],
    "gifted": ["gifted by", "sent by", "received as a gift", "pr package"],
    "promo_code": ["promo code", "discount code", "use code"],
    "affiliate": ["affiliate link", "referral link", "through my link"],
    "product_link": ["product link", "shop this look", "linked below"],
    "ambassador": ["ambassador", "brand ambassador", "face of the brand"],
    "visual_product_presence": ["my favorite", "my go-to", "never take it off"],
}


@dataclass
class PromotionPatternSet:
    brand: str
    language: str
    families: dict[str, list[str]] = field(default_factory=dict)
    ai_extended_families: dict[str, list[str]] = field(default_factory=dict)  # только для UI/debug - НЕ evidence

    def phrases_for(self, *family_names: str) -> list[str]:
        out: list[str] = []
        for fam in family_names:
            out.extend(self.families.get(fam, []))
        return list(dict.fromkeys(out))

    def all_phrases(self) -> list[str]:
        return self.phrases_for(*FAMILY_NAMES)


def _base_vocabulary(language: str) -> dict[str, list[str]]:
    return _DEFAULT_VOCABULARY_RU if (language or "ru").lower().startswith("ru") else _DEFAULT_VOCABULARY_EN


def generate_promotion_patterns(
    brand: str,
    category: Optional[str] = None,
    language: str = "ru",
    geography: Optional[str] = None,
    known_products: Optional[list[str]] = None,
    ai_client=None,
) -> PromotionPatternSet:
    """Строит PromotionPatternSet (раздел 5). Работает без AI (встроенный словарь);
    ai_client (опционально) - любой объект с методом
    `.generate_phrase_hypotheses(brand, category, language, geography, known_products) -> dict[str, list[str]]`
    (см. app/providers/openrouter.py) - используется ТОЛЬКО чтобы ДОПОЛНИТЬ
    словарь новыми вариантами (append-only, дедуп), никогда не заменяет базовый
    словарь и никогда не помечается как evidence."""
    base = {k: list(v) for k, v in _base_vocabulary(language).items()}
    families = {fam: list(base.get(fam, [])) for fam in FAMILY_NAMES}

    ai_extended: dict[str, list[str]] = {}
    if ai_client is not None and hasattr(ai_client, "generate_phrase_hypotheses"):
        try:
            hypotheses = ai_client.generate_phrase_hypotheses(
                brand=brand, category=category, language=language,
                geography=geography, known_products=known_products or [],
            ) or {}
        except Exception:  # noqa: BLE001 - AI-расширение словаря best-effort, никогда не роняет discovery
            hypotheses = {}
        for fam, phrases in hypotheses.items():
            if fam not in FAMILY_NAMES:
                continue
            new_phrases = [p for p in phrases if p and p not in families[fam]]
            if new_phrases:
                families[fam].extend(new_phrases)
                ai_extended[fam] = new_phrases

    return PromotionPatternSet(brand=brand, language=language, families=families, ai_extended_families=ai_extended)
