from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.analysis.models import AnalysisConfig, AnalyzeRequest


def test_default_config_allows_confirmed_and_organic_not_manual_review():
    config = AnalysisConfig()
    assert config.allowed_integration_categories() == {"confirmed", "organic_mention"}


def test_confirmed_only_overrides_everything_else():
    config = AnalysisConfig(confirmed_only=True, include_manual_review=True, include_organic=True)
    assert config.allowed_integration_categories() == {"confirmed"}


def test_sponsored_only_excludes_organic():
    config = AnalysisConfig(sponsored_only=True)
    assert "organic_mention" not in config.allowed_integration_categories()


def test_include_manual_review_adds_category():
    config = AnalysisConfig(include_manual_review=True)
    assert config.allowed_integration_categories() == {"confirmed", "organic_mention", "manual_review"}


def test_custom_date_range_requires_start_and_end():
    with pytest.raises(ValidationError):
        AnalysisConfig(date_range="custom")

    config = AnalysisConfig(date_range="custom", custom_start=date(2026, 1, 1), custom_end=date(2026, 2, 1))
    assert config.date_range == "custom"


def test_custom_start_after_end_is_rejected():
    with pytest.raises(ValidationError):
        AnalysisConfig(date_range="custom", custom_start=date(2026, 3, 1), custom_end=date(2026, 1, 1))


def test_min_followers_greater_than_max_is_rejected():
    with pytest.raises(ValidationError):
        AnalysisConfig(min_followers=100_000, max_followers=1_000)


def test_topics_are_normalized_to_snake_case():
    config = AnalysisConfig(include_topics=["Student Lifestyle", "exam-prep"])
    assert config.include_topics == ["student_lifestyle", "exam_prep"]


def test_matches_followers_missing_data_fails_strict_filter_but_passes_when_no_filter_set():
    config_no_filter = AnalysisConfig()
    assert config_no_filter.matches_followers(None) is True

    config_with_filter = AnalysisConfig(min_followers=1000)
    # Раздел 8 требований: недостающие данные не додумываются - не проходят строгий фильтр.
    assert config_with_filter.matches_followers(None) is False
    assert config_with_filter.matches_followers(500) is False
    assert config_with_filter.matches_followers(5000) is True


def test_matches_metrics_respects_min_thresholds():
    config = AnalysisConfig(min_median_views=1000, min_engagement_rate=0.02)
    assert config.matches_metrics(median_views=1500, avg_views=2000, engagement_rate=0.03) is True
    assert config.matches_metrics(median_views=500, avg_views=2000, engagement_rate=0.03) is False
    assert config.matches_metrics(median_views=1500, avg_views=2000, engagement_rate=None) is False


def test_matches_topics_include_and_exclude():
    config = AnalysisConfig(include_topics=["medical_students"], exclude_topics=["entertainment"])
    assert config.matches_topics(["medical_students"]) is True
    assert config.matches_topics(["entertainment"]) is False
    assert config.matches_topics(["tech"]) is False  # не входит в include_topics
    assert config.matches_topics(["medical_students", "entertainment"]) is False  # exclude выигрывает


def test_matches_geo_and_language():
    config = AnalysisConfig(geo=["RU"], language=["ru"])
    assert config.matches_geo("RU", "ru") is True
    assert config.matches_geo("KZ", "ru") is False
    assert config.matches_geo(None, None) is False


def test_analyze_request_deduplicates_platforms_preserving_order():
    request = AnalyzeRequest(brand="Автор24", platforms=["youtube", "instagram", "youtube"])
    assert request.platforms == ["youtube", "instagram"]


def test_analyze_request_rejects_empty_platforms():
    with pytest.raises(ValidationError):
        AnalyzeRequest(brand="Автор24", platforms=[])
