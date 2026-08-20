"""
Local connector - раздел 10-14 требований.

Отдельный компонент, который пользователь запускает НА СВОЁМ Mac (НЕ на
Render): python local_connector/run.py. Собирает реальные публичные
Instagram/TikTok данные через authenticated Playwright-сессию и отправляет
normalized результаты обратно в Render backend.

См. LOCAL_CONNECTOR.md за инструкциями по установке/запуску/логину/security.
"""
