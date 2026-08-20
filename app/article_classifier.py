"""
ArticleClassifier - раздел 7 требований.

Детерминированный (без LLM) rule-based классификатор для web-статей,
структурно симметричный app/ingestion/live_youtube.py::IntegrationDetector,
но со своими категориями:

    confirmed_sponsored - явный sponsor disclosure ("реклама", "sponsored",
                          "на правах рекламы", "при поддержке"...)
    affiliate           - affiliate/партнёрская ссылка, promo/discount code
    partner_content     - "партнёрский материал"/"partner content" без явного
                          "реклама"-маркера
    editorial_review    - обзорные слова (обзор/review/тест) без коммерческого
                          disclosure - НЕ считается рекламой (раздел 7:
                          "просто редакционный обзор" != реклама)
    organic_mention     - просто упоминание бренда, без остальных сигналов
    manual_review       - есть brand evidence, но коммерческий сигнал
                          слишком слабый/противоречивый для уверенного вердикта
    rejected            - бренд не упомянут вообще

"Просто упоминание бренда - НЕ реклама" (раздел 7) - organic_mention и
editorial_review явно отделены от confirmed_sponsored/affiliate/partner_content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SPONSOR_WORDING = [
    "на правах рекламы", "#реклама", " реклама.", "реклама:", "спонсор этого материала",
    "спонсор статьи", "спонсорский материал", "sponsored", "sponsored post", "sponsor disclosure",
    "advertisement", "продвижение материала", "промо-материал",
]
PARTNER_WORDING = [
    "партнёрский материал", "партнерский материал", "partner content", "при поддержке",
    "подготовлено совместно", "материал подготовлен при участии",
]
AFFILIATE_PATTERNS = [
    re.compile(r"(промо[\s-]?код|промокод|discount\s*code|promo\s*code)\s*[:\-]?\s*[A-ZА-Я0-9_]{3,20}", re.IGNORECASE),
    re.compile(r"(\bref=|affiliate|/aff/|utm_source=partner)", re.IGNORECASE),
]
REVIEW_WORDING = [
    "обзор", "review", "тестируем", "протестировали", "мы протестировали", "распаковка",
    "unboxing", "сравнение", "тест-драйв", "разбор",
]

CATEGORIES = (
    "confirmed_sponsored", "affiliate", "partner_content", "editorial_review",
    "organic_mention", "manual_review", "rejected",
)

# category -> (has_commercial_evidence, base_confidence)
_CATEGORY_CONFIDENCE = {
    "confirmed_sponsored": 0.9,
    "affiliate": 0.75,
    "partner_content": 0.7,
    "editorial_review": 0.5,
    "organic_mention": 0.3,
}


def _find_ci(haystack: str | None, needle: str) -> bool:
    if not haystack or not needle:
        return False
    return needle.lower() in haystack.lower()


@dataclass
class ArticleClassification:
    category: str
    confidence: float
    signals: dict[str, dict] = field(default_factory=dict)
    has_brand_evidence: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def has_commercial_evidence(self) -> bool:
        return self.category in {"confirmed_sponsored", "affiliate", "partner_content"}


class ArticleClassifier:
    """Правило приоритета сигналов: sponsor_wording > affiliate_pattern >
    partner_wording > review_wording > (просто brand mention). Первый
    сработавший (в этом порядке) сигнал определяет категорию - осознанно
    консервативно: явное disclosure перевешивает более слабые сигналы."""

    def classify(self, title: str | None, main_text: str | None, brand_terms: list[str]) -> ArticleClassification:
        text_all = f"{title or ''} {main_text or ''}"
        signals: dict[str, dict] = {}

        brand_hit = next((t for t in brand_terms if _find_ci(text_all, t)), None)
        signals["brand_mention"] = {"matched": bool(brand_hit), "raw_fragment": brand_hit}
        if not brand_hit:
            return ArticleClassification(category="rejected", confidence=0.0, signals=signals,
                                          has_brand_evidence=False, reasons=[])

        sponsor_hit = next((w for w in SPONSOR_WORDING if _find_ci(text_all, w)), None)
        signals["sponsor_wording"] = {"matched": bool(sponsor_hit), "raw_fragment": sponsor_hit}

        affiliate_match = next((m for m in (p.search(text_all) for p in AFFILIATE_PATTERNS) if m), None)
        signals["affiliate_pattern"] = {
            "matched": bool(affiliate_match), "raw_fragment": affiliate_match.group(0) if affiliate_match else None,
        }

        partner_hit = next((w for w in PARTNER_WORDING if _find_ci(text_all, w)), None)
        signals["partner_wording"] = {"matched": bool(partner_hit), "raw_fragment": partner_hit}

        review_hit = next((w for w in REVIEW_WORDING if _find_ci(text_all, w)), None)
        signals["review_wording"] = {"matched": bool(review_hit), "raw_fragment": review_hit}

        reasons = [k for k, v in signals.items() if v["matched"]]

        if sponsor_hit:
            category = "confirmed_sponsored"
        elif affiliate_match:
            category = "affiliate"
        elif partner_hit:
            category = "partner_content"
        elif review_hit:
            category = "editorial_review"
        else:
            category = "organic_mention"

        confidence = _CATEGORY_CONFIDENCE.get(category, 0.3)
        return ArticleClassification(category=category, confidence=confidence, signals=signals,
                                      has_brand_evidence=True, reasons=reasons)
