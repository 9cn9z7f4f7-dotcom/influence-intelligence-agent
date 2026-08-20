"""
Instagram - real-data update (раздел 10-19 требований).

Официальный Graph API требует бизнес-аккаунт и OAuth-авторизацию владельца
аккаунта - он не даёт доступа к произвольному чужому (конкурентному)
аккаунту. Обход логина/CAPTCHA/anti-bot защиты этот проект осознанно не
делает (раздел 3, 12, 33).

Поэтому реальные Instagram-данные собираются НЕ с Render, а через
local_connector/ - authenticated Playwright-сессию, которую пользователь
поднимает и логинит вручную на своём Mac (см. LOCAL_CONNECTOR.md). Эта
платформа - тонкий маршрутизатор к connector registry
(app/platforms/social_connector_base.py): если connector online - реальный
job/результат; если offline/CAPTCHA - честный connector_offline/
manual_intervention_required статус, НИКОГДА не имитированные данные.
"""
from __future__ import annotations

from app.platforms.social_connector_base import SocialConnectorPlatformAdapter


class InstagramPlatformAdapter(SocialConnectorPlatformAdapter):
    platform_name = "instagram"
