"""
YouTube - единственная платформа, где live-discovery официально разрешён
и реализован через YouTube Data API v3 (см. app/ingestion/live_youtube.py и
app/ingestion/youtube_adapter.py, которые этот адаптер оборачивает без
изменения их внутренней логики - она уже покрыта тестами).
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional
from urllib.parse import parse_qs, urlparse

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.brand_domain import BrandDomainProfile, build_brand_domain_profile_from_terms
from app.detection import escalate_with_affinity, escalate_with_hard_signals
from app.hard_signals import detect_hard_commercial_signals
from app.ingestion.live_youtube import (
    CompetitorQueryBuilder,
    IntegrationDetector,
    build_creator_from_channel,
    discover_videos,
)
from app.ingestion.youtube_adapter import YouTubeAdapter, _parse_dt
from app.ingestion.identifiers import stable_id
from app.links_extractor import classify_links, extract_links
from app.metrics_builder import compute_creator_metrics
from app.models import Creator, Integration, SourceMode
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult
from app.potential_creator import detect_brand_affinity_signals
from app.search_client import get_default_search_client
from app.runtime_budget import budget_exhausted
from app.topic_classifier import classify_topic
from config.settings import Settings, settings as default_settings

RECENT_VIDEOS_FOR_METRICS = 5
MAX_YOUTUBE_SEARCH_CALLS_BRAND = 3
YOUTUBE_WEB_FALLBACK_RESULTS = 8


class YouTubePlatformAdapter(PlatformAdapter):
    platform_name = "youtube"

    def __init__(self, adapter: YouTubeAdapter | None = None, settings: Settings | None = None) -> None:
        self.adapter = adapter or YouTubeAdapter()
        self.settings = settings or default_settings
        self.detector = IntegrationDetector(self.settings)
        self._domain_profile_cache: dict[tuple, BrandDomainProfile] = {}

    def _domain_profile(self, brand_terms: list[str]) -> BrandDomainProfile:
        key = tuple(brand_terms)
        if key not in self._domain_profile_cache:
            self._domain_profile_cache[key] = build_brand_domain_profile_from_terms(brand_terms)
        return self._domain_profile_cache[key]

    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        # Web search is the broad discovery layer; YouTube search.list is only a
        # bounded supplement. videos.list/channels.list remain enrichment calls.
        client = get_default_search_client(self.settings)
        web_provider = None
        web_items: list[dict] = []
        seen_video_ids: set[str] = set()
        web_queries = [
            f"site:youtube.com {brand.canonical_name}",
            f"site:youtube.com {brand.canonical_name} review",
            f"site:youtube.com {brand.canonical_name} sponsored",
            f"site:youtube.com {brand.canonical_name} promo",
            f"site:youtube.com {brand.canonical_name} product",
            f"site:youtube.com {brand.canonical_name} recommendation",
        ]

        if client.is_available():
            for query in web_queries:
                if budget_exhausted(45):
                    break
                try:
                    results = client.search(query, max_results=YOUTUBE_WEB_FALLBACK_RESULTS)
                    web_provider = getattr(client, "last_used_provider", None) or getattr(client, "source_name", None)
                except Exception:
                    continue
                for result in results:
                    parsed = urlparse(result.url)
                    host = parsed.netloc.lower().removeprefix("www.")
                    if host not in {"youtube.com", "m.youtube.com", "youtu.be"}:
                        continue
                    video_id = (parse_qs(parsed.query).get("v") or [None])[0]
                    if host == "youtu.be" and not video_id:
                        video_id = parsed.path.strip("/").split("/")[0] or None
                    if video_id and video_id in seen_video_ids:
                        continue
                    if video_id:
                        seen_video_ids.add(video_id)
                    snippet = {"title": result.title or "YouTube video", "description": result.content or result.snippet or ""}

                    # A watch result is content until videos.list resolves real
                    # channel identity. Never promote the video title to creator.
                    if video_id and self.adapter.is_available() and not budget_exhausted(25):
                        try:
                            video_item = self.adapter._run_with_retries(self.adapter.get_video_stats, video_id)
                        except Exception:
                            video_item = None
                        video_snippet = (video_item or {}).get("snippet", {}) or {}
                        if video_snippet.get("channelId"):
                            snippet["channelId"] = video_snippet["channelId"]
                        if video_snippet.get("channelTitle"):
                            snippet["channelTitle"] = video_snippet["channelTitle"]
                        if video_snippet.get("publishedAt"):
                            snippet["publishedAt"] = video_snippet["publishedAt"]
                        if video_snippet.get("description"):
                            snippet["description"] = video_snippet["description"]

                    web_items.append({
                        "id": {"videoId": video_id} if video_id else {},
                        "snippet": snippet,
                        "_web_source_url": result.url,
                        "_web_search_provider": web_provider,
                    })

        discovery = None
        if self.adapter.is_available() and not budget_exhausted(35):
            query_builder = CompetitorQueryBuilder(brand.canonical_name, aliases=brand.aliases)
            queries = query_builder.build_queries()[:MAX_YOUTUBE_SEARCH_CALLS_BRAND]
            discovery = discover_videos(self.adapter, queries, self.settings.live_max_results_per_query)
        else:
            from app.ingestion.live_youtube import DiscoveryResult
            discovery = DiscoveryResult()

        combined = list(web_items)
        for item in discovery.videos:
            video_id = (item.get("id") or {}).get("videoId")
            if video_id and video_id in seen_video_ids:
                continue
            if video_id:
                seen_video_ids.add(video_id)
            combined.append(item)

        if combined:
            status = "degraded" if discovery.quota_exceeded or discovery.queries_failed else "ok"
            source_mode = "live"
        elif not client.is_available() and not self.adapter.is_available():
            status = "unavailable"
            source_mode = "none"
        else:
            status = "degraded" if discovery.quota_exceeded or discovery.queries_failed else "ok"
            source_mode = "live"

        reason = None
        if discovery.quota_exceeded:
            reason = "Лимит YouTube Search исчерпан; широкое discovery продолжено через web search."
        elif discovery.queries_failed:
            reason = f"Часть YouTube API запросов не выполнена: {discovery.queries_failed}"
        elif not combined:
            if not self.adapter.is_available() and not client.is_available():
                reason = "YOUTUBE_API_KEY и web search provider не настроены; live YouTube discovery недоступен."
            else:
                reason = "По выбранному бренду YouTube-кандидаты не найдены."

        return PlatformDiscoveryResult(
            platform="youtube", status=status, source_mode=source_mode, reason=reason,
            raw_items=combined, queries_run=discovery.queries_run, search_provider=web_provider,
            candidate_count=len(combined), accepted_count=len(combined),
            import_hint=(None if source_mode == "live" else "manage.py import-integrations --file <csv|json> (platform=youtube)"),
        )

    def detect_integration(self, raw_item: dict, brand_terms: list[str]):
        """Раздел 1/2/8/9 доработки: сначала обычный текстовый/URL детектор
        (app.ingestion.live_youtube.IntegrationDetector - НЕ изменён), затем два
        дополнительных, чисто АДДИТИВНЫХ слоя:

          1. hard commercial signal (paid partnership/#ad/промокод/affiliate
             ссылка/CTA+brand URL/"амбассадор BRAND" и т.п., включая ссылки
             ИЗ description - раздел 8) - если найден хотя бы один, category
             поднимается до "confirmed" независимо от confidence (раздел 1).
          2. если hard signal не найден, но есть organic brand affinity
             ("ношу", "рекомендую" и т.п.) - category поднимается с
             "organic_mention" до "potential_creator" (раздел 2) - НЕ считается
             confirmed интеграцией.

        Оба слоя работают строго по правилу app.detection: не создают
        has_brand_evidence из ничего - применяются только когда базовый
        детектор его уже нашёл."""
        snippet = raw_item.get("snippet", {}) or {}
        title = snippet.get("title", "")
        description = snippet.get("description", "")
        base_result = self.detector.detect(title, description, brand_terms)

        text_all = f"{title} {description}"
        profile = self._domain_profile(brand_terms)
        links = classify_links(extract_links(description), profile)
        hard = detect_hard_commercial_signals(
            text_all, brand_name=brand_terms[0] if brand_terms else "",
            brand_aliases=brand_terms[1:] if len(brand_terms) > 1 else [], links=links,
        )

        new_category = escalate_with_hard_signals(base_result.category, base_result.has_brand_evidence, hard.matched)
        affinity_signals: list[str] = []
        if new_category == base_result.category:
            affinity_signals = detect_brand_affinity_signals(text_all, brand_terms)
            new_category = escalate_with_affinity(new_category, base_result.has_brand_evidence, affinity_signals)

        if new_category == base_result.category:
            return base_result

        merged_signals = dict(base_result.signals)
        for name, sig in hard.signals.items():
            if sig.get("matched"):
                merged_signals[f"hard:{name}"] = sig
        for phrase in affinity_signals:
            merged_signals[f"affinity:{phrase}"] = {"matched": True, "raw_fragment": phrase}

        return replace(
            base_result, category=new_category, signals=merged_signals,
            is_integration=(new_category == "confirmed"),
            reasons=list(dict.fromkeys(base_result.reasons + hard.reasons + [f"affinity:{p}" for p in affinity_signals])),
        )

    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        web_url = raw_item.get("_web_source_url")
        snippet = raw_item.get("snippet", {}) or {}
        channel_id = snippet.get("channelId")
        channel_title = (snippet.get("channelTitle") or "").strip()

        # A Tavily watch URL is a content candidate until a real channel identity
        # is resolved. The video title must never become creator.name.
        if web_url and not channel_id:
            return None

        if not channel_id:
            return None

        if web_url:
            channel_url = f"https://www.youtube.com/channel/{channel_id}"
            try:
                channel_item = self.adapter._run_with_retries(self.adapter.get_channel_stats, channel_id)
            except Exception:
                channel_item = None

            if channel_item:
                creator = build_creator_from_channel(channel_item, video_stats=None)
                creator.source_refs = list(dict.fromkeys([*creator.source_refs, web_url]))
            elif channel_title:
                topic_result = classify_topic(f"{snippet.get('title', '')} {snippet.get('description', '')}")
                creator = Creator(
                    creator_id=f"yt_{channel_id}", name=channel_title, canonical_url=channel_url,
                    platform="youtube", followers=None, avg_views=None, median_views=None,
                    topic_tags=topic_result.topic_tags, source_refs=[channel_url, web_url],
                    is_synthetic=False, source_mode=SourceMode.LIVE,
                )
            else:
                return None

            creator.canonical_url = channel_url
            creator.source_mode = SourceMode.LIVE
            return creator
        try:
            channel_item = self.adapter._run_with_retries(self.adapter.get_channel_stats, channel_id)
        except Exception:  # noqa: BLE001 - недоступность канала не должна ронять discovery
            return None
        if not channel_item:
            return None

        # avg_views/median_views come from several recent uploads, but the
        # upload list is fetched through playlistItems.list (not search.list).
        creator = build_creator_from_channel(channel_item, video_stats=None)
        creator.avg_views = None
        creator.median_views = None

        try:
            recent_videos = self.adapter._run_with_retries(
                self.adapter.list_channel_recent_videos, channel_id, RECENT_VIDEOS_FOR_METRICS,
            )
        except Exception:
            recent_videos = []

        sample_items: list[dict] = []
        recent_texts: list[str] = []
        for video in recent_videos:
            if budget_exhausted(20):
                break
            video_id = (video.get("id") or {}).get("videoId")
            snippet = video.get("snippet", {}) or {}
            if snippet.get("title") or snippet.get("description"):
                recent_texts.append(f"{snippet.get('title', '')} {snippet.get('description', '')}")
            if not video_id:
                continue
            try:
                stats_item = self.adapter._run_with_retries(self.adapter.get_video_stats, video_id)
            except Exception:
                continue
            if not stats_item:
                continue
            stats = stats_item.get("statistics", {}) or {}
            views = stats.get("viewCount")
            published_at = _parse_dt((stats_item.get("snippet") or {}).get("publishedAt"))
            sample_items.append({"views": float(views) if views is not None else None, "published_at": published_at})

        metrics = compute_creator_metrics(sample_items)
        creator.avg_views = metrics.avg_views
        creator.median_views = metrics.median_views
        trigger_snippet = raw_item.get("snippet", {}) or {}
        recent_texts.append(f"{trigger_snippet.get('title', '')} {trigger_snippet.get('description', '')}")
        topic_result = classify_topic(" ".join(recent_texts))
        creator.topic_tags = topic_result.topic_tags

        return creator

    def normalize_creator(self, creator: Creator) -> Creator:
        creator.platform = "youtube"
        return creator

    def normalize_integration(self, integration: Integration) -> Integration:
        integration.platform = "youtube"
        return integration
