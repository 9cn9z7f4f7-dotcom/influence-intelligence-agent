"""
Общие вычислительные примитивы, используемые несколькими аналитическими
слоями (Competitor DNA, Next Move). Здесь нет LLM и нет побочных эффектов -
чистые функции над списками Integration/Creator.
"""
from __future__ import annotations

from collections import Counter, defaultdict

from app.models import Creator, Integration
from config.settings import Settings


def aggregate_integrations(integrations: list[Integration], creators_by_id: dict[str, Creator],
                            settings: Settings) -> dict[str, Counter]:
    platform = Counter()
    topic = Counter()
    size = Counter()
    views_bucket = Counter()
    content_type = Counter()
    offer = Counter()
    mechanic = Counter()
    creator_repeat = Counter()

    for i in integrations:
        if i.platform:
            platform[i.platform] += 1
        if i.content_type:
            content_type[i.content_type] += 1
        if i.detected_offer:
            offer[i.detected_offer] += 1
        if i.detected_mechanic:
            mechanic[i.detected_mechanic] += 1
        creator_repeat[i.creator_id] += 1

        creator = creators_by_id.get(i.creator_id)
        if creator:
            if creator.topic_tags:
                topic[creator.topic_tags[0]] += 1
            bucket = settings.bucket_for_value(creator.followers, settings.follower_buckets)
            if bucket:
                size[bucket] += 1
            vb = settings.bucket_for_value(creator.avg_views, settings.views_buckets)
            if vb:
                views_bucket[vb] += 1

    return {
        "platform": platform, "topic": topic, "size": size, "views_bucket": views_bucket,
        "content_type": content_type, "offer": offer, "mechanic": mechanic,
        "creator_repeat": creator_repeat,
    }


def top_key(counter: Counter) -> str | None:
    if not counter:
        return None
    return counter.most_common(1)[0][0]


def creator_content_type_profile(integrations: list[Integration]) -> dict[str, str]:
    """Для каждого creator_id - самый частый content_type среди ВСЕХ его интеграций
    (по любому конкуренту). Нужно для Next Move, так как content_type - атрибут
    интеграции, а не самого креатора."""
    per_creator: dict[str, Counter] = defaultdict(Counter)
    for i in integrations:
        if i.content_type:
            per_creator[i.creator_id][i.content_type] += 1
    return {cid: counter.most_common(1)[0][0] for cid, counter in per_creator.items() if counter}
