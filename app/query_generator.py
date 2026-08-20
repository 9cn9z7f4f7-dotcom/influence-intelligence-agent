"""
DynamicQueryGenerator (hotfix раздел 1) - Creator Universe discovery queries
НЕ должны быть захардкожены под одну вертикаль (образование/студенты).

input:  observed_topics (из уже найденного контента бренда), include_topics,
        exclude_topics, platform
output: discovery queries (topic-driven, НЕ brand-specific)

Приоритет seeds: include_topics (если задан пользователем) -> observed_topics
(из реально найденных интеграций/креаторов бренда) -> нейтральный generic
fallback (если вообще ничего не известно - например, live недоступен).
Никакого LLM здесь не требуется - template-based, но интерфейс не мешает
подключить LLM позже (не в рамках этого hotfix).
"""
from __future__ import annotations

import re

from app.topic_classifier import TAXONOMY

# Topic -> нейтральные (НЕ brand-specific) query-шаблоны для discovery.
# Ключевое требование hotfix: для разных тем queries реально разные -
# Nike (sports/fitness) не должен получать education/student запросы и наоборот.
TOPIC_QUERY_TEMPLATES: dict[str, list[str]] = {
    "education": ["education blog", "онлайн курсы обзор"],
    "student": ["student vlog", "студенческая жизнь блог"],
    "beauty": ["beauty blog", "makeup обзор"],
    "fashion": ["fashion blog", "мода блог"],
    "fitness": ["fitness blog", "workout блог"],
    "sports": ["sports blog", "sneakers обзор", "running blog"],
    "food": ["food blog", "рецепты блог"],
    "travel": ["travel blog", "путешествия блог"],
    "finance": ["finance blog", "финансовая грамотность блог"],
    "tech": ["tech blog", "гаджеты обзор"],
    "gaming": ["gaming blog", "игры стрим"],
    "career": ["career blog", "карьера блог"],
    "health": ["health blog", "медицина блог"],
    "parenting": ["parenting blog", "мама блог"],
    "automotive": ["automotive blog", "авто обзор"],
    "entertainment": ["entertainment blog", "юмор блог"],
    "lifestyle": ["lifestyle blog", "лайфстайл блог"],
}

# Нейтральный fallback, если и include_topics, и observed_topics пусты
# (например, live недоступен и по бренду вообще ничего не найдено) -
# НЕ education-specific default, максимально общий.
GENERIC_FALLBACK_TOPICS = ["lifestyle", "entertainment"]

MAX_QUERIES_DEFAULT = 12


# ---------------------------------------------------------------------------
# Articles/Web discovery queries (раздел 5 требований).
#
# Не привязано к одной вертикали/языку - если имя бренда содержит кириллицу,
# используем русские шаблоны (плюс "sponsored" - явно указан в разделе 5 как
# обязательный кросс-языковой запрос); иначе - английские.
# ---------------------------------------------------------------------------
_CYRILLIC_RE = re.compile(r"[а-яА-ЯёЁ]")

ARTICLE_QUERY_TEMPLATES_RU = [
    "{name}", "{name} обзор", "{name} промокод", "{name} партнер", "{name} реклама", "{name} скидка",
    "где купить {name}",
]
ARTICLE_QUERY_TEMPLATES_EN = [
    "{name}", "{name} review", "{name} promo code", "{name} partner", "{name} discount",
    "where to buy {name}",
]
# Всегда добавляем "sponsored" отдельно (раздел 5 - явный пример запроса) -
# независимо от определённого языка бренда, т.к. рекламные disclosure на
# английском встречаются и на русскоязычных сайтах.
ARTICLE_QUERY_SPONSORED_SUFFIX = "sponsored"

MAX_ARTICLE_QUERIES_DEFAULT = 12


def generate_article_queries(
    brand_name: str, aliases: list[str] | None = None, max_queries: int = MAX_ARTICLE_QUERIES_DEFAULT,
) -> list[str]:
    """Динамические (НЕ захардкоженные под одну вертикаль/язык) discovery-запросы
    для Articles/Web платформы (раздел 5 требований)."""
    brand_name = (brand_name or "").strip()
    if not brand_name:
        return []
    names = [brand_name] + [a.strip() for a in (aliases or []) if a.strip() and a.strip().lower() != brand_name.lower()]

    is_cyrillic = bool(_CYRILLIC_RE.search(brand_name))
    templates = ARTICLE_QUERY_TEMPLATES_RU if is_cyrillic else ARTICLE_QUERY_TEMPLATES_EN

    queries: list[str] = []
    for name in names:
        for template in templates:
            queries.append(template.format(name=name))
        queries.append(f"{name} {ARTICLE_QUERY_SPONSORED_SUFFIX}")

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:max_queries]


def generate_discovery_queries(
    observed_topics: list[str] | None = None,
    include_topics: list[str] | None = None,
    exclude_topics: list[str] | None = None,
    platform: str | None = None,  # зарезервировано для platform-специфичных шаблонов в будущем
    max_queries: int = MAX_QUERIES_DEFAULT,
) -> list[str]:
    include_topics = [t for t in (include_topics or []) if t]
    observed_topics = [t for t in (observed_topics or []) if t]
    exclude_topics = set(exclude_topics or [])

    if include_topics:
        seeds = include_topics
    elif observed_topics:
        seeds = observed_topics
    else:
        seeds = GENERIC_FALLBACK_TOPICS

    seeds = [t for t in seeds if t in TAXONOMY and t not in exclude_topics and t != "other"]
    if not seeds:
        seeds = [t for t in GENERIC_FALLBACK_TOPICS if t not in exclude_topics] or ["lifestyle"]

    queries: list[str] = []
    for topic in seeds:
        queries.extend(TOPIC_QUERY_TEMPLATES.get(topic, []))

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:max_queries]
