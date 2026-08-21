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
from datetime import date, datetime, timedelta, timezone

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
    "{name} обзор", "{name} статья", "{name} блог", "{name} новости",
    "{name} партнерский материал", "{name} реклама", "{name} промокод обзор",
]
ARTICLE_QUERY_TEMPLATES_EN = [
    "{name} review", "{name} article", "{name} blog", "{name} news",
    "{name} partner article", "{name} sponsored article", "{name} promo code review",
]
# Всегда добавляем "sponsored" отдельно (раздел 5 - явный пример запроса) -
# независимо от определённого языка бренда, т.к. рекламные disclosure на
# английском встречаются и на русскоязычных сайтах.
ARTICLE_QUERY_SPONSORED_SUFFIX = "sponsored"

MAX_ARTICLE_QUERIES_DEFAULT = 12


def _topic_text(value: str) -> str:
    return (value or "").strip().replace("_", " ").replace("-", " ")


def _date_query_suffix(
    date_range: str | None = None,
    custom_start: date | None = None,
    custom_end: date | None = None,
    now: datetime | None = None,
) -> str:
    """Best-effort discovery constraint. Final date filtering still happens in pipeline.

    Google/SerpAPI understand after:/before: directly; Tavily also benefits from
    explicit date language in the query even when it does not enforce operators.
    """
    today = (now or datetime.now(timezone.utc)).date()
    if date_range == "custom" and custom_start and custom_end:
        return f" after:{custom_start.isoformat()} before:{(custom_end + timedelta(days=1)).isoformat()}"
    days = {"7d": 7, "30d": 30, "90d": 90}.get(date_range or "")
    if not days:
        return ""
    start = today - timedelta(days=days)
    return f" after:{start.isoformat()} before:{(today + timedelta(days=1)).isoformat()}"


def apply_search_constraints(
    query: str,
    *,
    exclude_topics: list[str] | None = None,
    date_range: str | None = None,
    custom_start: date | None = None,
    custom_end: date | None = None,
) -> str:
    """Apply cheap discovery-time constraints without changing provider APIs."""
    negatives = "".join(f' -"{_topic_text(t)}"' for t in (exclude_topics or []) if _topic_text(t))
    return f"{query}{negatives}{_date_query_suffix(date_range, custom_start, custom_end)}".strip()


def generate_article_queries(
    brand_name: str, aliases: list[str] | None = None, max_queries: int = MAX_ARTICLE_QUERIES_DEFAULT,
    *, include_topics: list[str] | None = None, exclude_topics: list[str] | None = None,
    date_range: str | None = None, custom_start: date | None = None, custom_end: date | None = None,
) -> list[str]:
    """Generate article discovery queries that honour advanced search settings.

    User topics influence discovery first; the pipeline still re-checks the
    returned content afterwards.  We keep broad brand queries as a fallback so
    a too-narrow topic does not collapse the whole sample to zero.
    """
    brand_name = (brand_name or "").strip()
    if not brand_name:
        return []
    names = [brand_name] + [a.strip() for a in (aliases or []) if a.strip() and a.strip().lower() != brand_name.lower()]

    is_cyrillic = bool(_CYRILLIC_RE.search(brand_name))
    primary_templates = ARTICLE_QUERY_TEMPLATES_RU if is_cyrillic else ARTICLE_QUERY_TEMPLATES_EN
    secondary_templates = ARTICLE_QUERY_TEMPLATES_EN if is_cyrillic else ARTICLE_QUERY_TEMPLATES_RU
    topics = [_topic_text(t) for t in (include_topics or []) if _topic_text(t)]

    queries: list[str] = []
    for name in names:
        # Topic-aware queries go first because they are the user's explicit intent.
        # For Latin brand names we still add Russian article/news formulations: this
        # is important for brands like Nike, Adidas, Apple etc. in a RU search context.
        for topic in topics[:4]:
            queries.extend([
                f"{name} {topic} article", f"{name} {topic} review",
                f"{name} {topic} статья", f"{name} {topic} новости",
            ])

        # Broad article/news queries in BOTH languages. Search provider may rank
        # either language better depending on the current web index / geography.
        preferred = [
            f"{name} article", f"{name} news", f"{name} review",
            f"{name} статьи", f"{name} новости", f"{name} обзор",
            f"{name} sponsored article", f"{name} партнерский материал",
        ]
        queries.extend(preferred)
        for template in primary_templates[:4]:
            queries.append(template.format(name=name))
        for template in secondary_templates[:2]:
            queries.append(template.format(name=name))
        queries.append(f"{name} {ARTICLE_QUERY_SPONSORED_SUFFIX}")

    seen: set[str] = set()
    unique: list[str] = []
    for raw in queries:
        q = apply_search_constraints(
            raw, exclude_topics=exclude_topics, date_range=date_range,
            custom_start=custom_start, custom_end=custom_end,
        )
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

    # Explicit user topics may be more specific than our taxonomy (e.g.
    # "running", "streetwear"). Do not throw them away: use generic creator
    # discovery templates for unknown topics. Observed automatic topics remain
    # taxonomy-driven.
    normalized_excluded = {_topic_text(t) for t in exclude_topics}
    cleaned: list[str] = []
    for t in seeds:
        topic = _topic_text(t)
        if not topic or topic == "other" or topic in normalized_excluded:
            continue
        if include_topics or t in TAXONOMY:
            cleaned.append(t)
    seeds = cleaned
    if not seeds:
        seeds = [t for t in GENERIC_FALLBACK_TOPICS if t not in exclude_topics] or ["lifestyle"]

    queries: list[str] = []
    for topic in seeds:
        if topic in TOPIC_QUERY_TEMPLATES:
            queries.extend(TOPIC_QUERY_TEMPLATES[topic])
        else:
            text = _topic_text(topic)
            queries.extend([f"{text} creator", f"{text} review", f"{text} blogger"])

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(q)
    return unique[:max_queries]
