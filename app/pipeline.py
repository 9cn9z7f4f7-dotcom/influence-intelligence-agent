"""
Оркестрация полного аналитического pipeline:

    ingestion -> Market Map -> Competitor DNA -> Next Move -> White Space -> Our Move

Работает в двух режимах:
  - demo: только локальный synthetic dataset (data/demo/*.json), без интернета;
  - live: пытается дополнительно опросить реальные источники (YouTube/web) для
    health-статуса и обогащения, но аналитическим "скелетом" остаётся тот же
    demo dataset - конкурентная карта интеграций по реальным
    источникам не входит в объём этого MVP (см. FINAL_READINESS_REPORT.md).

Если один источник падает - pipeline продолжает работу с тем, что есть,
и явно показывает degraded/unavailable статус (никакого silent failure).
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
from app.ingestion.optional_adapters import InstagramAdapter, TelegramAdapter
from app.ingestion.web_adapter import WebAdapter
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import OurProfile
from app.storage import Storage
from config.settings import OUTPUT_DIR, settings


def run_pipeline(mode: str | None = None, persist: bool = True) -> dict[str, Any]:
    mode = (mode or settings.app_mode or "demo").lower()
    health_registry.reset()

    storage = Storage()
    storage.reset()

    loader = DemoLoader()
    demo_result = loader.fetch()
    for c in demo_result.competitors:
        storage.upsert_competitor(c)
    for c in demo_result.creators:
        storage.upsert_creator(c)
    for i in demo_result.integrations:
        storage.upsert_integration(i)

    our_profile_raw = loader.load_our_profile()
    our_profile = OurProfile.model_validate(our_profile_raw) if our_profile_raw else OurProfile()

    notes: list[str] = list(demo_result.notes)

    # Опциональные/live источники - никогда не должны обрушить pipeline.
    if mode == "live":
        yt = YouTubeAdapter()
        try:
            yt_result = yt.fetch(query="influencer marketing") if yt.is_available() else yt.fetch()
            notes.extend(yt_result.notes)
        except Exception as exc:  # noqa: BLE001
            health_registry.degraded("youtube", f"unexpected error: {exc}")

        web = WebAdapter()
        try:
            web_result = web.fetch(url="")
            notes.extend(web_result.notes)
        except Exception as exc:  # noqa: BLE001
            health_registry.degraded("web", f"unexpected error: {exc}")
    else:
        health_registry.ok("youtube", "не опрашивается в demo-режиме (нет необходимости в интернете)")
        health_registry.ok("web", "не опрашивается в demo-режиме (нет необходимости в интернете)")

    # Всегда честно показываем статус адаптеров, которых нет в этом MVP.
    TelegramAdapter().fetch()
    InstagramAdapter().fetch()

    creators = storage.list_creators()
    competitors = storage.list_competitors()
    integrations = storage.list_integrations()

    shared_evidence = EvidenceStore()
    # Регистрируем raw FACT evidence по каждой интеграции (см. app/ingestion/demo_loader.py) -
    # так evidence-цепочка доходит до самого источника, а не только до COMPUTED-агрегатов.
    for integration in integrations:
        shared_evidence.add_many(integration.evidence)

    market_map_builder = MarketMapBuilder(creators, competitors, integrations, settings, shared_evidence)
    market_map = market_map_builder.build()

    dna_builder = CompetitorDnaBuilder(creators, integrations, settings, shared_evidence)
    competitor_dna = [dna_builder.build(c) for c in competitors]

    next_move_builder = NextMoveBuilder(creators, integrations, settings, shared_evidence)
    next_moves = next_move_builder.build_all(competitors)

    white_space_builder = WhiteSpaceBuilder(creators, competitors, integrations, our_profile, settings, shared_evidence)
    white_space = white_space_builder.build()

    our_move_builder = OurMoveBuilder(settings, our_profile)
    our_move = our_move_builder.build(market_map, competitor_dna, next_moves, white_space)

    health_snapshot = health_registry.snapshot()
    degraded_sources = [h["source"] for h in health_snapshot if h["status"] != "ok"]

    overview = {
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "integrations_analyzed": len(integrations),
        "creators_analyzed": len(creators),
        "competitors_analyzed": len(competitors),
        "active_sources": [h["source"] for h in health_snapshot if h["status"] == "ok"],
        "degraded_sources": degraded_sources,
        "is_synthetic_data": all(c.is_synthetic for c in creators) if creators else False,
        "notes": notes,
    }

    result = {
        "overview": overview,
        "market_map": market_map,
        "competitor_dna": competitor_dna,
        "next_moves": next_moves,
        "white_space": white_space,
        "our_move": our_move,
        "evidence": shared_evidence.as_dict(),
        "health": health_snapshot,
        "our_profile": our_profile.model_dump(),
    }

    if persist:
        _persist_outputs(result)

    return result


def _persist_outputs(result: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    mapping = {
        "overview.json": result["overview"],
        "market_map.json": result["market_map"],
        "competitor_dna.json": result["competitor_dna"],
        "next_moves.json": result["next_moves"],
        "white_space.json": result["white_space"],
        "our_move.json": result["our_move"],
        "evidence.json": result["evidence"],
        "health.json": result["health"],
    }
    for filename, payload in mapping.items():
        (OUTPUT_DIR / filename).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
