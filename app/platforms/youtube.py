"""
YouTube - единственная платформа, где live-discovery официально разрешён
и реализован через YouTube Data API v3 (см. app/ingestion/live_youtube.py и
app/ingestion/youtube_adapter.py, которые этот адаптер оборачивает без
изменения их внутренней логики - она уже покрыта тестами).
"""
from __future__ import annotations

from typing import Optional

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.ingestion.live_youtube import (
    CompetitorQueryBuilder,
    IntegrationDetector,
    build_creator_from_channel,
    discover_videos,
)
from app.ingestion.youtube_adapter import YouTubeAdapter, _parse_dt
from app.metrics_builder import compute_creator_metrics
from app.models import Creator, Integration
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult
from app.topic_classifier import classify_topic
from config.settings import Settings, settings as default_settings

RECENT_VIDEOS_FOR_METRICS = 5


class YouTubePlatformAdapter(PlatformAdapter):
    platform_name = "youtube"

    def __init__(self, adapter: YouTubeAdapter | None = None, settings: Settings | None = None) -> None:
        self.adapter = adapter or YouTubeAdapter()
        self.settings = settings or default_settings
        self.detector = IntegrationDetector(self.settings)

    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        if not self.adapter.is_available():
            return PlatformDiscoveryResult(
                platform="youtube",
                status="unavailable",
                source_mode="none",
                reason="YOUTUBE_API_KEY не задан - live discovery для YouTube недоступен",
                import_hint="manage.py import-integrations --file <csv|json> (platform=youtube)",
            )

        query_builder = CompetitorQueryBuilder(brand.canonical_name, aliases=brand.aliases)
        queries = query_builder.build_queries()
        discovery = discover_videos(self.adapter, queries, self.settings.live_max_results_per_query)

        if discovery.quota_exceeded and not discovery.videos:
            status = "unavailable"
        elif discovery.queries_failed or discovery.quota_exceeded:
            status = "degraded"
        else:
            status = "ok"

        reason = None
        if discovery.quota_exceeded:
            reason = "YouTube API квота/лимит превышен во время сбора"
        elif discovery.queries_failed:
            reason = f"часть запросов не выполнена: {discovery.queries_failed}"

        return PlatformDiscoveryResult(
            platform="youtube",
            status=status,
            source_mode="live",
            reason=reason,
            raw_items=discovery.videos,
            queries_run=discovery.queries_run,
        )

    def detect_integration(self, raw_item: dict, brand_terms: list[str]):
        snippet = raw_item.get("snippet", {}) or {}
        return self.detector.detect(snippet.get("title", ""), snippet.get("description", ""), brand_terms)

    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        channel_id = (raw_item.get("snippet") or {}).get("channelId")
        if not channel_id:
            return None
        try:
            channel_item = self.adapter._run_with_retries(self.adapter.get_channel_stats, channel_id)
        except Exception:  # noqa: BLE001 - недоступность канала не должна ронять discovery
            return None
        if not channel_item:
            return None

        # avg_views/median_views НЕ берём из одного видео (raw_item), которое вызвало
        # детекцию - собираем несколько последних видео канала и считаем честную
        # агрегацию (раздел 8 требований). Если это не удаётся - остаются None.
        creator = build_creator_from_channel(channel_item, video_stats=None)
        creator.avg_views = None
        creator.median_views = None

        try:
            recent_videos = self.adapter._run_with_retries(
                self.adapter.list_channel_recent_videos, channel_id, RECENT_VIDEOS_FOR_METRICS,
            )
        except Exception:  # noqa: BLE001 - метрики - best effort, не критичны для discovery
            recent_videos = []

        sample_items: list[dict] = []
        recent_texts: list[str] = []
        for video in recent_videos:
            video_id = (video.get("id") or {}).get("videoId")
            snippet = video.get("snippet", {}) or {}
            if snippet.get("title") or snippet.get("description"):
                recent_texts.append(f"{snippet.get('title', '')} {snippet.get('description', '')}")
            if not video_id:
                continue
            try:
                stats_item = self.adapter._run_with_retries(self.adapter.get_video_stats, video_id)
            except Exception:  # noqa: BLE001
                continue
            if not stats_item:
                continue
            stats = stats_item.get("statistics", {}) or {}
            views = stats.get("viewCount")
            published_at = _parse_dt((stats_item.get("snippet") or {}).get("publishedAt"))
            sample_items.append({
                "views": float(views) if views is not None else None,
                "published_at": published_at,
            })

        metrics = compute_creator_metrics(sample_items)
        creator.avg_views = metrics.avg_views
        creator.median_views = metrics.median_views

        # topic_tags определяются по НЕСКОЛЬКИМ последним публикациям (раздел 2
        # hotfix), а не по единственному видео, которое вызвало детекцию.
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
