from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.metrics_builder import MIN_SAMPLE_FOR_AVG, compute_creator_metrics


def test_single_video_never_becomes_avg_views():
    """Раздел 8: просмотры ОДНОГО видео никогда не считаются avg_views канала."""
    metrics = compute_creator_metrics([{"views": 100000, "published_at": datetime.now(timezone.utc)}])
    assert metrics.avg_views is None
    assert metrics.median_views is None
    assert metrics.sample_size == 1


def test_multiple_videos_produce_real_average_and_median():
    now = datetime.now(timezone.utc)
    items = [
        {"views": 1000, "published_at": now - timedelta(days=1)},
        {"views": 3000, "published_at": now - timedelta(days=5)},
        {"views": 2000, "published_at": now - timedelta(days=10)},
    ]
    metrics = compute_creator_metrics(items, now=now)
    assert metrics.sample_size == 3
    assert metrics.avg_views == 2000.0
    assert metrics.median_views == 2000.0
    assert metrics.recent_upload_count_30d == 3
    assert metrics.last_upload_at == now - timedelta(days=1)


def test_no_data_returns_all_none():
    metrics = compute_creator_metrics([])
    assert metrics.avg_views is None
    assert metrics.median_views is None
    assert metrics.recent_upload_count_30d is None
    assert metrics.last_upload_at is None
    assert metrics.sample_size == 0


def test_missing_views_are_excluded_not_treated_as_zero():
    items = [
        {"views": None, "published_at": datetime.now(timezone.utc)},
        {"views": 500, "published_at": datetime.now(timezone.utc)},
    ]
    metrics = compute_creator_metrics(items)
    # только 1 видео с реальными views -> ниже MIN_SAMPLE_FOR_AVG -> None, не 250.0
    assert metrics.sample_size == 1
    assert MIN_SAMPLE_FOR_AVG == 2
    assert metrics.avg_views is None


def test_recent_upload_count_30d_excludes_old_videos():
    now = datetime.now(timezone.utc)
    items = [
        {"views": 100, "published_at": now - timedelta(days=5)},
        {"views": 200, "published_at": now - timedelta(days=45)},
    ]
    metrics = compute_creator_metrics(items, now=now)
    assert metrics.recent_upload_count_30d == 1
