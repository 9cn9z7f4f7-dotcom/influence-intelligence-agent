"""
Creator Universe (раздел 9 требований).

Next Move НЕ должен строиться из "остальных креаторов, найденных в
интеграциях этого же конкурента" - это не независимая выборка, а просто
подмножество уже известных бренду данных. Creator Universe - это отдельный
кандидат-пул, построенный по platform/topic/size критериям (topic-driven
поиск, а не поиск по имени бренда/конкурента), из которого затем вычитаются
креаторы, уже использованные брендом:

    next_move_candidates = creator_universe MINUS creators_used_by_brand

Сейчас live-построение universe реализовано только для YouTube (единственная
live-платформа, раздел 3) - для Instagram/TikTok universe остаётся пустым
с явным honest note, а не имитацией данных.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.analysis.models import AnalysisConfig
from app.ingestion.live_youtube import discover_videos
from app.models import Creator
from app.platforms.youtube import YouTubePlatformAdapter
from app.query_generator import generate_discovery_queries
from app.runtime_budget import budget_exhausted

MAX_QUERIES_PER_UNIVERSE = 2
MAX_RESULTS_PER_QUERY = 10


@dataclass
class CreatorUniverse:
    creators: list[Creator] = field(default_factory=list)
    queries_used: list[str] = field(default_factory=list)
    status: str = "ok"  # ok | degraded | unavailable
    notes: list[str] = field(default_factory=list)


def build_creator_universe(config: AnalysisConfig, observed_topics: Optional[list[str]] = None,
                            platform_adapter: Optional[YouTubePlatformAdapter] = None) -> CreatorUniverse:
    """Строит независимый пул креаторов по DYNAMIC topic/size критериям
    (см. app/query_generator.py) - НЕ по захардкоженной default-теме.

    observed_topics - темы, реально наблюдаемые в найденном контенте бренда
    (используются как discovery seeds, если пользователь не задал include_topics).
    """
    adapter = platform_adapter or YouTubePlatformAdapter()
    universe = CreatorUniverse()

    if not adapter.adapter.is_available():
        universe.status = "unavailable"
        universe.notes.append("YOUTUBE_API_KEY не задан - creator universe для YouTube недоступен")
        return universe

    queries = generate_discovery_queries(
        observed_topics=observed_topics, include_topics=config.include_topics,
        exclude_topics=config.exclude_topics, platform="youtube", max_queries=MAX_QUERIES_PER_UNIVERSE,
    )
    discovery = discover_videos(adapter.adapter, queries, max_results_per_query=MAX_RESULTS_PER_QUERY)
    universe.queries_used = discovery.queries_run

    if discovery.quota_exceeded and not discovery.videos:
        universe.status = "unavailable"
        universe.notes.append("YouTube API квота превышена во время построения creator universe")
        return universe
    if discovery.queries_failed or discovery.quota_exceeded:
        universe.status = "degraded"
        universe.notes.append(f"часть topic-запросов не выполнена: {discovery.queries_failed}")

    seen_channel_ids: set[str] = set()
    creators: list[Creator] = []
    max_creators = config.max_creators or 200

    for video in discovery.videos:
        if budget_exhausted(20):
            universe.status = "degraded"
            universe.notes.append("Общий лимит анализа достигнут во время enrichment авторов")
            break
        if len(creators) >= max_creators:
            break
        channel_id = (video.get("snippet") or {}).get("channelId")
        if not channel_id or channel_id in seen_channel_ids:
            continue
        seen_channel_ids.add(channel_id)

        creator = adapter.extract_creator(video)
        if not creator:
            continue
        if not config.matches_followers(creator.followers):
            continue
        creators.append(creator)

    universe.creators = creators
    return universe


def next_move_candidate_pool(universe: CreatorUniverse, used_creator_ids: set[str]) -> list[Creator]:
    """next_move_candidates = creator_universe MINUS уже использованные брендом креаторы."""
    return [c for c in universe.creators if c.creator_id not in used_creator_ids]
