#!/usr/bin/env python
"""
CLI управления проектом Influence Intelligence Agent.

Команды:
    python manage.py demo-reset                        - очистить demo state (SQLite + output/*.json)
    python manage.py demo-run                           - полный прогон demo pipeline end-to-end
    python manage.py serve                               - поднять API + UI (uvicorn)
    python manage.py ingest-youtube --competitor "..."   - live YouTube discovery + detector -> Creator/Integration
    python manage.py import-integrations file.csv         - импорт заранее собранных публичных данных
    python manage.py live-run --competitor "..."          - ingest-youtube + полный аналитический pipeline на live-данных
"""
from __future__ import annotations

import os

import click

from app.pipeline import run_pipeline
from app.storage import Storage
from config.settings import OUTPUT_DIR


@click.group()
def cli() -> None:
    """Influence Intelligence Agent - management CLI."""


@cli.command("demo-reset")
def demo_reset() -> None:
    """Очищает demo state: SQLite storage + output/*.json."""
    storage = Storage()
    storage.reset()
    if OUTPUT_DIR.exists():
        for f in OUTPUT_DIR.glob("*.json"):
            f.unlink()
    click.echo("Demo state очищен (SQLite + output/*.json).")


@cli.command("demo-run")
def demo_run() -> None:
    """Полный прогон: load demo dataset -> Market Map -> DNA -> Next Move -> White Space -> Our Move."""
    demo_reset.callback()  # type: ignore[attr-defined]
    result = run_pipeline(mode="demo", persist=True)

    overview = result["overview"]
    white_spaces_found = len([s for s in result["white_space"]["segments"] if s["our_relevance"] > 0 and s["opportunity_score"] >= 50])
    next_targets_found = sum(
        1 for nm in result["next_moves"] for c in nm.get("candidates", []) if c["similarity_score"] >= 50
    )

    click.echo("")
    click.echo("DEMO READY")
    click.echo(f"- Integrations analyzed: {overview['integrations_analyzed']}")
    click.echo(f"- Creators analyzed: {overview['creators_analyzed']}")
    click.echo(f"- Competitors analyzed: {overview['competitors_analyzed']}")
    click.echo(f"- White spaces found: {white_spaces_found}")
    click.echo(f"- Next targets found: {next_targets_found}")
    if overview["degraded_sources"]:
        click.echo(f"- Degraded/unavailable sources (ожидаемо в demo-режиме): {', '.join(overview['degraded_sources'])}")
    click.echo(f"- Output JSON: {OUTPUT_DIR}")


@cli.command("serve")
@click.option("--host", default=lambda: os.environ.get("HOST", "0.0.0.0"))
@click.option("--port", default=lambda: int(os.environ.get("PORT", 8000)), type=int)
@click.option("--reload", is_flag=True, default=False)
def serve(host: str, port: int, reload: bool) -> None:
    """Запускает FastAPI сервер (API + статический UI).

    Локально: 'python manage.py serve' -> 0.0.0.0:8000 (если HOST/PORT не заданы).
    На Render/аналогичных PaaS: HOST/PORT берутся из env автоматически -
    ничего дополнительно передавать не нужно.
    """
    import uvicorn

    uvicorn.run("app.api.server:app", host=host, port=port, reload=reload)


def _parse_csv_list(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [v.strip() for v in value.split(",") if v.strip()]


@cli.command("ingest-youtube")
@click.option("--competitor", required=True, help="Canonical-имя конкурента, напр. \"Автор24\"")
@click.option("--aliases", default=None, help="Через запятую: \"Author24,avtor24\"")
@click.option("--brand-keywords", default=None, help="Через запятую - переопределить дефолтные ключевые слова")
def ingest_youtube(competitor: str, aliases: str | None, brand_keywords: str | None) -> None:
    """LIVE: YouTube discovery + deterministic detector -> Creator/Integration (без дальнейшей аналитики)."""
    from app.live_pipeline import ingest_youtube_for_competitor

    report = ingest_youtube_for_competitor(
        competitor, aliases=_parse_csv_list(aliases), brand_keywords=_parse_csv_list(brand_keywords),
    )
    _print_ingestion_report(report)


@cli.command("import-integrations")
@click.argument("file_path", type=click.Path(exists=True))
def import_integrations_cmd(file_path: str) -> None:
    """Импорт CSV/JSON с заранее собранными публичными интеграциями (Instagram/Telegram/др.)."""
    from app.live_pipeline import import_integrations_file

    report = import_integrations_file(file_path)
    click.echo("")
    click.echo("IMPORT DONE")
    click.echo(f"- Rows total: {report.rows_total}")
    click.echo(f"- Imported: {report.rows_imported}")
    click.echo(f"- Failed: {report.rows_failed}")
    if report.errors:
        click.echo("- Errors:")
        for err in report.errors[:10]:
            click.echo(f"    {err}")
    click.echo(f"- Competitors: {[c.name for c in report.competitors]}")
    click.echo(f"- Creators: {len(report.creators)}")


@cli.command("live-run")
@click.option("--competitor", required=True, help="Canonical-имя конкурента, напр. \"Автор24\"")
@click.option("--aliases", default=None, help="Через запятую")
@click.option("--brand-keywords", default=None, help="Через запятую")
def live_run(competitor: str, aliases: str | None, brand_keywords: str | None) -> None:
    """PUBLIC YOUTUBE DATA -> CONFIRMED INTEGRATIONS -> CREATORS -> Market Map -> DNA -> Next Move -> White Space -> Our Move."""
    from app.live_pipeline import run_live_pipeline_for_competitor

    result = run_live_pipeline_for_competitor(
        competitor, aliases=_parse_csv_list(aliases), brand_keywords=_parse_csv_list(brand_keywords),
    )
    _print_ingestion_report(result["ingestion"])

    analytics = result["analytics"]
    overview = analytics["overview"]
    click.echo("")
    click.echo("LIVE ANALYTICS")
    click.echo(f"- source_modes_present: {overview['source_modes_present']}")
    click.echo(f"- Integrations analyzed: {overview['integrations_analyzed']}")
    click.echo(f"- Creators analyzed: {overview['creators_analyzed']}")
    click.echo(f"- Competitors analyzed: {overview['competitors_analyzed']}")
    click.echo(f"- Our Move opportunities: {len(analytics['our_move']['opportunities'])}")
    click.echo(f"- Output JSON: {OUTPUT_DIR} (live_*.json)")


@cli.command("analyze")
@click.option("--brand", required=True, help="Имя бренда ИЛИ ссылка на его аккаунт")
@click.option("--platform", "platforms", multiple=True, default=("youtube",),
              help="Можно указать несколько раз: --platform youtube --platform instagram")
@click.option("--date-range", default="90d", type=click.Choice(["7d", "30d", "90d", "custom"]))
@click.option("--confirmed-only", is_flag=True, default=False)
@click.option("--include-topics", default=None, help="Через запятую")
@click.option("--min-followers", default=None, type=int)
def analyze_cmd(brand: str, platforms: tuple[str, ...], date_range: str, confirmed_only: bool,
                 include_topics: str | None, min_followers: int | None) -> None:
    """DEBUG-ONLY: тот же orchestration pipeline, что /api/analyze (см. раздел 5 требований -
    основной user-flow - через UI/API; эта команда - для отладки без браузера)."""
    import uuid

    from app.analysis.models import AnalysisConfig, AnalyzeRequest
    from app.analysis.pipeline import run_analysis
    from app.analysis.store import save_analysis

    config = AnalysisConfig(
        date_range=date_range, confirmed_only=confirmed_only,
        include_topics=_parse_csv_list(include_topics) or [],
        min_followers=min_followers,
    )
    request = AnalyzeRequest(brand=brand, platforms=list(platforms), settings=config)
    analysis_id = f"an_{uuid.uuid4().hex[:12]}"
    result = run_analysis(request, analysis_id=analysis_id)
    save_analysis(result)

    click.echo("")
    click.echo("ANALYZE DONE")
    click.echo(f"- analysis_id: {analysis_id}")
    click.echo(f"- brand: {result.brand.canonical_name} (input_type={result.brand.input_type})")
    for cov in result.coverage.platforms:
        click.echo(f"- platform {cov.platform}: status={cov.status} source_mode={cov.source_mode} "
                    f"items={cov.items_collected}" + (f" reason={cov.reason}" if cov.reason else ""))
    click.echo(f"- integrations_found: {result.summary.integrations_found}")
    click.echo(f"- creators_used: {result.summary.creators_used}")
    click.echo(f"- creator_universe_size: {result.summary.creator_universe_size}")
    click.echo(f"- next_move candidates: {sum(len(n.get('candidates', [])) for n in result.next_move)}")
    if result.limitations:
        click.echo("- limitations:")
        for lim in result.limitations:
            click.echo(f"    {lim}")
    click.echo(f"- Saved to: output/analyses/{analysis_id}.json")


def _print_ingestion_report(report) -> None:
    click.echo("")
    click.echo("PUBLIC YOUTUBE DATA -> CONFIRMED INTEGRATIONS")
    click.echo(f"- Competitor: {report.competitor_name}")
    click.echo(f"- Queries run: {len(report.queries)}")
    click.echo(f"- Videos found (search pool): {report.videos_found}")
    click.echo(f"- Filtered out (below confidence threshold, manual_review): {report.videos_filtered_out}")
    click.echo(f"- Confirmed integrations: {len(report.confirmed_integrations)}")
    click.echo(f"- Creators (channels): {len(report.creators)}")
    if report.quota_exceeded:
        click.echo("- ⚠ YouTube API квота/лимит превышен во время этого прогона - результат частичный.")
    if report.notes:
        click.echo("- Notes:")
        for note in report.notes[:10]:
            click.echo(f"    {note}")
    for integration in report.confirmed_integrations[:3]:
        click.echo(f"    source_url: {integration.content_url} (confidence={integration.confidence})")


if __name__ == "__main__":
    cli()
