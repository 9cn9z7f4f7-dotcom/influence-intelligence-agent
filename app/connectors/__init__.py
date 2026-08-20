"""Local connector registration/job/result system (раздел 10-19 требований).

Позволяет local_connector/run.py (запускается пользователем на своём Mac,
НЕ на Render) регистрироваться, слать heartbeat, получать fixed-schema jobs
и присылать normalized результаты - см. app/connectors/models.py и
app/connectors/registry.py."""
from __future__ import annotations

from app.connectors.registry import ConnectorRegistry, registry

__all__ = ["ConnectorRegistry", "registry"]
