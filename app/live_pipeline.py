"""
Live/imported data pipeline - отдельно от demo pipeline (app/pipeline.py).

Ключевой принцип (раздел I требований): live/imported данные НИКОГДА не
смешиваются с synthetic demo dataset без явной маркировки. Здесь это
реализовано физически - отдельная SQLite (LIVE_STATE_DB_PATH), отдельные
output/live_*.json, и каждый объект несёт source_mode=live|imported.

Аналитические слои (Market Map/DNA/Next Move/White Space/Our Move) - те же
самые классы из app/analytics/*, без изменений: живые данные проходят через
ровно тот же pipeline, что и demo.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.analytics.competitor_dna import CompetitorDnaBuilder
from app.analytics.market_map import MarketMapBuilder
from app.analytics.next_move import NextMoveBuilder
from app.analytics.our_move import OurMoveBuilder
from app.analytics.white_space import WhiteSpaceBuilder
from app.evidence import EvidenceStore
from app.health import health_registry
from app.ingestion.demo_loader import DemoLoader
from app.ingestion.identifiers import stable_id
from app.ingestion.import_adapter import ImportReport, import_integrations
from app.ingestion.live_youtube import DEFAULT_BRAND_KEYWORDS, LiveIngestionReport, run_youtube_ingestion
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import Competitor, OurProfile, SourceMode
from app.storage import Storage
from config.settings import LIVE_STATE_DB_PATH, OUTPUT_DIR, settings


def get_live_storage() -> Storage:
    return Storage(db_path=LIVE_STATE_DB_PATH)


def _load_our_profile() -> OurProfile:
    raw = DemoLoader().load_our_profile()
    return OurProfile.model_validate(raw) if raw else OurProfile()


# ---------------------------------------------------------------------------
# ingest-youtube
# ---------------------------------------------------------------------------

def ingest_youtube_for_competitor(competitor_name: str, aliases: list[str] | None = None,
                                   brand_keywords: list[str] | None = None) -> LiveIngestionReport:
    storage = get_live_storage()
    competitor_id = stable_id("comp", competitor_name)

    existing = next((c for c in storage.list_competitors() if c.competitor_id == competitor_id), None)
    if not existing:
        storage.upsert_competitor(Competitor(
            competitor_id=competitor_id, name=competitor_name, aliases=aliases or [],
            source_mode=SourceMode.LIVE,
        ))

    adapter = YouTubeAdapter()
    evidence_store = EvidenceStore()
    report = run_youtube_ingestion(
        competitor_id=competitor_id, competitor_name=competitor_name, aliases=aliases,
        brand_keywords=brand_keywords or DEFAULT_BRAND_KEYWORDS, adapter=adapter,
        settings=settings, evidence_store=evidence_store,
    )

    for creator in report.creators:
        storage.upsert_creator(creator)
    for integration in report.confirmed_integrations:
        storage.upsert_integration(integration)  # upsert -> дедуп при повторном запуске (F)

    return report


# ---------------------------------------------------------------------------
# import-integrations
# ---------------------------------------------------------------------------

def import_integrations_file(path: str) -> ImportReport:
    storage = get_live_storage()
    report = import_integrations(path)
    for competitor in report.competitors:
        storage.upsert_competitor(competitor)
    for creator in report.creators:
        storage.upsert_creator(creator)
    for integration in report.integrations:
        storage.upsert_integration(integration)
    return report


# ---------------------------------------------------------------------------
# live-run: ingest (если competitor указан) + прогон существующих аналитических слоёв
# ---------------------------------------------------------------------------

def run_live_analytics(persist: bool = True) -> dict[str, Any]:
    storage = get_live_storage()
    creators = storage.list_creators()
    competitors = storage.list_competitors()
    integrations = storage.list_integrations()
    our_profile = _load_our_profile()

    shared_evidence = EvidenceStore()
    for integration in integrations:
        shared_evidence.add_many(integration.evidence)

    market_map = MarketMapBuilder(creators, competitors, integrations, settings, shared_evidence).build()
    competitor_dna = [
        CompetitorDnaBuilder(creators, integrations, settings, shared_evidence).build(c) for c in competitors
    ]
    next_moves = NextMoveBuilder(creators, integrations, settings, shared_evidence).build_all(competitors)
    white_space = WhiteSpaceBuilder(creators, competitors, integrations, our_profile, settings, shared_evidence).build()
    our_move = OurMoveBuilder(settings, our_profile).build(market_map, competitor_dna, next_moves, white_space)

    overview = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "mode": "live",
        "source_modes_present": sorted({c.source_mode.value for c in creators} | {i.source_mode.value for i in integrations}),
        "integrations_analyzed": len(integrations),
        "creators_analyzed": len(creators),
        "competitors_analyzed": len(competitors),
        "is_synthetic_data": False,
    }

    result = {
        "overview": overview,
        "market_map": market_map,
        "competitor_dna": competitor_dna,
        "next_moves": next_moves,
        "white_space": white_space,
        "our_move": our_move,
        "evidence": shared_evidence.as_dict(),
        "health": health_registry.snapshot(),
    }

    if persist:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        mapping = {
            "live_overview.json": result["overview"],
            "live_market_map.json": result["market_map"],
            "live_competitor_dna.json": result["competitor_dna"],
            "live_next_moves.json": result["next_moves"],
            "live_white_space.json": result["white_space"],
            "live_our_move.json": result["our_move"],
            "live_evidence.json": result["evidence"],
            "live_health.json": result["health"],
        }
        for filename, payload in mapping.items():
            (OUTPUT_DIR / filename).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )

    return result


def run_live_pipeline_for_competitor(competitor_name: str, aliases: list[str] | None = None,
                                      brand_keywords: list[str] | None = None) -> dict[str, Any]:
    """PUBLIC YOUTUBE DATA -> CONFIRMED INTEGRATIONS -> CREATORS -> Market Map -> ... -> Our Move."""
    ingestion_report = ingest_youtube_for_competitor(competitor_name, aliases, brand_keywords)
    analytics = run_live_analytics(persist=True)
    return {"ingestion": ingestion_report, "analytics": analytics}
