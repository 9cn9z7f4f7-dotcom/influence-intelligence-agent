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


# ---------------------------------------------------------------------------
# Комбинирование DOM/API-детектора с VisualEvidenceEnricher (раздел 2, 3, 17
# real-data требований).
# ---------------------------------------------------------------------------

# Только эти категории имеют смысл эскалировать к screenshot+vision (раздел 3):
# "не делать screenshot каждой страницы" - только когда DOM/API уже нашёл brand
# evidence, но не уверен насчёт commercial evidence.
VISUAL_ESCALATION_CATEGORIES = {"manual_review"}

DEFAULT_VISUAL_CONFIDENCE_WEIGHT = 0.25


def should_escalate_to_visual_evidence(category: str) -> bool:
    """True, если стоит вызвать screenshot+vision для этого детектора-результата.

    confirmed - уже уверенно подтверждено DOM/API, vision не нужен.
    rejected - нет даже brand evidence, vision не поможет (см. combine_dom_and_visual).
    organic_mention - можно оставить как есть (не commercial), vision не обязателен для MVP.
    manual_review - ЕДИНСТВЕННЫЙ случай, где vision может реально изменить решение.
    """
    return category in VISUAL_ESCALATION_CATEGORIES


def combine_dom_and_visual(
    category: str, confidence: float, visual_commercial_signal_visible: bool,
    visual_confidence: float, threshold: float,
    visual_weight: float = DEFAULT_VISUAL_CONFIDENCE_WEIGHT,
) -> tuple[str, float, bool]:
    """Комбинирует детерминированный DOM/API результат с visual evidence.

    ЖЁСТКОЕ ПРАВИЛО (раздел 2, 17): visual evidence НИКОГДА не создаёт brand
    evidence с нуля. Если DOM/API вообще не нашёл brand evidence (category ==
    "rejected") - результат остаётся "rejected", что бы ни "увидела" vision-модель
    на скриншоте (иначе один логотип на фоне мог бы "создать" интеграцию без
    единого текстового/структурного упоминания бренда - недопустимо).

    Если DOM/API нашёл brand evidence, но НЕ commercial evidence
    (category == "manual_review") - visual commercial signal может добавить вес
    и поднять итоговую категорию до "confirmed", если суммарный confidence
    дойдёт до threshold. Иначе остаётся manual_review (менее уверенно, чем
    "точно интеграция", но и не отбрасывается).

    Возвращает (new_category, new_confidence, used_visual_evidence).
    """
    if category != "manual_review":
        return category, confidence, False
    if not visual_commercial_signal_visible:
        return category, confidence, False

    bonus = round(min(visual_weight, visual_weight * max(0.0, min(1.0, visual_confidence))), 3)
    new_confidence = round(min(1.0, confidence + bonus), 3)
    new_category = "confirmed" if new_confidence >= threshold else "manual_review"
    return new_category, new_confidence, True


# ---------------------------------------------------------------------------
# Раздел 1+2 доработки: hard commercial signals + potential creators.
#
# "Убрать confidence как порог confirmed" - если найден хотя бы ОДИН
# однозначный hard commercial signal (app/hard_signals.py), категория
# становится "confirmed" НЕЗАВИСИМО от aggregate confidence. confidence
# остаётся значимым порогом только для VISUAL_AI/AI_INFERENCE/ambiguous
# случаев (см. should_escalate_to_visual_evidence/combine_dom_and_visual выше -
# они не меняются этой доработкой).
#
# ЖЁСТКОЕ ПРАВИЛО (тот же принцип, что и для visual evidence): hard signal
# НИКОГДА не создаёт confirmed из "rejected" - has_brand_evidence должен уже
# быть True из обычного текстового/URL детектора. Иначе один promo-код в
# видео вообще не про наш бренд мог бы "создать" интеграцию - недопустимо.
# ---------------------------------------------------------------------------

# Категории, для которых hard signal может поднять до "confirmed" - "rejected"
# сюда осознанно не входит (см. правило выше).
HARD_SIGNAL_ESCALATABLE_CATEGORIES = {"manual_review", "organic_mention", "potential_creator"}

# Категории, для которых, если hard signal НЕ найден, но есть organic brand
# affinity (app/potential_creator.py), можно поднять до "potential_creator" -
# manual_review осознанно НЕ входит (там уже есть свой ambiguous-commercial
# сигнал, который заслуживает отдельного review, а не "potential creator").
AFFINITY_ESCALATABLE_CATEGORIES = {"organic_mention"}


def escalate_with_hard_signals(category: str, has_brand_evidence: bool, hard_signal_matched: bool) -> str:
    """category уже посчитана обычным (текстовым/URL) детектором. Если
    has_brand_evidence=False (category=="rejected") - hard signal ничего не
    меняет. Иначе - hard_signal_matched поднимает "manual_review"/
    "organic_mention"/"potential_creator" до "confirmed"; "confirmed" остаётся
    "confirmed" (не понижается)."""
    if not has_brand_evidence:
        return category
    if category == "confirmed":
        return category
    if hard_signal_matched and category in HARD_SIGNAL_ESCALATABLE_CATEGORIES:
        return "confirmed"
    return category


def escalate_with_affinity(category: str, has_brand_evidence: bool, affinity_signals: list[str]) -> str:
    """Если ни hard signal, ни higher category не сработали, но найдена
    органическая brand affinity (раздел 2) - "organic_mention" повышается до
    "potential_creator" (НЕ confirmed - это принципиально другая, некоммерческая
    категория, см. app/potential_creator.py)."""
    if not has_brand_evidence:
        return category
    if affinity_signals and category in AFFINITY_ESCALATABLE_CATEGORIES:
        return "potential_creator"
    return category
