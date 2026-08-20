#!/usr/bin/env python
"""
Local connector entry point - раздел 10 требований.

Запуск:
    python local_connector/run.py

Работает на Mac пользователя, НЕ на Render. Регистрируется в Render backend,
шлёт периодический heartbeat, поллит фиксированные jobs (см.
app/connectors/models.py::ConnectorJob) и отправляет normalized результаты
обратно.

НИКАКИХ arbitrary shell commands: job schema фиксированная - dispatch ниже
(_dispatch) - обычный if/elif по job.platform (Literal["instagram","tiktok"],
уже провалидированному pydantic-ом на этапе получения job). Ничего в этом
файле не вызывает eval()/exec()/subprocess с содержимым job.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Позволяет запускать `python local_connector/run.py` из корня проекта и
# при этом использовать `from app...` импорты (общие pydantic-схемы).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from local_connector import config  # noqa: E402
from local_connector.api_client import RenderApiClient  # noqa: E402


def _load_or_register(client: RenderApiClient) -> tuple[str, str]:
    if config.CREDENTIALS_PATH.exists():
        data = json.loads(config.CREDENTIALS_PATH.read_text(encoding="utf-8"))
        return data["connector_id"], data["connector_token"]

    response = client.register(config.SUPPORTED_PLATFORMS)
    config.CREDENTIALS_PATH.write_text(
        json.dumps({"connector_id": response.connector_id, "connector_token": response.connector_token}, indent=2),
        encoding="utf-8",
    )
    print(f"Зарегистрирован новый connector_id={response.connector_id} (сохранён в {config.CREDENTIALS_PATH})")
    return response.connector_id, response.connector_token


def _dispatch(job, connector_id: str, connector_token: str, playwright):
    """Фиксированный dispatch - ТОЛЬКО instagram/tiktok (раздел 14: "job schema
    фиксированная"). job.platform уже ограничен pydantic Literal на этапе
    ConnectorJob.model_validate() в api_client.fetch_jobs()."""
    if job.platform == "instagram":
        from local_connector.instagram_connector import handle_job
        return handle_job(job, connector_id, connector_token, playwright, config.INSTAGRAM_STATE_PATH)
    if job.platform == "tiktok":
        from local_connector.tiktok_connector import handle_job
        return handle_job(job, connector_id, connector_token, playwright, config.TIKTOK_STATE_PATH)
    raise ValueError(f"неизвестная платформа в job: {job.platform!r}")  # не должно случаться - fixed schema


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright не установлен. Запусти: pip install -r requirements.txt && playwright install chromium")
        sys.exit(1)

    client = RenderApiClient()
    connector_id, connector_token = _load_or_register(client)
    print(f"Local connector запущен. platforms={config.SUPPORTED_PLATFORMS} render={config.RENDER_BASE_URL}")
    print("Ctrl+C - остановить.")

    last_heartbeat = 0.0
    with sync_playwright() as playwright:
        while True:
            now = time.time()
            if now - last_heartbeat >= config.HEARTBEAT_INTERVAL_SECONDS:
                client.heartbeat(connector_id, connector_token)
                last_heartbeat = now

            try:
                jobs = client.fetch_jobs(connector_id, connector_token)
            except Exception as exc:  # noqa: BLE001 - Render временно недоступен - ждём и пробуем снова
                print(f"[warn] не удалось получить jobs: {exc}")
                jobs = []

            for job in jobs:
                print(f"[job {job.job_id}] platform={job.platform} brand={job.brand!r}")
                submission = _dispatch(job, connector_id, connector_token, playwright)
                if submission.status == "manual_intervention_required":
                    client.heartbeat(connector_id, connector_token, status="manual_intervention_required",
                                      detail=submission.detail)
                    print(f"[job {job.job_id}] MANUAL INTERVENTION REQUIRED: {submission.detail}")
                client.submit_results(submission)
                print(f"[job {job.job_id}] done: status={submission.status}, items={len(submission.items)}")

            time.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nLocal connector остановлен.")
