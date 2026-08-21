"""
YouTube Data API v3 адаптер.

Использует YOUTUBE_API_KEY из .env. Если ключ не задан или API вернул
ошибку - адаптер не роняет pipeline, а помечает источник как
degraded/unavailable и возвращает пустой результат.

Обязательные способности (по мастер-промпту):
  - искать видео (search.list)
  - получать канал (channels.list)
  - публичную статистику канала (statistics)
  - статистику видео (videos.list statistics)
  - title / description / publishedAt
  - source URL
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from app.health import health_registry
from app.ingestion.base import BaseAdapter, IngestionResult
from app.models import Creator
from config.settings import settings

API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAdapter(BaseAdapter):
    source_name = "youtube"

    def __init__(self, api_key: str | None = None, timeout: float = 10.0) -> None:
        self.api_key = api_key if api_key is not None else settings.youtube_api_key
        self.timeout = timeout

    def is_available(self) -> bool:
        return bool(self.api_key)

    def search_videos(self, query: str, max_results: int = 10) -> list[dict]:
        if not self.is_available():
            raise RuntimeError("YOUTUBE_API_KEY не задан")
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": max_results,
            "key": self.api_key,
        }
        resp = httpx.get(f"{API_BASE}/search", params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("items", [])

    def list_channel_recent_videos(self, channel_id: str, max_results: int = 5) -> list[dict]:
        """Последние uploads канала без search.list.

        Использует channels.list(contentDetails) + playlistItems.list, поэтому
        не расходует узкую Search Queries/day квоту.
        """
        if not self.is_available():
            raise RuntimeError("YOUTUBE_API_KEY не задан")
        channel = self.get_channel_stats(channel_id)
        uploads = (((channel or {}).get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        if not uploads:
            return []
        params = {
            "part": "snippet,contentDetails",
            "playlistId": uploads,
            "maxResults": max_results,
            "key": self.api_key,
        }
        resp = httpx.get(f"{API_BASE}/playlistItems", params=params, timeout=self.timeout)
        resp.raise_for_status()
        items = []
        for item in resp.json().get("items", []):
            snippet = item.get("snippet", {}) or {}
            video_id = ((item.get("contentDetails") or {}).get("videoId")
                        or ((snippet.get("resourceId") or {}).get("videoId")))
            items.append({"id": {"videoId": video_id} if video_id else {}, "snippet": snippet})
        return items

    def resolve_channel_by_handle(self, handle: str) -> dict | None:
        """Резолвит @handle в РЕАЛЬНЫЙ channel item (title, id, statistics) через
        channels.list?forHandle= - раздел 5 hotfix: handle сам по себе НЕ должен
        использоваться как единственный brand_name, если можно получить настоящее
        имя канала."""
        if not self.is_available():
            raise RuntimeError("YOUTUBE_API_KEY не задан")
        handle_q = handle if handle.startswith("@") else f"@{handle}"
        params = {"part": "snippet,statistics", "forHandle": handle_q, "key": self.api_key}
        resp = httpx.get(f"{API_BASE}/channels", params=params, timeout=self.timeout)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return items[0] if items else None

    def get_channel_stats(self, channel_id: str) -> dict | None:
        if not self.is_available():
            raise RuntimeError("YOUTUBE_API_KEY не задан")
        params = {"part": "snippet,statistics,contentDetails", "id": channel_id, "key": self.api_key}
        resp = httpx.get(f"{API_BASE}/channels", params=params, timeout=self.timeout)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return items[0] if items else None

    def get_video_stats(self, video_id: str) -> dict | None:
        if not self.is_available():
            raise RuntimeError("YOUTUBE_API_KEY не задан")
        params = {"part": "snippet,statistics", "id": video_id, "key": self.api_key}
        resp = httpx.get(f"{API_BASE}/videos", params=params, timeout=self.timeout)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        return items[0] if items else None

    @staticmethod
    def channel_to_creator(channel_item: dict) -> Creator:
        snippet = channel_item.get("snippet", {}) or {}
        stats = channel_item.get("statistics", {}) or {}
        followers = stats.get("subscriberCount")
        channel_id = channel_item.get("id", "")
        return Creator(
            creator_id=f"yt_{channel_id}",
            name=snippet.get("title", "Unknown channel"),
            canonical_url=f"https://www.youtube.com/channel/{channel_id}" if channel_id else None,
            platform="youtube",
            followers=int(followers) if followers is not None else None,
            avg_views=None,
            median_views=None,
            engagement_rate=None,
            topic_tags=[],
            audience_tags=[],
            geo=snippet.get("country"),
            language=snippet.get("defaultLanguage"),
            created_at=_parse_dt(snippet.get("publishedAt")),
            last_seen_at=datetime.now(timezone.utc),
            source_refs=[f"https://www.youtube.com/channel/{channel_id}"] if channel_id else [],
            is_synthetic=False,
        )

    def fetch(self, query: str = "", max_results: int = 10, **kwargs) -> IngestionResult:
        result = IngestionResult()
        if not self.is_available():
            health_registry.unavailable(self.source_name, "YOUTUBE_API_KEY не задан - live-режим для YouTube выключен")
            result.notes.append("YOUTUBE_API_KEY отсутствует, источник unavailable")
            return result

        try:
            videos = self._run_with_retries(self.search_videos, query, max_results)
            channel_ids = {v["snippet"]["channelId"] for v in videos if "snippet" in v}
            creators: list[Creator] = []
            for cid in channel_ids:
                try:
                    channel = self.get_channel_stats(cid)
                    if channel:
                        creators.append(self.channel_to_creator(channel))
                except Exception as exc:  # noqa: BLE001
                    result.notes.append(f"channel {cid} error: {exc}")
            result.creators = creators
            health_registry.ok(self.source_name, f"получено {len(creators)} каналов по запросу '{query}'")
        except Exception as exc:  # noqa: BLE001
            health_registry.degraded(self.source_name, f"ошибка YouTube API: {exc}")
            result.notes.append(f"YouTube adapter degraded: {exc}")
        return result


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
