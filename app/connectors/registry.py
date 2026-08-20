"""
In-memory connector registry (раздел 11 требований).

Симметрично app/health.py::HealthRegistry и app/analysis/store.py (process-
global in-memory состояние - для MVP этого достаточно, раздел 33 явно
запрещает добавлять scheduler/queue-инфраструктуру сверх необходимого).

Хранит: зарегистрированные connectors (id/token/supported_platforms/last
heartbeat), pending jobs по платформе, submitted результаты по job_id.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.connectors.models import ConnectorJob, ConnectorResultsSubmission
from config.settings import settings as default_settings


@dataclass
class ConnectorRecord:
    connector_id: str
    connector_token: str
    supported_platforms: list[str]
    last_heartbeat_ts: float
    status: str = "online"
    detail: Optional[str] = None
    registered_at: float = field(default_factory=time.time)


class ConnectorRegistry:
    def __init__(self, offline_after_seconds: int | None = None, settings=None) -> None:
        self.settings = settings or default_settings
        self.offline_after_seconds = offline_after_seconds or self.settings.connector_offline_after_seconds
        self._lock = threading.Lock()
        self._connectors: dict[str, ConnectorRecord] = {}
        self._jobs: dict[str, ConnectorJob] = {}
        self._pending_by_platform: dict[str, list[str]] = defaultdict(list)
        self._results: dict[str, ConnectorResultsSubmission] = {}

    # ------------------------------------------------------------------
    def reset(self) -> None:
        """Только для тестов - сбрасывает всё in-memory состояние."""
        with self._lock:
            self._connectors.clear()
            self._jobs.clear()
            self._pending_by_platform.clear()
            self._results.clear()

    def register(self, supported_platforms: list[str], shared_secret: Optional[str] = None) -> Optional[ConnectorRecord]:
        required = self.settings.connector_shared_secret
        if required and shared_secret != required:
            return None  # неверный shared secret - регистрация отклонена
        connector_id = f"conn_{uuid.uuid4().hex[:10]}"
        token = uuid.uuid4().hex
        record = ConnectorRecord(
            connector_id=connector_id, connector_token=token,
            supported_platforms=list(supported_platforms), last_heartbeat_ts=time.time(),
        )
        with self._lock:
            self._connectors[connector_id] = record
        return record

    def _authenticate(self, connector_id: str, token: str) -> Optional[ConnectorRecord]:
        with self._lock:
            record = self._connectors.get(connector_id)
        if record is None or record.connector_token != token:
            return None
        return record

    def heartbeat(self, connector_id: str, token: str, status: str = "online",
                   detail: Optional[str] = None) -> bool:
        record = self._authenticate(connector_id, token)
        if record is None:
            return False
        with self._lock:
            record.last_heartbeat_ts = time.time()
            record.status = status
            record.detail = detail
        return True

    def platform_status(self, platform: str) -> tuple[str, Optional[str]]:
        """(status, detail) для платформы - раздел 19 требований:
        "online" (можно enqueue job) | "connector_offline" | "manual_intervention_required"."""
        now = time.time()
        with self._lock:
            candidates = [r for r in self._connectors.values() if platform in r.supported_platforms]
        if not candidates:
            return "connector_offline", f"Ни один local connector с поддержкой platform={platform} не зарегистрирован"

        best = max(candidates, key=lambda r: r.last_heartbeat_ts)
        if now - best.last_heartbeat_ts > self.offline_after_seconds:
            age = int(now - best.last_heartbeat_ts)
            return "connector_offline", f"Local connector не присылал heartbeat {age}s (порог {self.offline_after_seconds}s)"
        if best.status == "manual_intervention_required":
            return "manual_intervention_required", best.detail or "CAPTCHA/challenge - требуется ручной вход пользователя"
        return "online", best.detail

    # ------------------------------------------------------------------
    def enqueue_job(self, analysis_id: str, platform: str, brand: str,
                     aliases: list[str] | None = None, settings: dict | None = None) -> ConnectorJob:
        job = ConnectorJob(
            job_id=f"job_{uuid.uuid4().hex[:10]}", analysis_id=analysis_id, platform=platform,
            brand=brand, aliases=aliases or [], settings=settings or {},
            created_at=datetime.now(timezone.utc),
        )
        with self._lock:
            self._jobs[job.job_id] = job
            self._pending_by_platform[platform].append(job.job_id)
        return job

    def pop_jobs_for_connector(self, connector_id: str, token: str, max_jobs: int = 5) -> list[ConnectorJob]:
        record = self._authenticate(connector_id, token)
        if record is None:
            return []
        jobs: list[ConnectorJob] = []
        with self._lock:
            for platform in record.supported_platforms:
                queue = self._pending_by_platform.get(platform, [])
                while queue and len(jobs) < max_jobs:
                    job_id = queue.pop(0)
                    job = self._jobs.get(job_id)
                    if job:
                        jobs.append(job)
        return jobs

    def submit_results(self, submission: ConnectorResultsSubmission) -> bool:
        record = self._authenticate(submission.connector_id, submission.connector_token)
        if record is None:
            return False
        with self._lock:
            self._results[submission.job_id] = submission
            if submission.status == "manual_intervention_required":
                record.status = "manual_intervention_required"
                record.detail = submission.detail
        return True

    def get_result(self, job_id: str) -> Optional[ConnectorResultsSubmission]:
        with self._lock:
            return self._results.get(job_id)

    def wait_for_result(self, job_id: str, timeout_seconds: float = 0.0,
                         poll_interval: float = 0.2) -> Optional[ConnectorResultsSubmission]:
        """Короткий bounded poll (раздел 33 - без отдельного scheduler/queue,
        /api/analyze остаётся синхронным MVP-эндпоинтом). Если connector не
        успел ответить за timeout_seconds - честно возвращает None, вызывающий
        код (app/analysis/pipeline.py) помечает платформу как degraded с
        job_id, а НЕ подмешивает synthetic данные."""
        deadline = time.time() + timeout_seconds
        while True:
            result = self.get_result(job_id)
            if result is not None or time.time() >= deadline:
                return result
            time.sleep(poll_interval)


# Глобальный registry на процесс - симметрично app.health.health_registry.
registry = ConnectorRegistry()
