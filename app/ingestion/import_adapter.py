"""
J. IMPORT FALLBACK для Instagram/Telegram (и любых других платформ).

Не обходит platform protections - просто нормализует заранее собранные
публичные данные (CSV/JSON) в ту же модель Creator/Integration/Evidence,
чтобы они прошли через тот же analytical pipeline, что live YouTube и demo.

Ожидаемые колонки: competitor, creator, platform, content_url, published_at,
followers, views, topic, raw_text, offer, cta, mechanic.
Все колонки кроме competitor/creator/platform опциональны - недостающие
данные не додумываются, а остаются None.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.evidence import EvidenceStore, fact
from app.ingestion.identifiers import stable_id
from app.models import Competitor, Creator, Integration, SourceMode

REQUIRED_COLUMNS = {"competitor", "creator", "platform"}


@dataclass
class ImportReport:
    source_path: str
    rows_total: int = 0
    rows_imported: int = 0
    rows_failed: int = 0
    errors: list[str] = field(default_factory=list)
    competitors: list[Competitor] = field(default_factory=list)
    creators: list[Creator] = field(default_factory=list)
    integrations: list[Integration] = field(default_factory=list)


def _safe_int(value: Any) -> int | None:
    if value in (None, "", "null", "None"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value in (None, "", "null", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    for fmt in (None, "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            if fmt is None:
                return datetime.fromisoformat(text.replace("Z", "+00:00"))
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _read_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("rows") or data.get("integrations") or [data]
        return list(data)
    # По умолчанию - CSV (наиболее вероятный формат для ручного сбора данных).
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def import_integrations(path: str | Path, evidence_store: EvidenceStore | None = None) -> ImportReport:
    path = Path(path)
    report = ImportReport(source_path=str(path))
    evidence_store = evidence_store or EvidenceStore()

    if not path.exists():
        report.errors.append(f"файл не найден: {path}")
        return report

    try:
        rows = _read_rows(path)
    except Exception as exc:  # noqa: BLE001 - плохой файл не должен ронять CLI
        report.errors.append(f"не удалось прочитать файл: {exc}")
        return report

    report.rows_total = len(rows)
    competitors_by_id: dict[str, Competitor] = {}
    creators_by_id: dict[str, Creator] = {}

    for idx, row in enumerate(rows):
        try:
            row = {(k or "").strip().lower(): v for k, v in row.items()}
            missing = REQUIRED_COLUMNS - {k for k, v in row.items() if v not in (None, "")}
            if missing:
                raise ValueError(f"отсутствуют обязательные колонки: {sorted(missing)}")

            competitor_name = str(row["competitor"]).strip()
            creator_name = str(row["creator"]).strip()
            platform = str(row["platform"]).strip().lower()

            competitor_id = stable_id("comp", competitor_name)
            if competitor_id not in competitors_by_id:
                competitors_by_id[competitor_id] = Competitor(
                    competitor_id=competitor_id, name=competitor_name, source_mode=SourceMode.IMPORTED,
                )

            creator_id = stable_id("cr_imported", platform, creator_name)
            if creator_id not in creators_by_id:
                topic = (row.get("topic") or "").strip()
                creators_by_id[creator_id] = Creator(
                    creator_id=creator_id,
                    name=creator_name,
                    platform=platform,
                    followers=_safe_int(row.get("followers")),
                    avg_views=_safe_float(row.get("views")),
                    topic_tags=[topic] if topic else [],
                    source_refs=[row["content_url"]] if row.get("content_url") else [],
                    is_synthetic=False,
                    source_mode=SourceMode.IMPORTED,
                )

            content_url = (row.get("content_url") or "").strip() or None
            raw_text = row.get("raw_text") or None
            published_at = _parse_date(row.get("published_at"))

            dedup_key = content_url or f"{competitor_name}|{creator_name}|{raw_text}"
            integration_id = stable_id("imported", dedup_key)

            ev = fact(
                field="imported_row",
                value={"competitor": competitor_name, "creator": creator_name, "platform": platform},
                source_url=content_url,
                observed_at=published_at,
                raw_fragment=raw_text,
            )
            evidence_store.add(ev)

            integration = Integration(
                integration_id=integration_id,
                competitor_id=competitor_id,
                creator_id=creator_id,
                platform=platform,
                content_url=content_url,
                published_at=published_at,
                content_type=None,
                detected_offer=(row.get("offer") or None),
                detected_cta=(row.get("cta") or None),
                detected_mechanic=(row.get("mechanic") or None),
                raw_text=raw_text,
                evidence=[ev],
                is_synthetic=False,
                source_mode=SourceMode.IMPORTED,
                confidence=1.0,  # заранее собранные публичные данные считаем подтверждённым FACT
                ingestion_source=f"{path.suffix.lstrip('.')}_import",
            )
            report.integrations.append(integration)
            report.rows_imported += 1
        except Exception as exc:  # noqa: BLE001 - одна плохая строка не должна ронять весь импорт
            report.rows_failed += 1
            report.errors.append(f"строка {idx + 1}: {exc}")

    report.competitors = list(competitors_by_id.values())
    report.creators = list(creators_by_id.values())
    return report
