"""
Конфигурация local connector - раздел 10-14 требований.

Все настройки читаются из переменных окружения (или .env в корне проекта -
python-dotenv уже используется остальным проектом) - никаких хардкоженных
URL/секретов в коде.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv опционален - без него просто используем os.environ как есть
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

# Раздел 12-13: session state НИКОГДА не коммитится в git (см. .gitignore) и
# НИКОГДА не отправляется на Render/OpenRouter - остаётся только на диске
# пользователя.
LOCAL_SESSIONS_DIR = BASE_DIR / ".local_sessions"
LOCAL_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

RENDER_BASE_URL = os.environ.get("RENDER_BASE_URL", "http://localhost:8000").rstrip("/")
CONNECTOR_SHARED_SECRET = os.environ.get("CONNECTOR_SHARED_SECRET", "").strip() or None
SUPPORTED_PLATFORMS = [
    p.strip() for p in os.environ.get("CONNECTOR_PLATFORMS", "instagram,tiktok").split(",") if p.strip()
]

CREDENTIALS_PATH = LOCAL_SESSIONS_DIR / "connector_credentials.json"
INSTAGRAM_STATE_PATH = LOCAL_SESSIONS_DIR / "instagram_state.json"
TIKTOK_STATE_PATH = LOCAL_SESSIONS_DIR / "tiktok_state.json"

POLL_INTERVAL_SECONDS = float(os.environ.get("CONNECTOR_POLL_INTERVAL_SECONDS", "5"))
HEARTBEAT_INTERVAL_SECONDS = float(os.environ.get("CONNECTOR_HEARTBEAT_INTERVAL_SECONDS", "20"))
