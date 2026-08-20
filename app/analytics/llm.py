"""
Тонкая обёртка над LLM для смыслового анализа.

Жёсткое правило проекта: LLM НИКОГДА не считает числа и не придумывает
факты. Она получает только заранее посчитанные агрегаты + evidence_ids
и должна:
  - сформулировать наблюдаемый паттерн в разрешённых формулировках
    ("в наблюдаемой выборке чаще встречается...", "это может указывать на...");
  - вернуть confidence (0..1);
  - НЕ добавлять цифры, которых не было во входных агрегатах.

Если ANTHROPIC_API_KEY не задан или вызов упал - вызывающий код обязан
использовать deterministic fallback (см. competitor_dna.py). Эта функция
в таком случае возвращает None, а не бросает исключение выше.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from config.settings import settings

SYSTEM_PROMPT = (
    "Ты - аналитик influence-marketing разведки. Тебе дают ТОЛЬКО посчитанные "
    "агрегаты и evidence_ids, без сырых данных. Твоя задача - сформулировать "
    "наблюдаемый паттерн закупки на русском языке в разрешённых формулировках "
    "('в наблюдаемой выборке чаще встречается...', 'за последние N дней доля X "
    "выросла...', 'это может указывать на...'). "
    "ЗАПРЕЩЕНО: утверждать что конкурент 'точно сделает X' или 'хочет X', "
    "запрещено придумывать цифры, которых не было в агрегатах. "
    "Ответь СТРОГО в формате JSON-массива объектов "
    "{\"statement\": str, \"confidence\": float 0..1, \"supporting_metric_keys\": [str]}. "
    "confidence должен быть ниже 0.5, если меньше 2 подтверждающих метрик."
)


def _get_client():
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def generate_patterns(competitor_name: str, aggregates: dict[str, Any]) -> Optional[list[dict]]:
    """Возвращает список паттернов от LLM, либо None если LLM недоступна/упала."""
    client = _get_client()
    if client is None:
        return None

    user_payload = {
        "competitor": competitor_name,
        "aggregates": aggregates,
    }
    try:
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
        )
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        raw_text = "".join(text_parts).strip()
        # LLM иногда оборачивает JSON в ```json ... ``` - подчищаем
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        parsed = json.loads(raw_text)
        if not isinstance(parsed, list):
            return None
        return parsed
    except Exception:  # noqa: BLE001 - любая ошибка LLM => graceful fallback выше
        return None


def generate_our_move_summary(opportunity: dict[str, Any]) -> Optional[str]:
    """Короткое actionable summary для одной гипотезы Our Move. None если LLM недоступна."""
    client = _get_client()
    if client is None:
        return None
    try:
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=256,
            system=(
                "Сформулируй короткое (1-2 предложения) actionable summary гипотезы на русском, "
                "используя только переданные факты. Не придумывай новых чисел. "
                "Если confidence ниже 0.55, используй формулировку 'стоит исследовать', а не 'нужно делать'."
            ),
            messages=[{"role": "user", "content": json.dumps(opportunity, ensure_ascii=False)}],
        )
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        return "".join(text_parts).strip() or None
    except Exception:  # noqa: BLE001
        return None
