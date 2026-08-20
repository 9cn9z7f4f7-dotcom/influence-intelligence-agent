"""
Централизованная конфигурация проекта.

Все "магические числа" (buckets, windows, weights) вынесены сюда,
чтобы их было легко объяснить жюри и изменить без правки логики.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DEMO_DATA_DIR = DATA_DIR / "demo"
OUTPUT_DIR = BASE_DIR / "output"
STATE_DB_PATH = BASE_DIR / "output" / "state.sqlite3"
# Отдельная БД для live/imported данных - НИКОГДА не смешивается с demo state.sqlite3
# автоматически (см. раздел I требований к live ingestion: source_mode-разметка +
# физическое разделение хранилищ, чтобы demo-reset/demo-run не мог случайно затронуть
# накопленные live/imported данные, и наоборот).
LIVE_STATE_DB_PATH = BASE_DIR / "output" / "live_state.sqlite3"
CONFIG_OVERRIDE_PATH = BASE_DIR / "config" / "config.local.json"

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Follower buckets (nano/micro/mid/macro). Верхняя граница не включается,
# кроме последнего бакета.
# ---------------------------------------------------------------------------
DEFAULT_FOLLOWER_BUCKETS = [
    {"name": "nano", "min": 0, "max": 10_000},
    {"name": "micro", "min": 10_000, "max": 50_000},
    {"name": "mid", "min": 50_000, "max": 200_000},
    {"name": "macro", "min": 200_000, "max": None},
]

DEFAULT_VIEWS_BUCKETS = [
    {"name": "low", "min": 0, "max": 5_000},
    {"name": "medium", "min": 5_000, "max": 50_000},
    {"name": "high", "min": 50_000, "max": 500_000},
    {"name": "viral", "min": 500_000, "max": None},
]

# ---------------------------------------------------------------------------
# Competitor DNA windows (в днях)
# ---------------------------------------------------------------------------
DEFAULT_DNA_WINDOWS = {
    "recent_days": 30,
    "historical_days": 90,  # предыдущие 90 дней ПЕРЕД recent-окном
}

# ---------------------------------------------------------------------------
# Next Move: веса similarity score. Сумма должна быть 1.0 (проверяется тестом).
# ---------------------------------------------------------------------------
DEFAULT_NEXT_MOVE_WEIGHTS = {
    "creator_size_match": 0.25,
    "topic_match": 0.25,
    "platform_match": 0.15,
    "content_type_match": 0.15,
    "views_profile_match": 0.10,
    "recent_strategy_match": 0.10,
}

# ---------------------------------------------------------------------------
# White Space: веса opportunity score. Сумма должна быть 1.0.
# ---------------------------------------------------------------------------
DEFAULT_WHITE_SPACE_WEIGHTS = {
    "supply": 0.30,          # доступное количество креаторов в сегменте
    "low_saturation": 0.30,  # инверсия насыщенности конкурентами
    "our_relevance": 0.25,   # соответствие our_profile
    "momentum": 0.15,        # активность/рост креаторов в сегменте
}

DEFAULT_MIN_HYPOTHESIS_OBSERVATIONS = 2  # минимум supporting observations для AI_INFERENCE

DEFAULT_OUR_MOVE_MAX_ITEMS = 5
DEFAULT_OUR_MOVE_MIN_ITEMS = 3

DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.55

# ---------------------------------------------------------------------------
# Live YouTube integration detector - веса сигналов и порог confidence.
# Ниже порога -> candidate/manual_review, а не автоматически созданный Integration.
# ---------------------------------------------------------------------------
DEFAULT_LIVE_INTEGRATION_CONFIDENCE_THRESHOLD = 0.5

DEFAULT_LIVE_DETECTOR_WEIGHTS = {
    "brand_in_title": 0.30,
    "brand_in_description": 0.15,
    "alias_match": 0.10,
    "promo_code": 0.20,
    "brand_url": 0.15,
    "cta_phrase": 0.10,
    "sponsor_wording": 0.20,
    "repeated_mention": 0.10,
}

DEFAULT_LIVE_MAX_RESULTS_PER_QUERY = 15


def _load_overrides() -> dict[str, Any]:
    if CONFIG_OVERRIDE_PATH.exists():
        try:
            return json.loads(CONFIG_OVERRIDE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


class Settings:
    """Простой конфиг-контейнер с возможностью локального override через JSON-файл."""

    def __init__(self) -> None:
        overrides = _load_overrides()

        self.follower_buckets = overrides.get("follower_buckets", DEFAULT_FOLLOWER_BUCKETS)
        self.views_buckets = overrides.get("views_buckets", DEFAULT_VIEWS_BUCKETS)
        self.dna_windows = overrides.get("dna_windows", DEFAULT_DNA_WINDOWS)
        self.next_move_weights = overrides.get("next_move_weights", DEFAULT_NEXT_MOVE_WEIGHTS)
        self.white_space_weights = overrides.get("white_space_weights", DEFAULT_WHITE_SPACE_WEIGHTS)
        self.min_hypothesis_observations = overrides.get(
            "min_hypothesis_observations", DEFAULT_MIN_HYPOTHESIS_OBSERVATIONS
        )
        self.our_move_max_items = overrides.get("our_move_max_items", DEFAULT_OUR_MOVE_MAX_ITEMS)
        self.our_move_min_items = overrides.get("our_move_min_items", DEFAULT_OUR_MOVE_MIN_ITEMS)
        self.low_confidence_threshold = overrides.get(
            "low_confidence_threshold", DEFAULT_LOW_CONFIDENCE_THRESHOLD
        )
        self.live_integration_confidence_threshold = overrides.get(
            "live_integration_confidence_threshold", DEFAULT_LIVE_INTEGRATION_CONFIDENCE_THRESHOLD
        )
        self.live_detector_weights = overrides.get("live_detector_weights", DEFAULT_LIVE_DETECTOR_WEIGHTS)
        self.live_max_results_per_query = overrides.get(
            "live_max_results_per_query", DEFAULT_LIVE_MAX_RESULTS_PER_QUERY
        )

        # Внешние credentials / режимы
        self.youtube_api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
        self.anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        self.app_mode = os.environ.get("APP_MODE", "demo").strip().lower()  # demo | live

    def bucket_for_value(self, value: float | None, buckets: list[dict]) -> str | None:
        if value is None:
            return None
        for b in buckets:
            lo = b["min"]
            hi = b["max"]
            if value >= lo and (hi is None or value < hi):
                return b["name"]
        return buckets[-1]["name"] if buckets else None


settings = Settings()
