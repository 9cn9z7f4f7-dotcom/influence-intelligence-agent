"""
FastAPI-приложение: API-слой + отдача статического UI-дашборда.

Все данные, которые видит UI, идут из output/*.json / кэша последнего
прогона pipeline - никаких захардкоженных чисел в HTML (раздел 14
мастер-промпта).
"""
from __future__ import annotations

import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response

from app.analysis.models import AnalyzeRequest
from app.analysis.pipeline import run_analysis_with_evidence
from app.analysis.store import get_analysis, get_analysis_evidence, save_analysis
from app.export_xlsx import build_analysis_xlsx
from app.connectors.models import (
    ConnectorHeartbeatRequest,
    ConnectorHeartbeatResponse,
    ConnectorJobsResponse,
    ConnectorRegisterRequest,
    ConnectorRegisterResponse,
    ConnectorResultsAck,
    ConnectorResultsSubmission,
)
from app.connectors.registry import registry as connector_registry
from app.pipeline import run_pipeline
from config.settings import BASE_DIR


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Прогреваем pipeline один раз при старте, чтобы первый запрос UI не ждал.
    try:
        get_result(force_refresh=True)
    except Exception:  # noqa: BLE001 - сервер не должен падать даже если pipeline упал
        _cache["result"] = {
            "overview": {"error": "pipeline_failed_on_startup"},
            "market_map": {}, "competitor_dna": [], "next_moves": [],
            "white_space": {"segments": []}, "our_move": {"opportunities": []},
            "evidence": {}, "health": [],
        }
    yield


app = FastAPI(title="Influence Intelligence Agent", version="0.1.0", lifespan=_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_lock = threading.Lock()
_cache: dict[str, Any] = {"result": None}


def get_result(force_refresh: bool = False) -> dict[str, Any]:
    with _lock:
        if _cache["result"] is None or force_refresh:
            _cache["result"] = run_pipeline()
        return _cache["result"]


@app.get("/api/health")
def api_health() -> Any:
    return get_result()["health"]


@app.get("/api/overview")
def api_overview() -> Any:
    return get_result()["overview"]


@app.get("/api/market-map")
def api_market_map() -> Any:
    return get_result()["market_map"]


@app.get("/api/competitor-dna")
def api_competitor_dna() -> Any:
    return get_result()["competitor_dna"]


@app.get("/api/next-moves")
def api_next_moves() -> Any:
    return get_result()["next_moves"]


@app.get("/api/white-space")
def api_white_space() -> Any:
    return get_result()["white_space"]


@app.get("/api/our-move")
def api_our_move() -> Any:
    return get_result()["our_move"]


@app.get("/api/evidence/{evidence_id}")
def api_evidence(evidence_id: str) -> Any:
    ev = get_result()["evidence"].get(evidence_id)
    if ev is None:
        raise HTTPException(status_code=404, detail="evidence_id не найден")
    return ev


@app.post("/api/pipeline/run")
def api_pipeline_run() -> Any:
    result = get_result(force_refresh=True)
    return {"status": "ok", "overview": result["overview"]}


# ---------------------------------------------------------------------------
# Новый user-flow: Brand -> Platforms -> AnalysisConfig -> Analyze
# ---------------------------------------------------------------------------


@app.post("/api/analyze")
def api_analyze(request: AnalyzeRequest) -> Any:
    """Запускает orchestration pipeline синхронно и возвращает analysis_id.

    Синхронно (без очереди/scheduler - см. do-NOT-build раздел требований):
    для MVP/демо это приемлемо, т.к. YouTube discovery по одному бренду -
    это несколько HTTP-запросов, не тяжёлый batch job.
    """
    analysis_id = f"an_{uuid.uuid4().hex[:12]}"
    try:
        result, evidence = run_analysis_with_evidence(request, analysis_id=analysis_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_analysis(result, evidence=evidence)
    return {"analysis_id": analysis_id}


@app.get("/api/analysis/{analysis_id}")
def api_get_analysis(analysis_id: str) -> Any:
    result = get_analysis(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="analysis_id не найден")
    return result.model_dump()


@app.get("/api/analysis/{analysis_id}/export.xlsx")
def api_export_analysis_xlsx(analysis_id: str) -> Response:
    result = get_analysis(analysis_id)
    if result is None:
        raise HTTPException(status_code=404, detail="analysis_id не найден")
    content = build_analysis_xlsx(result)
    safe_brand = "".join(ch for ch in result.brand.canonical_name if ch.isalnum() or ch in ("-", "_")) or "analysis"
    headers = {"Content-Disposition": f'attachment; filename="{safe_brand}-influence-analysis.xlsx"'}
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers=headers)


@app.get("/api/analysis/{analysis_id}/evidence/{evidence_id}")
def api_get_analysis_evidence(analysis_id: str, evidence_id: str) -> Any:
    if get_analysis(analysis_id) is None:
        raise HTTPException(status_code=404, detail="analysis_id не найден")
    evidence = get_analysis_evidence(analysis_id, evidence_id)
    if evidence is None:
        raise HTTPException(status_code=404, detail="evidence_id не найден")
    return evidence


# ---------------------------------------------------------------------------
# Local connector registration/jobs/results (раздел 11 требований) - НЕ
# arbitrary shell execution: фиксированная схема (см. app/connectors/models.py),
# используется только Instagram/TikTok local_connector/run.py (см.
# LOCAL_CONNECTOR.md), никогда браузером пользователя напрямую.
# ---------------------------------------------------------------------------


@app.post("/api/connectors/register")
def api_connectors_register(request: ConnectorRegisterRequest) -> ConnectorRegisterResponse:
    if not request.has_at_least_one_platform():
        raise HTTPException(status_code=400, detail="supported_platforms не может быть пустым")
    record = connector_registry.register(request.supported_platforms, request.shared_secret)
    if record is None:
        raise HTTPException(status_code=401, detail="неверный shared_secret")
    return ConnectorRegisterResponse(
        connector_id=record.connector_id, connector_token=record.connector_token,
        supported_platforms=record.supported_platforms,
    )


@app.post("/api/connectors/heartbeat")
def api_connectors_heartbeat(request: ConnectorHeartbeatRequest) -> ConnectorHeartbeatResponse:
    from datetime import datetime, timezone

    ok = connector_registry.heartbeat(request.connector_id, request.connector_token, request.status, request.detail)
    if not ok:
        raise HTTPException(status_code=401, detail="неизвестный connector_id/connector_token")
    return ConnectorHeartbeatResponse(ok=True, server_time=datetime.now(timezone.utc).isoformat())


@app.get("/api/connectors/jobs")
def api_connectors_jobs(connector_id: str, connector_token: str, max_jobs: int = 5) -> ConnectorJobsResponse:
    jobs = connector_registry.pop_jobs_for_connector(connector_id, connector_token, max_jobs)
    return ConnectorJobsResponse(jobs=jobs)


@app.post("/api/connectors/results")
def api_connectors_results(submission: ConnectorResultsSubmission) -> ConnectorResultsAck:
    ok = connector_registry.submit_results(submission)
    if not ok:
        raise HTTPException(status_code=401, detail="неизвестный connector_id/connector_token")
    return ConnectorResultsAck(ok=True)


@app.get("/api/connectors/status")
def api_connectors_status() -> Any:
    """Для UI (раздел 20): Instagram/TikTok - Connected / Offline."""
    return {
        platform: dict(zip(("status", "detail"), connector_registry.platform_status(platform)))
        for platform in ("instagram", "tiktok")
    }


static_dir = Path(BASE_DIR) / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
