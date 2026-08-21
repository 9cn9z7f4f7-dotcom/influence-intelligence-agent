"""
Простое in-memory хранилище результатов /api/analyze, с опциональной
персистентностью в output/analyses/<id>.json (чтобы результат переживал
перезапуск процесса в рамках демо/дебага, аналогично output/*.json в
app/pipeline.py и app/live_pipeline.py).
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Mapping, Optional

from app.analysis.models import AnalysisResult
from config.settings import OUTPUT_DIR

_lock = threading.Lock()
_store: dict[str, AnalysisResult] = {}
_evidence_store: dict[str, dict[str, dict[str, Any]]] = {}

ANALYSIS_OUTPUT_DIR = OUTPUT_DIR / "analyses"


def _evidence_path(analysis_id: str) -> Path:
    return ANALYSIS_OUTPUT_DIR / f"{analysis_id}.evidence.json"


def _normalize_evidence_map(evidence: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    if evidence is None:
        return {}
    normalized: dict[str, dict[str, Any]] = {}
    for evidence_id, item in evidence.items():
        if hasattr(item, "model_dump"):
            payload = item.model_dump(mode="json")
        elif isinstance(item, Mapping):
            payload = dict(item)
        else:
            raise TypeError(f"unsupported evidence payload for {evidence_id}")
        normalized[str(evidence_id)] = payload
    return normalized


def save_analysis(
    result: AnalysisResult,
    evidence: Mapping[str, Any] | None = None,
    persist: bool = True,
) -> None:
    evidence_payload = _normalize_evidence_map(evidence)
    with _lock:
        _store[result.analysis_id] = result
        _evidence_store[result.analysis_id] = evidence_payload
    if persist:
        ANALYSIS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = ANALYSIS_OUTPUT_DIR / f"{result.analysis_id}.json"
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        _evidence_path(result.analysis_id).write_text(
            json.dumps(evidence_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def get_analysis(analysis_id: str) -> Optional[AnalysisResult]:
    with _lock:
        cached = _store.get(analysis_id)
    if cached is not None:
        return cached
    path = ANALYSIS_OUTPUT_DIR / f"{analysis_id}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result = AnalysisResult.model_validate(data)
            with _lock:
                _store[analysis_id] = result
            return result
        except Exception:  # noqa: BLE001 - повреждённый файл не должен ронять API
            return None
    return None


def get_analysis_evidence(analysis_id: str, evidence_id: str) -> Optional[dict[str, Any]]:
    """Возвращает evidence только из store конкретного real analysis.

    Legacy/demo cache намеренно не используется. Если процесс перезапустился,
    evidence map лениво загружается из sidecar-файла рядом с AnalysisResult.
    """
    with _lock:
        cached_map = _evidence_store.get(analysis_id)
    if cached_map is None:
        path = _evidence_path(analysis_id)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return None
            cached_map = {
                str(key): dict(value)
                for key, value in raw.items()
                if isinstance(value, dict)
            }
            with _lock:
                _evidence_store[analysis_id] = cached_map
        except Exception:  # noqa: BLE001 - повреждённый sidecar не должен ронять API
            return None
    return cached_map.get(evidence_id)


def list_analysis_ids() -> list[str]:
    with _lock:
        return list(_store.keys())
