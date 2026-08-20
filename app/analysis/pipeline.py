"""
Orchestration pipeline: Brand -> Platforms -> AnalysisConfig -> Analyze.

12 именованных стадий (раздел 6 требований), каждая - отдельная, тестируемая
функция ниже:

    1.  resolve_brand            - BrandResolver (+ реальное резолвление
                                    YouTube handle -> channel title, hotfix #5)
    2.  build_competitor(s)       - Competitor создаётся на лету, + optional
                                    competitor_brands[] (hotfix #6)
    3.  discover_per_platform    - Source Router: discover_brand_content() на
                                    каждой выбранной платформе, честный статус
    4.  detect_and_extract       - категоризация (confirmed/manual_review/
                                    organic_mention/rejected) + extract_creator
    5.  enrich_topics            - topic_tags по НЕСКОЛЬКИМ последним публикациям
                                    (внутри extract_creator, hotfix #2)
    6.  (метрики уже посчитаны внутри extract_creator - несколько видео/постов)
    7.  apply_config_filters     - AnalysisConfig реально фильтрует результат
                                    (категории, confidence, date range, размер,
                                    followers, метрики, темы, geo) - hotfix #4
    8.  build_universe           - независимый DYNAMIC Creator Universe
                                    (hotfix #1: queries строятся из observed
                                    topics бренда / include_topics, НЕ хардкод)
    9.  merge_candidate_pool     - next_move_candidates = universe MINUS used;
                                    white_space supply = universe (hotfix #3)
    10. run_analytical_layers    - 5 существующих builder-ов, БЕЗ ИЗМЕНЕНИЙ
    11. assemble_coverage        - честная coverage/summary/limitations +
                                    min_strategy_match / min_white_space_opportunity
                                    (hotfix #4)
    12. persist_and_return       - AnalysisResult (см. app/analysis/store.py)

Ни на одном этапе не подделываются данные под видом live - если платформа
недоступна, это явно отражено в coverage/limitations (раздел 3).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.analysis.brand_resolver import resolve_brand
from app.analysis.models import (
    AnalysisConfig,
    AnalysisCoverage,
    AnalysisResult,
    AnalysisSummary,
    AnalyzeRequest,
    PlatformCoverage,
    ResolvedBrand,
)
from app.analytics.competitor_dna import CompetitorDnaBuilder
from app.analytics.market_map import MarketMapBuilder
from app.analytics.next_move import NextMoveBuilder
from app.analytics.our_move import OurMoveBuilder
from app.analytics.white_space import WhiteSpaceBuilder
from app.creator_universe import build_creator_universe, next_move_candidate_pool
from app.evidence import EvidenceStore
from app.ingestion.demo_loader import DemoLoader
from app.ingestion.identifiers import stable_id
from app.ingestion.live_youtube import build_integration
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import Competitor, Creator, Integration, OurProfile, SourceMode
from app.platforms import get_platform_adapter
from config.settings import settings as default_settings

# Next Move / White Space нужен буфер кандидатов ДО применения min_strategy_match /
# min_white_space_opportunity - иначе builder сам отрежет top_n раньше фильтра по порогу.
_CANDIDATE_BUFFER_MULTIPLIER = 4
_CANDIDATE_BUFFER_MIN = 20

# ---------------------------------------------------------------------------
# Stage 1-2: brand + competitor(s)
# ---------------------------------------------------------------------------


def stage_resolve_brand(brand_input: str) -> ResolvedBrand:
    return resolve_brand(brand_input)


def stage_resolve_youtube_channel(brand: ResolvedBrand, platforms: list[str]) -> ResolvedBrand:
    """Hotfix #5: если brand пришёл как youtube URL/handle и YouTube API доступен -
    получить РЕАЛЬНЫЕ channel title/id/canonical URL, а не использовать handle как
    единственный brand_name. Если API недоступен/упал - graceful fallback (без
    изменений, handle остаётся canonical_name, как раньше)."""
    if brand.input_type != "url" or brand.detected_platform != "youtube" or not brand.normalized_handle:
        return brand
    if "youtube" not in platforms:
        return brand

    adapter = YouTubeAdapter()
    if not adapter.is_available():
        return brand

    try:
        channel_item = adapter._run_with_retries(adapter.resolve_channel_by_handle, brand.normalized_handle)
    except Exception:  # noqa: BLE001 - резолвинг best-effort, никогда не роняет pipeline
        return brand
    if not channel_item:
        return brand

    snippet = channel_item.get("snippet", {}) or {}
    real_title = snippet.get("title")
    channel_id = channel_item.get("id")
    if not real_title:
        return brand

    new_aliases = list(dict.fromkeys(brand.aliases + [brand.brand_name, brand.normalized_handle]))
    return brand.model_copy(update={
        "brand_name": real_title,
        "canonical_name": real_title,
        "aliases": [a for a in new_aliases if a and a.lower() != real_title.lower()],
        "source_url": f"https://www.youtube.com/channel/{channel_id}" if channel_id else brand.source_url,
    })


def stage_build_competitor(brand: ResolvedBrand) -> Competitor:
    competitor_id = stable_id("comp", brand.canonical_name)
    return Competitor(
        competitor_id=competitor_id,
        name=brand.canonical_name,
        aliases=brand.aliases,
        source_mode=SourceMode.LIVE,
    )


def _load_our_profile() -> OurProfile:
    raw = DemoLoader().load_our_profile()
    return OurProfile.model_validate(raw) if raw else OurProfile()


# ---------------------------------------------------------------------------
# Stage 3-5: per-platform discovery + detection + creator extraction + topics
# ---------------------------------------------------------------------------


def stage_discover_and_extract(
    platform: str, brand: ResolvedBrand, competitor_id: str, config: AnalysisConfig,
    evidence_store: EvidenceStore,
) -> tuple[PlatformCoverage, list[Creator], list[Integration], list[Integration], list[dict]]:
    """Возвращает (coverage, creators, confirmed_integrations, organic_mentions, manual_review_candidates)."""
    adapter = get_platform_adapter(platform)
    discovery = adapter.discover_brand_content(brand, config)

    coverage = PlatformCoverage(
        platform=platform,
        source_mode=discovery.source_mode if discovery.raw_items else "none",
        status=discovery.status,
        reason=discovery.reason,
        items_collected=len(discovery.raw_items),
    )

    if not discovery.raw_items:
        return coverage, [], [], [], []

    brand_terms = [brand.canonical_name] + brand.aliases
    creators_by_id: dict[str, Creator] = {}
    confirmed: list[Integration] = []
    organic: list[Integration] = []
    manual_review: list[dict] = []

    for raw_item in discovery.raw_items:
        detector_result = adapter.detect_integration(raw_item, brand_terms)

        if detector_result.category == "rejected":
            continue

        if detector_result.category == "manual_review":
            manual_review.append({
                "platform": platform,
                "confidence": detector_result.confidence,
                "reasons": detector_result.reasons,
                "status": "candidate_manual_review",
            })
            continue

        # confirmed | organic_mention - у обоих brand evidence точно есть.
        snippet = raw_item.get("snippet", {}) or {}
        channel_id = snippet.get("channelId")
        cache_key = channel_id or id(raw_item)

        if cache_key not in creators_by_id:
            # extract_creator сам определяет topic_tags по нескольким последним
            # публикациям канала (hotfix #2, см. app/platforms/youtube.py) -
            # НЕ переопределяем это здесь по одному триггерному видео.
            creator = adapter.extract_creator(raw_item)
            if creator is None:
                continue
            creators_by_id[cache_key] = adapter.normalize_creator(creator)

        creator = creators_by_id.get(cache_key)
        if not creator:
            continue

        integration = build_integration(competitor_id, creator, raw_item, None, detector_result, evidence_store)
        integration = adapter.normalize_integration(integration)

        if detector_result.category == "confirmed":
            confirmed.append(integration)
        else:
            organic.append(integration)

    return coverage, list(creators_by_id.values()), confirmed, organic, manual_review


# ---------------------------------------------------------------------------
# Stage 7: AnalysisConfig реально фильтрует результат (не просто хранится)
# ---------------------------------------------------------------------------


def _date_window(config: AnalysisConfig) -> tuple[datetime, datetime]:
    if config.date_range == "custom" and config.custom_start and config.custom_end:
        start = datetime.combine(config.custom_start, datetime.min.time(), tzinfo=timezone.utc)
        end = datetime.combine(config.custom_end, datetime.max.time(), tzinfo=timezone.utc)
        return start, end
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=config.date_range_days())
    return start, end


def stage_apply_date_filter(integrations: list[Integration], config: AnalysisConfig) -> list[Integration]:
    """Hotfix #4: интеграции вне date range не должны участвовать в анализе.
    Интеграция без published_at не может быть подтверждена как "внутри диапазона" -
    честно исключается, а не додумывается."""
    start, end = _date_window(config)
    result = []
    for i in integrations:
        if i.published_at is None:
            continue
        pub = i.published_at if i.published_at.tzinfo else i.published_at.replace(tzinfo=timezone.utc)
        if start <= pub <= end:
            result.append(i)
    return result


def stage_apply_config_filters(
    creators: list[Creator], integrations: list[Integration], config: AnalysisConfig,
) -> tuple[list[Creator], list[Integration]]:
    allowed_categories = config.allowed_integration_categories()

    def _passes_confidence(integration: Integration) -> bool:
        # min_integration_confidence относится к confirmed-интеграциям (насколько
        # уверенно detector категоризировал именно КОММЕРЧЕСКУЮ интеграцию).
        # organic_mention по определению может иметь низкий confidence (там нет
        # коммерческого сигнала вообще) - фильтровать его по этому порогу не имеет смысла.
        if integration.category != "confirmed":
            return True
        return integration.confidence is None or integration.confidence >= config.min_integration_confidence

    integrations = stage_apply_date_filter(integrations, config)

    filtered_integrations = [
        i for i in integrations if i.category in allowed_categories and _passes_confidence(i)
    ][: config.max_integrations]

    kept_creator_ids = {i.creator_id for i in filtered_integrations}
    filtered_creators = [
        c for c in creators
        if config.matches_creator_size(_bucket_for_creator(c, config))
        and config.matches_followers(c.followers)
        and config.matches_metrics(c.median_views, c.avg_views, c.engagement_rate)
        and config.matches_topics(c.topic_tags)
        and config.matches_geo(c.geo, c.language)
    ][: config.max_creators]

    filtered_creator_ids = {c.creator_id for c in filtered_creators}
    # Интеграции держим только у прошедших фильтр креаторов, чтобы не показывать
    # "интеграцию" с креатором, которого сами же отфильтровали настройками.
    filtered_integrations = [i for i in filtered_integrations if i.creator_id in filtered_creator_ids or not kept_creator_ids]

    return filtered_creators, filtered_integrations


def _bucket_for_creator(creator: Creator, config: AnalysisConfig) -> str | None:
    return default_settings.bucket_for_value(creator.followers, default_settings.follower_buckets)


# ---------------------------------------------------------------------------
# Stage 8-9: Creator Universe (DYNAMIC, независимый пул) + candidate pools
# ---------------------------------------------------------------------------


def _observed_topics(creators: list[Creator], max_topics: int = 5) -> list[str]:
    """Темы, реально наблюдаемые в найденном контенте бренда - используются как
    discovery seeds для Creator Universe, если пользователь не задал include_topics
    (hotfix #1 - НЕ захардкоженный education/student default)."""
    counts: dict[str, int] = {}
    for c in creators:
        for tag in (c.topic_tags or []):
            if tag and tag != "other":
                counts[tag] = counts.get(tag, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return [topic for topic, _ in ranked[:max_topics]]


def stage_build_universe_pool(
    platforms: list[str], config: AnalysisConfig, observed_topics: list[str],
) -> tuple[list[Creator], str, list[str], list[str]]:
    """Возвращает (universe_creators, status, notes, queries_used).
    Сейчас live только для YouTube."""
    if "youtube" not in platforms:
        return [], "unavailable", ["Creator Universe пока реализован только для YouTube"], []
    universe = build_creator_universe(config, observed_topics=observed_topics)
    return universe.creators, universe.status, universe.notes, universe.queries_used


# ---------------------------------------------------------------------------
# Stage 10: 5 аналитических слоёв - БЕЗ ИЗМЕНЕНИЙ (переиспользуем как есть)
# ---------------------------------------------------------------------------


def stage_run_analytical_layers(
    creators_for_next_move: list[Creator], creators_for_white_space: list[Creator],
    creators_for_market_map: list[Creator], competitors: list[Competitor],
    integrations: list[Integration], config: AnalysisConfig, evidence_store: EvidenceStore,
) -> tuple[dict, list[dict], list[dict], dict, dict]:
    our_profile = _load_our_profile()

    market_map = MarketMapBuilder(
        creators_for_market_map, competitors, integrations, default_settings, evidence_store,
    ).build()
    competitor_dna = [
        CompetitorDnaBuilder(creators_for_market_map, integrations, default_settings, evidence_store).build(c)
        for c in competitors
    ]

    candidate_buffer = max(_CANDIDATE_BUFFER_MIN, config.max_next_move_candidates * _CANDIDATE_BUFFER_MULTIPLIER)
    next_move_builder = NextMoveBuilder(
        creators_for_next_move, integrations, default_settings, evidence_store, top_n=candidate_buffer,
    )
    next_move_raw = next_move_builder.build_all(competitors)
    next_move = []
    for entry in next_move_raw:
        candidates = [c for c in entry.get("candidates", []) if c["similarity_score"] >= config.min_strategy_match]
        entry = {**entry, "candidates": candidates[: config.max_next_move_candidates]}
        next_move.append(entry)

    # Hotfix #3: White Space SUPPLY = независимый creator universe (+ brand creators,
    # чтобы их интеграции не "потерялись" при подсчёте saturation по сегментам) -
    # НЕ только креаторы из интеграций бренда/конкурентов.
    white_space_raw = WhiteSpaceBuilder(
        creators_for_white_space, competitors, integrations, our_profile, default_settings, evidence_store,
    ).build()
    filtered_segments = [
        s for s in white_space_raw.get("segments", []) if s["opportunity_score"] >= config.min_white_space_opportunity
    ][: config.max_white_space_segments]
    white_space = {**white_space_raw, "segments": filtered_segments}

    our_move = OurMoveBuilder(default_settings, our_profile).build(market_map, competitor_dna, next_move, white_space)

    return market_map, competitor_dna, next_move, white_space, our_move


# ---------------------------------------------------------------------------
# Главный оркестратор
# ---------------------------------------------------------------------------


def _process_brand(
    brand_input: str, platforms: list[str], config: AnalysisConfig, evidence_store: EvidenceStore,
) -> tuple[ResolvedBrand, Competitor, list[PlatformCoverage], list[Creator], list[Integration], list[dict]]:
    """Resolve + discover + extract для ОДНОГО бренда (основного или optional
    конкурента, hotfix #6). Возвращает (brand, competitor, coverages, creators,
    integrations_all_categories, manual_review_candidates)."""
    brand = stage_resolve_brand(brand_input)
    brand = stage_resolve_youtube_channel(brand, platforms)
    competitor = stage_build_competitor(brand)

    coverages: list[PlatformCoverage] = []
    creators: list[Creator] = []
    integrations: list[Integration] = []
    manual_review: list[dict] = []

    for platform in platforms:
        coverage, plat_creators, confirmed, organic, plat_manual_review = stage_discover_and_extract(
            platform, brand, competitor.competitor_id, config, evidence_store,
        )
        coverages.append(coverage)
        creators.extend(plat_creators)
        integrations.extend(confirmed)
        integrations.extend(organic)
        manual_review.extend(plat_manual_review)

    return brand, competitor, coverages, creators, integrations, manual_review


def _run_analysis_internal(request: AnalyzeRequest, analysis_id: str) -> tuple[AnalysisResult, list[str]]:
    config = request.settings
    evidence_store = EvidenceStore()

    platform_coverages: list[PlatformCoverage] = []
    all_creators: list[Creator] = []
    all_integrations: list[Integration] = []
    manual_review_total: list[dict] = []
    live_sources: list[str] = []
    imported_sources: list[str] = []
    degraded_sources: list[str] = []
    competitors: list[Competitor] = []

    # Stage 1-5: основной бренд
    brand, primary_competitor, primary_coverages, primary_creators, primary_integrations, primary_manual_review = (
        _process_brand(request.brand, request.platforms, config, evidence_store)
    )
    competitors.append(primary_competitor)
    platform_coverages.extend(primary_coverages)
    all_creators.extend(primary_creators)
    all_integrations.extend(primary_integrations)
    manual_review_total.extend(primary_manual_review)

    # Hotfix #6: optional competitor_brands[] - single-brand mode работает честно,
    # если пусто (см. limitations ниже).
    for competitor_brand_input in request.competitor_brands:
        _, extra_competitor, extra_coverages, extra_creators, extra_integrations, extra_manual_review = (
            _process_brand(competitor_brand_input, request.platforms, config, evidence_store)
        )
        competitors.append(extra_competitor)
        platform_coverages.extend(extra_coverages)
        all_creators.extend(extra_creators)
        all_integrations.extend(extra_integrations)
        manual_review_total.extend(extra_manual_review)

    for coverage in platform_coverages:
        if coverage.source_mode == "live" and coverage.platform not in live_sources:
            live_sources.append(coverage.platform)
        if coverage.status == "degraded" and coverage.platform not in degraded_sources:
            degraded_sources.append(coverage.platform)

    # Stage 7 (+ date filter)
    filtered_creators, filtered_integrations = stage_apply_config_filters(all_creators, all_integrations, config)

    # Stage 8: DYNAMIC universe - seeds из include_topics ИЛИ observed topics бренда
    # (hotfix #1) - НЕ захардкоженная тема.
    observed_topics = _observed_topics(all_creators)
    universe_creators, universe_status, universe_notes, universe_queries = stage_build_universe_pool(
        request.platforms, config, observed_topics,
    )
    if universe_status == "degraded":
        degraded_sources.append("creator_universe")

    # Stage 9: next_move_candidates = creator_universe MINUS brand_used_creators;
    # white_space supply = universe (+ brand creators, чтобы их интеграции не
    # "терялись" при атрибуции по сегментам).
    used_creator_ids = {i.creator_id for i in filtered_integrations}
    universe_pool_placeholder = type("U", (), {"creators": universe_creators})()
    universe_minus_used = next_move_candidate_pool(universe_pool_placeholder, used_creator_ids)
    creators_for_next_move = filtered_creators + universe_minus_used

    seen_ids = {c.creator_id for c in filtered_creators}
    creators_for_white_space = list(filtered_creators) + [c for c in universe_creators if c.creator_id not in seen_ids]

    # Stage 10 - существующие 5 слоёв, без изменений
    market_map, competitor_dna, next_move, white_space, our_move = stage_run_analytical_layers(
        creators_for_next_move, creators_for_white_space, filtered_creators, competitors,
        filtered_integrations, config, evidence_store,
    )

    # Stage 11 - честная coverage/summary/limitations
    limitations: list[str] = []
    for coverage in platform_coverages:
        if coverage.status == "unavailable":
            limitations.append(
                f"{coverage.platform}: live-данные недоступны ({coverage.reason}). "
                f"Доступен импорт вручную собранных данных (CSV/JSON)."
            )
        elif coverage.status == "degraded":
            limitations.append(f"{coverage.platform}: данные собраны частично ({coverage.reason})")
    if universe_status != "ok":
        limitations.extend(universe_notes)
    if manual_review_total:
        limitations.append(
            f"{len(manual_review_total)} найденных материалов требуют ручной проверки "
            f"(brand+commercial evidence есть, но confidence ниже порога) - не включены в результат."
        )
    if not request.competitor_brands:
        limitations.append("Competitive saturation is based only on the analyzed brand.")

    coverage_obj = AnalysisCoverage(
        sources=list(dict.fromkeys(c.platform for c in platform_coverages)),
        live_sources=live_sources,
        imported_sources=imported_sources,
        degraded_sources=degraded_sources,
        platforms=platform_coverages,
    )
    summary = AnalysisSummary(
        integrations_found=len(filtered_integrations),
        creators_used=len(used_creator_ids),
        creator_universe_size=len(universe_creators),
    )

    result = AnalysisResult(
        analysis_id=analysis_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        brand=brand,
        platforms=request.platforms,
        settings=config,
        coverage=coverage_obj,
        summary=summary,
        market_map=market_map,
        competitor_dna=competitor_dna,
        next_move=next_move,
        white_space=white_space,
        our_move=our_move,
        limitations=limitations,
    )
    return result, universe_queries


def run_analysis(request: AnalyzeRequest, analysis_id: str) -> AnalysisResult:
    result, _queries = _run_analysis_internal(request, analysis_id)
    return result


def run_analysis_with_debug(request: AnalyzeRequest, analysis_id: str) -> tuple[AnalysisResult, list[str]]:
    """Как run_analysis(), но дополнительно возвращает queries, реально
    использованные Creator Universe discovery (hotfix #1 verification), без
    изменения публичной схемы AnalysisResult."""
    return _run_analysis_internal(request, analysis_id)
