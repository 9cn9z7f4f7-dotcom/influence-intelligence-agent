"""
Health / degraded-mode tracking для источников данных.

Правило проекта: если один источник падает, pipeline не падает целиком,
а UI явно показывает, что результат неполный (никакого silent failure).
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import SourceHealth, SourceStatus


class HealthRegistry:
    def __init__(self) -> None:
        self._state: dict[str, SourceHealth] = {}

    def report(self, source: str, status: SourceStatus, detail: str | None = None) -> None:
        self._state[source] = SourceHealth(
            source=source,
            status=status,
            detail=detail,
            last_checked_at=datetime.now(timezone.utc),
        )

    def ok(self, source: str, detail: str | None = None) -> None:
        self.report(source, SourceStatus.OK, detail)

    def degraded(self, source: str, detail: str | None = None) -> None:
        self.report(source, SourceStatus.DEGRADED, detail)

    def unavailable(self, source: str, detail: str | None = None) -> None:
        self.report(source, SourceStatus.UNAVAILABLE, detail)

    def snapshot(self) -> list[dict]:
        return [
            {
                "source": h.source,
                "status": h.status.value,
                "detail": h.detail,
                "last_checked_at": h.last_checked_at.isoformat() if h.last_checked_at else None,
            }
            for h in self._state.values()
        ]

    def has_degradation(self) -> bool:
        return any(h.status != SourceStatus.OK for h in self._state.values())

    def reset(self) -> None:
        self._state.clear()


# Глобальный реестр на процесс - для MVP этого достаточно (single-process demo/live).
health_registry = HealthRegistry()
