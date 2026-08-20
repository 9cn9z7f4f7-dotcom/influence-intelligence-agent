"""
Guardrails против LLM-галлюцинаций и запрещённых формулировок (раздел 2 и
раздел 18.1 мастер-промпта: "где AI может галлюцинировать").

Даже если системный промпт LLM просит не писать "конкурент точно сделает X",
модель может это игнорировать. Этот модуль - последний рубеж защиты:
если LLM всё-таки вернула запрещённую формулировку, мы откатываемся на
детерминированный fallback, а не показываем недоказанное утверждение жюри.
"""
from __future__ import annotations

FORBIDDEN_PATTERNS = [
    "точно сделает", "точно будет", "точно сделают", "точно станет",
    "конкурент хочет", "они хотят", "обязательно сделает", "гарантированно",
    "100% сделает", "наверняка сделает",
    # Раздел 15 требований (новый user-flow) - обязательные замены формулировок,
    # которые преувеличивают полноту/уверенность наблюдаемой выборки:
    "весь рынок", "рынок свободен", "рынок пуст", "пойдёт к этому блогеру",
    "пойдёт к этому креатору", "конкурент пойдёт",
]

# Разрешённые/рекомендуемые замены (для справки при написании нового текста -
# сами по себе НЕ проверяются кодом, служат ориентиром):
#   "мы нашли весь рынок"                -> "в наблюдаемом creator universe"
#   "конкурент пойдёт к этому блогеру"   -> "creator соответствует наблюдаемому профилю закупки бренда"
#   "рынок свободен"                     -> "низкая конкурентная насыщенность в наблюдаемой выборке"

CAUTIOUS_MARKER = "стоит исследовать"
CONFIDENT_ACTION_MARKER = "нужно делать"


def contains_forbidden_claim(text: str | None) -> bool:
    if not text:
        return False
    lowered = text.lower()
    return any(p in lowered for p in FORBIDDEN_PATTERNS)


def sanitize_statement(candidate_text: str | None, fallback_text: str) -> str:
    """Возвращает candidate_text, если он не содержит запрещённых формулировок
    и не пустой; иначе - детерминированный fallback."""
    if not candidate_text or contains_forbidden_claim(candidate_text):
        return fallback_text
    return candidate_text


def enforce_confidence_wording(text: str, confidence: float, threshold: float) -> str:
    """Если confidence ниже порога, а текст всё равно звучит уверенно
    ('нужно делать' без 'стоит исследовать') - подстраховываемся припиской."""
    if confidence >= threshold:
        return text
    lowered = text.lower()
    if CAUTIOUS_MARKER in lowered:
        return text
    if CONFIDENT_ACTION_MARKER in lowered or "нужно" in lowered:
        return text + " (confidence ниже порога - стоит исследовать перед решительными действиями.)"
    return text
