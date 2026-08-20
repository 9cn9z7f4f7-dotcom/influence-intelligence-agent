"""
TikTok - real-data update (раздел 10-19 требований), симметрично Instagram
(см. app/platforms/instagram.py). Официальный API не даёт доступа к чужим
(конкурентным) аккаунтам без их авторизации, а обход anti-bot защиты веб-версии
этот проект осознанно не делает (раздел 3, 12, 33).

Реальные данные собираются через local_connector/ (authenticated Playwright-
сессия на Mac пользователя, см. LOCAL_CONNECTOR.md). Эта платформа - тонкий
маршрутизатор к connector registry (app/platforms/social_connector_base.py).
"""
from __future__ import annotations

from app.platforms.social_connector_base import SocialConnectorPlatformAdapter


class TikTokPlatformAdapter(SocialConnectorPlatformAdapter):
    platform_name = "tiktok"
