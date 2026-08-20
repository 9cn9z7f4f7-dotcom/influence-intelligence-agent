"""
Детерминированный классификатор тематики (раздел 7 требований).

Фиксированная таксономия - никаких свободных/выдуманных тем:
    student_lifestyle, education, school, university, medical_students,
    exam_prep, productivity, career, finance, tech, entertainment,
    lifestyle, other.

Основной путь - keyword-scoring (deterministic, воспроизводимо, объяснимо
через topic_evidence). LLM используется ТОЛЬКО для явно неоднозначных
случаев (несколько тем с близким счётом и/или отсутствие сигнала) - и
только как вспомогательная подсказка: если LLM недоступна или упала,
используется deterministic top-candidate (или "other"), поведение
никогда не падает и никогда не блокирует пайплайн.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from config.settings import settings

TAXONOMY = [
    "education", "student", "beauty", "fashion", "fitness", "sports", "food", "travel",
    "finance", "tech", "gaming", "career", "health", "parenting", "automotive",
    "entertainment", "lifestyle", "other",
]

# Ключевые слова на русском и английском - НЕ исчерпывающе, широкая таксономия
# (не завязана на один вертикаль/индустрию - раздел 2 требований hotfix).
KEYWORDS: dict[str, list[str]] = {
    "education": ["образование", "учёба", "обучение", "курс", "лекция", "education", "онлайн-школа", "edtech",
                  "экзамен", "сессия", "егэ", "огэ", "зачёт", "exam prep", "школа", "университет", "вуз", "институт"],
    "student": ["студент", "студенческая жизнь", "общага", "общежитие", "student life", "студенчество", "student vlog"],
    "beauty": ["красота", "beauty", "макияж", "makeup", "skincare", "уход за кожей", "haircare", "косметика"],
    "fashion": ["мода", "fashion", "стиль", "одежда", "outfit", "стайлинг"],
    "fitness": ["фитнес", "тренировка", "workout", "gym", "спортзал", "fitness", "тренажёрный зал"],
    "sports": ["спорт", "sports", "sneakers", "кроссовки", "running", "бег", "футбол", "баскетбол", "марафон"],
    "food": ["еда", "food", "рецепт", "кухня", "cooking", "ресторан", "готовка"],
    "travel": ["путешествие", "travel", "поездка", "тур", "vlog travel", "туризм"],
    "finance": ["финансы", "деньги", "инвестиции", "кредит", "finance", "money", "скидка", "промокод", "cashback"],
    "tech": ["технологии", "гаджет", "software", "tech", "приложение", "программирование", "ai", "нейросеть"],
    "gaming": ["игра", "gaming", "game", "стрим", "esports", "геймер", "видеоигра"],
    "career": ["карьера", "работа", "резюме", "собеседование", "career", "job", "стажировка", "вакансия"],
    "health": ["здоровье", "health", "медицина", "wellness", "анатомия", "медик", "медицинский"],
    "parenting": ["родители", "дети", "parenting", "мама", "декрет", "воспитание", "материнство"],
    "automotive": ["авто", "машина", "car", "automotive", "тест-драйв", "автомобиль"],
    "entertainment": ["развлечение", "юмор", "шоу", "entertainment", "мемы", "прикол", "стендап"],
    "lifestyle": ["лайфстайл", "lifestyle", "быт", "vlog", "влог", "повседневная жизнь"],
}

# Порог "уверенного" совпадения: если топовый счёт ниже, тема неоднозначна.
CONFIDENT_SCORE_THRESHOLD = 1.0
# Если разница между топ-1 и топ-2 темами меньше этого значения - тоже неоднозначно.
AMBIGUITY_MARGIN = 0.5


@dataclass
class TopicClassification:
    topic_tags: list[str] = field(default_factory=list)  # обычно 1 основная тема (+ доп. при близких счётах)
    topic_confidence: float = 0.0
    topic_evidence: list[str] = field(default_factory=list)  # какие ключевые слова сработали
    is_ambiguous: bool = False
    used_llm: bool = False


def _score_text(text: str) -> dict[str, tuple[float, list[str]]]:
    text_lower = (text or "").lower()
    scores: dict[str, tuple[float, list[str]]] = {}
    for topic, keywords in KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text_lower]
        if hits:
            scores[topic] = (float(len(hits)), hits)
    return scores


def classify_topic(text: str, use_llm_for_ambiguous: bool = True) -> TopicClassification:
    """Детерминированно классифицирует свободный текст (title+description/raw_text)
    по фиксированной таксономии. LLM подключается только для неоднозначных случаев."""
    scores = _score_text(text)

    if not scores:
        return TopicClassification(topic_tags=["other"], topic_confidence=0.0, topic_evidence=[])

    ranked = sorted(scores.items(), key=lambda kv: kv[1][0], reverse=True)
    top_topic, (top_score, top_hits) = ranked[0]
    second_score = ranked[1][1][0] if len(ranked) > 1 else 0.0

    is_ambiguous = top_score < CONFIDENT_SCORE_THRESHOLD or (top_score - second_score) < AMBIGUITY_MARGIN

    if not is_ambiguous:
        confidence = min(1.0, 0.5 + 0.15 * top_score)
        return TopicClassification(
            topic_tags=[top_topic], topic_confidence=round(confidence, 3), topic_evidence=top_hits,
        )

    used_llm = False
    chosen_topic = top_topic
    chosen_hits = top_hits
    if use_llm_for_ambiguous and settings.anthropic_api_key:
        llm_choice = _ask_llm_for_ambiguous_topic(text, [t for t, _ in ranked[:4]])
        if llm_choice in TAXONOMY:
            chosen_topic = llm_choice
            chosen_hits = scores.get(llm_choice, (0.0, []))[1]
            used_llm = True

    confidence = 0.35 if not used_llm else 0.5  # неоднозначный случай - консервативный confidence
    return TopicClassification(
        topic_tags=[chosen_topic],
        topic_confidence=round(confidence, 3),
        topic_evidence=chosen_hits,
        is_ambiguous=True,
        used_llm=used_llm,
    )


def _ask_llm_for_ambiguous_topic(text: str, candidates: list[str]) -> str | None:
    """Возвращает одну тему из candidates, либо None если LLM недоступна/упала.

    Строгий guardrail: LLM может выбрать ТОЛЬКО из переданных candidates (все они
    уже входят в фиксированную таксономию) - не может придумать новую тему.
    """
    if not settings.anthropic_api_key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=32,
            system=(
                "Выбери ОДНУ тему из списка candidates, которая лучше всего описывает текст. "
                "Ответь ТОЛЬКО одним словом - точным названием темы из списка, без пояснений."
            ),
            messages=[{"role": "user", "content": f"candidates={candidates}\ntext={text[:500]}"}],
        )
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        answer = "".join(text_parts).strip().lower()
        return answer if answer in candidates else None
    except Exception:  # noqa: BLE001 - LLM недоступна/упала => вызывающий код использует deterministic top-candidate
        return None
