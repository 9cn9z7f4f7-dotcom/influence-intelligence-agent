"""Per-analysis wall-clock budget used to stop optional discovery/enrichment safely."""
from __future__ import annotations

import time
from contextvars import ContextVar

_deadline: ContextVar[float | None] = ContextVar("analysis_deadline", default=None)


def start_budget(seconds: float = 295.0) -> None:
    _deadline.set(time.monotonic() + max(1.0, seconds))


def clear_budget() -> None:
    _deadline.set(None)


def remaining_seconds() -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def budget_exhausted(reserve_seconds: float = 0.0) -> bool:
    remaining = remaining_seconds()
    return remaining is not None and remaining <= reserve_seconds


def clamp_timeout(default: float, minimum: float = 1.0) -> float:
    remaining = remaining_seconds()
    if remaining is None:
        return default
    return max(minimum, min(default, max(minimum, remaining - 1.0)))
