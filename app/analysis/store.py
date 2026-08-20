"""
Простое in-memory хранилище результатов /api/analyze, с опциональной
персистентностью в output/analysis_<id>.json (чтобы результат переживал
перезапуск процесса в рамках демо/дебага, аналогично output/*.json в
app/pipeline.py и app/live_pipeline.py).
"""
from __future__ import annotations

import json
import threading
from typing import Optional

from app.analysis.models import AnalysisResult
from config.settings import OUTPUT_DIR

_lock = threading.Lock()
_store: dict[str, AnalysisResult] = {}

ANALYSIS_OUTPUT_DIR = OUTPUT_DIR / "analyses"


def save_analysis(result: AnalysisResult, persist: bool = True) -> None:
    with _lock:
        _store[result.analysis_id] = result
    if persist:
        ANALYSIS_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path = ANALYSIS_OUTPUT_DIR / f"{result.analysis_id}.json"
        path.write_text(result.model_dump_json(indent=2), encoding="utf-8")


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


def list_analysis_ids() -> list[str]:
    with _lock:
        return list(_store.keys())
