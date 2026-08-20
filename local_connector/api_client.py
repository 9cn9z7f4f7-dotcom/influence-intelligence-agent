"""
Тонкий HTTP-клиент к Render'овским /api/connectors/* эндпоинтам (раздел 11).

Использует те же pydantic-схемы, что серверная сторона
(app/connectors/models.py) - client и server никогда не расходятся по формату.
"""
from __future__ import annotations

from typing import Optional

import httpx

from app.connectors.models import (
    ConnectorHeartbeatRequest,
    ConnectorJob,
    ConnectorRegisterRequest,
    ConnectorRegisterResponse,
    ConnectorResultsSubmission,
)
from local_connector.config import CONNECTOR_SHARED_SECRET, RENDER_BASE_URL


class RenderApiClient:
    def __init__(self, base_url: str = RENDER_BASE_URL, timeout: float = 15.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def register(self, supported_platforms: list[str]) -> ConnectorRegisterResponse:
        payload = ConnectorRegisterRequest(supported_platforms=supported_platforms, shared_secret=CONNECTOR_SHARED_SECRET)
        resp = httpx.post(f"{self.base_url}/api/connectors/register", json=payload.model_dump(), timeout=self.timeout)
        resp.raise_for_status()
        return ConnectorRegisterResponse.model_validate(resp.json())

    def heartbeat(self, connector_id: str, connector_token: str, status: str = "online",
                   detail: Optional[str] = None) -> bool:
        payload = ConnectorHeartbeatRequest(
            connector_id=connector_id, connector_token=connector_token, status=status, detail=detail,
        )
        try:
            resp = httpx.post(f"{self.base_url}/api/connectors/heartbeat", json=payload.model_dump(), timeout=self.timeout)
            resp.raise_for_status()
            return True
        except Exception:  # noqa: BLE001 - heartbeat не должен ронять весь connector process
            return False

    def fetch_jobs(self, connector_id: str, connector_token: str, max_jobs: int = 5) -> list[ConnectorJob]:
        resp = httpx.get(
            f"{self.base_url}/api/connectors/jobs",
            params={"connector_id": connector_id, "connector_token": connector_token, "max_jobs": max_jobs},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return [ConnectorJob.model_validate(j) for j in resp.json().get("jobs", [])]

    def submit_results(self, submission: ConnectorResultsSubmission) -> bool:
        resp = httpx.post(f"{self.base_url}/api/connectors/results", json=submission.model_dump(), timeout=self.timeout)
        resp.raise_for_status()
        return True
