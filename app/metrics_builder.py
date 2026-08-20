"""
Метрики creator считаются по НЕСКОЛЬКИМ последним видео/постам, а не по
одному ролику (раздел 8 требований).

До этого модуля avg_views у live YouTube creator брался из статистики
ОДНОГО видео (того самого, которое вызвало детекцию интеграции) - это
искажает картину: один виральный или один провальный ролик не отражает
типичный охват канала. Здесь avg_views/median_views считаются как честная
агрегация по выборке последних видео/постов; если данных недостаточно -
возвращается None, а не "додуманное" число.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

# Одного видео недостаточно, чтобы говорить о "среднем" по каналу - это просьба
# минимум раздела 8: "никогда просмотры одного видео не используются как avg_views".
MIN_SAMPLE_FOR_AVG = 2


@dataclass
class CreatorMetrics:
    avg_views: Optional[float] = None
    median_views: Optional[float] = None
    recent_upload_count_30d: Optional[int] = None
    last_upload_at: Optional[datetime] = None
    sample_size: int = 0  # сколько видео/постов реально вошло в расчёт avg/median


def compute_creator_metrics(recent_items: list[dict], now: Optional[datetime] = None) -> CreatorMetrics:
    """recent_items: список словарей {"views": float|int|None, "published_at": datetime|None}
    по последним видео/постам ОДНОГО creator (не по разным каналам)."""
    now = now or datetime.now(timezone.utc)

    views = [float(item["views"]) for item in recent_items if item.get("views") is not None]
    published_dates = [item["published_at"] for item in recent_items if item.get("published_at") is not None]

    metrics = CreatorMetrics(sample_size=len(views))

    if len(views) >= MIN_SAMPLE_FOR_AVG:
        metrics.avg_views = round(statistics.mean(views), 2)
        metrics.median_views = round(statistics.median(views), 2)
    # len(views) in (0, 1) -> avg_views/median_views остаются None: недостаточно
    # данных для честного среднего, а не "додумываем" из единственного числа.

    if published_dates:
        metrics.last_upload_at = max(published_dates)
        cutoff = now - timedelta(days=30)
        metrics.recent_upload_count_30d = sum(
            1 for d in published_dates if _as_aware(d) >= cutoff
        )

    return metrics


def _as_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
