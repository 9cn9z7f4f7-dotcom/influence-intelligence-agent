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
from urllib.parse import parse_qs, urlparse

from app.analysis.models import AnalysisConfig
from app.ingestion.live_youtube import discover_videos
from app.models import Creator
from app.platforms.youtube import YouTubePlatformAdapter
from app.query_generator import generate_discovery_queries, apply_search_constraints
from app.runtime_budget import budget_exhausted
from app.topic_classifier import classify_topic
from app.search_client import get_default_search_client
from app.ingestion.youtube_adapter import YouTubeAdapter
from config.settings import settings as default_settings

MAX_QUERIES_PER_UNIVERSE = 2
MAX_RESULTS_PER_QUERY = 10
TARGET_HUNTING_CREATORS = 15
MAX_WEB_UNIVERSE_QUERIES = 3
MAX_WEB_RESULTS_PER_QUERY = 8


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


def _youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0] or None
    if host in {"youtube.com", "m.youtube.com"}:
        return (parse_qs(parsed.query).get("v") or [None])[0]
    return None



def _matches_hunting_topics(text: str, topics: list[str]) -> bool:
    """Reject obvious off-topic web-search noise before it becomes a hunter."""
    clean = [str(t).strip().lower().replace("_", " ") for t in topics if t and t != "other"]
    if not clean:
        return True
    lowered = (text or "").lower()
    if any(topic in lowered for topic in clean if len(topic) >= 4):
        return True
    classified = classify_topic(text or "", use_llm_for_ambiguous=False)
    return bool(set(classified.topic_tags) & {t.replace(" ", "_") for t in clean})

def expand_creator_universe_web(
    creators: list[Creator], observed_topics: Optional[list[str]] = None,
    target: int = TARGET_HUNTING_CREATORS, adapter: YouTubeAdapter | None = None,
    config: AnalysisConfig | None = None,
) -> tuple[list[Creator], list[str], list[str]]:
    """Bounded Tavily/SerpAPI fallback for hunting supply.

    Uses web search for discovery and only non-search YouTube endpoints for
    identity/metrics enrichment. It never invents a creator from a video title.
    """
    if len(creators) >= target or budget_exhausted(35):
        return creators, [], []
    yt = adapter or YouTubeAdapter()
    client = get_default_search_client(default_settings)
    if not client.is_available() or not yt.is_available():
        return creators, [], []

    requested_topics = list((config.include_topics if config else []) or [])
    topics = [t for t in (requested_topics or observed_topics or []) if t and t != "other"][:3]
    if not topics:
        topics = ["lifestyle", "entertainment"]
    queries = [f"site:youtube.com {str(topic).replace('_', ' ')} creator review" for topic in topics]
    if config:
        queries = [
            apply_search_constraints(
                q, exclude_topics=config.exclude_topics, date_range=config.date_range,
                custom_start=config.custom_start, custom_end=config.custom_end,
            )
            for q in queries
        ]
    queries = queries[:MAX_WEB_UNIVERSE_QUERIES]
    seen_ids = {c.creator_id for c in creators}
    added = list(creators)
    used: list[str] = []
    notes: list[str] = []

    for query in queries:
        if len(added) >= target or budget_exhausted(25):
            break
        try:
            results = client.search(query, max_results=MAX_WEB_RESULTS_PER_QUERY)
            used.append(query)
        except Exception as exc:  # best effort; never fail analysis
            notes.append(f"web creator discovery: {type(exc).__name__}")
            continue
        for result in results:
            if len(added) >= target or budget_exhausted(18):
                break
            video_id = _youtube_video_id(result.url)
            if not video_id:
                continue
            # Search providers occasionally return a globally popular but
            # irrelevant video. Do not let that pollute the hunting universe.
            search_text = " ".join(filter(None, [getattr(result, "title", None), getattr(result, "snippet", None), getattr(result, "content", None)]))
            if search_text.strip() and not _matches_hunting_topics(search_text, topics):
                continue
            try:
                video = yt._run_with_retries(yt.get_video_stats, video_id)
            except Exception:
                video = None
            snippet = (video or {}).get("snippet", {}) or {}
            video_text = f"{snippet.get('title') or ''} {snippet.get('description') or ''}"
            if video_text.strip() and not _matches_hunting_topics(video_text, topics):
                continue
            channel_id = snippet.get("channelId")
            if not channel_id:
                continue
            creator_id = f"yt_{channel_id}"
            if creator_id in seen_ids:
                continue
            try:
                channel = yt._run_with_retries(yt.get_channel_stats, channel_id)
            except Exception:
                channel = None
            if channel:
                creator = YouTubeAdapter.channel_to_creator(channel)
            else:
                title = (snippet.get("channelTitle") or "").strip()
                if not title:
                    continue
                creator = Creator(
                    creator_id=creator_id, name=title, platform="youtube",
                    canonical_url=f"https://www.youtube.com/channel/{channel_id}",
                    source_refs=[f"https://www.youtube.com/channel/{channel_id}", result.url],
                    is_synthetic=False, source_mode="live",
                )
            creator.source_refs = list(dict.fromkeys([*creator.source_refs, result.url]))
            seen_ids.add(creator.creator_id)
            added.append(creator)
    return added, used, notes
