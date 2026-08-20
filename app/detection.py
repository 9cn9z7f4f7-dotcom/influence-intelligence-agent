"""
Общая (platform-agnostic) логика категоризации интеграций.

Раздел 10 требований: категория интеграции определяется тем, какие ГРУППЫ
сигналов сработали, а не единственным числом confidence:

    confirmed        - brand evidence И commercial evidence одновременно,
                        суммарный confidence >= порога.
    manual_review    - brand evidence И commercial evidence есть, но
                        confidence ниже порога (неопределённый случай).
    organic_mention  - только brand evidence, НИ ОДНОГО коммерческого сигнала -
                        никогда не считается подтверждённой интеграцией.
    rejected         - нет даже brand evidence - видео/пост не про наш бренд.

Любой платформенный детектор (YouTube/Instagram/TikTok) должен различать
сигналы именно так - см. app/ingestion/live_youtube.py::IntegrationDetector
и будущие app/platforms/*.py адаптеры, которые переиспользуют эти константы.
"""
from __future__ import annotations

# Названия сигналов, которые говорят "этот контент про наш бренд".
BRAND_EVIDENCE_SIGNALS = {"brand_in_title", "brand_in_description", "alias_match", "repeated_mention"}

# Названия сигналов, которые говорят "здесь есть коммерческий/рекламный элемент".
COMMERCIAL_EVIDENCE_SIGNALS = {"promo_code", "brand_url", "cta_phrase", "sponsor_wording"}


def categorize_signals(signals: dict[str, dict], confidence: float, threshold: float) -> tuple[str, bool, bool]:
    """Возвращает (category, has_brand_evidence, has_commercial_evidence).

    signals: словарь signal_name -> {"matched": bool, ...} (см. DetectorResult.signals).
    """
    has_brand_evidence = any(
        sig.get("matched") for name, sig in signals.items() if name in BRAND_EVIDENCE_SIGNALS
    )
    has_commercial_evidence = any(
        sig.get("matched") for name, sig in signals.items() if name in COMMERCIAL_EVIDENCE_SIGNALS
    )

    if not has_brand_evidence:
        category = "rejected"
    elif has_commercial_evidence:
        category = "confirmed" if confidence >= threshold else "manual_review"
    else:
        category = "organic_mention"

    return category, has_brand_evidence, has_commercial_evidence
