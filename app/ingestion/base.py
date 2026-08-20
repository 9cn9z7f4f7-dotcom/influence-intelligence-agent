"""
Абстракция ингест-адаптера.

Каждый адаптер:
  - сам решает, доступен ли он (has_credentials / can_run);
  - никогда не бросает необработанное исключение наружу — сам ловит ошибки
    и репортит health-статус (ok/degraded/unavailable);
  - возвращает IngestionResult, даже если результат пустой.

Это то, что позволяет пайплайну не падать целиком, если один источник упал.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from app.models import Competitor, Creator, Integration


@dataclass
class IngestionResult:
    creators: list[Creator] = field(default_factory=list)
    integrations: list[Integration] = field(default_factory=list)
    competitors: list[Competitor] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Adapter(Protocol):
    source_name: str

    def is_available(self) -> bool:
        ...

    def fetch(self, **kwargs) -> IngestionResult:
        ...


class BaseAdapter:
    """Базовый класс с общей retry-логикой и безопасным fetch()."""

    source_name: str = "base"
    max_retries: int = 2

    def is_available(self) -> bool:
        return True

    def _run_with_retries(self, fn, *args, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - адаптер обязан быть отказоустойчивым
                last_exc = exc
        if last_exc:
            raise last_exc

    def fetch(self, **kwargs) -> IngestionResult:  # pragma: no cover - переопределяется
        raise NotImplementedError
