"""
Loader демо-датасета (data/demo/*.json) в единую модель данных.

Работает без интернета - это основа DEMO-режима и fallback, если live
источники недоступны.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.evidence import fact
from app.health import health_registry
from app.ingestion.base import BaseAdapter, IngestionResult
from app.models import Competitor, Creator, Integration
from config.settings import DEMO_DATA_DIR


class DemoLoader(BaseAdapter):
    source_name = "demo_dataset"

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or DEMO_DATA_DIR

    def is_available(self) -> bool:
        return (self.data_dir / "creators.json").exists()

    def _read_json(self, name: str) -> list[dict]:
        path = self.data_dir / name
        if not path.exists():
            return []
        return json.loads(path.read_text(encoding="utf-8"))

    def fetch(self, **kwargs) -> IngestionResult:
        result = IngestionResult()
        if not self.is_available():
            health_registry.unavailable(self.source_name, "demo dataset не найден - запустите scripts/generate_demo_data.py")
            return result
        try:
            competitors_raw = self._read_json("competitors.json")
            creators_raw = self._read_json("creators.json")
            integrations_raw = self._read_json("integrations.json")

            result.competitors = [Competitor.model_validate(c) for c in competitors_raw]
            result.creators = [Creator.model_validate(c) for c in creators_raw]
            integrations = [Integration.model_validate(i) for i in integrations_raw]
            for integration in integrations:
                # Каждая интеграция должна ссылаться хотя бы на один FACT (то, что
                # она наблюдалась) - иначе даже "сырые" записи не проходят evidence-цепочку.
                if not integration.evidence:
                    integration.evidence = [fact(
                        field="integration_observed",
                        value={
                            "competitor_id": integration.competitor_id,
                            "creator_id": integration.creator_id,
                            "detected_offer": integration.detected_offer,
                            "detected_mechanic": integration.detected_mechanic,
                        },
                        source_url=integration.content_url,
                        observed_at=integration.published_at,
                        raw_fragment=integration.raw_text,
                    )]
            result.integrations = integrations

            health_registry.ok(
                self.source_name,
                f"загружено {len(result.competitors)} конкурентов, {len(result.creators)} креаторов, "
                f"{len(result.integrations)} интеграций (synthetic demo data)",
            )
        except Exception as exc:  # noqa: BLE001
            health_registry.degraded(self.source_name, f"ошибка чтения demo dataset: {exc}")
        return result

    def load_our_profile(self) -> dict:
        path = self.data_dir / "our_profile.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))
