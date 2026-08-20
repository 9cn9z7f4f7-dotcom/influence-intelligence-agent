"""
Опциональные адаптеры платформ, для которых в этом MVP нет реальной
интеграции (Telegram/Instagram официальные API требуют отдельных
доступов/бизнес-верификации).

Они существуют, чтобы:
  1. health-панель честно показывала статус этих источников
     (см. раздел 13 мастер-промпта: "Telegram: DEGRADED, Instagram: UNAVAILABLE");
  2. в будущем сюда можно было подставить реальный adapter без изменения
     остального pipeline (тот же интерфейс BaseAdapter).

Никакого обхода авторизации/CAPTCHA/platform protections не производится.
"""
from __future__ import annotations

from app.health import health_registry
from app.ingestion.base import BaseAdapter, IngestionResult


class TelegramAdapter(BaseAdapter):
    source_name = "telegram"

    def is_available(self) -> bool:
        return False

    def fetch(self, **kwargs) -> IngestionResult:
        health_registry.degraded(
            self.source_name,
            "официальный Telegram adapter не подключён в этом MVP - используются только demo/интеграции из web-адаптера",
        )
        return IngestionResult(notes=["telegram adapter: not implemented, degraded by design"])


class InstagramAdapter(BaseAdapter):
    source_name = "instagram"

    def is_available(self) -> bool:
        return False

    def fetch(self, **kwargs) -> IngestionResult:
        health_registry.unavailable(
            self.source_name,
            "Instagram Graph API требует бизнес-верификации и токенов, которых нет в hackathon-среде",
        )
        return IngestionResult(notes=["instagram adapter: not implemented, unavailable by design"])
