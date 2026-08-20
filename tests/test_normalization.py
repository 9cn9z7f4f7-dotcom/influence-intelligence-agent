from __future__ import annotations

from config.settings import Settings


def test_bucket_for_value_handles_none(settings: Settings):
    assert settings.bucket_for_value(None, settings.follower_buckets) is None


def test_bucket_for_value_boundaries(settings: Settings):
    assert settings.bucket_for_value(0, settings.follower_buckets) == "nano"
    assert settings.bucket_for_value(9_999, settings.follower_buckets) == "nano"
    assert settings.bucket_for_value(10_000, settings.follower_buckets) == "micro"
    assert settings.bucket_for_value(49_999, settings.follower_buckets) == "micro"
    assert settings.bucket_for_value(50_000, settings.follower_buckets) == "mid"
    assert settings.bucket_for_value(200_000, settings.follower_buckets) == "macro"
    assert settings.bucket_for_value(10_000_000, settings.follower_buckets) == "macro"


def test_next_move_weights_sum_to_one(settings: Settings):
    assert abs(sum(settings.next_move_weights.values()) - 1.0) < 1e-6


def test_white_space_weights_sum_to_one(settings: Settings):
    assert abs(sum(settings.white_space_weights.values()) - 1.0) < 1e-6
