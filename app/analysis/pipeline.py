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

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

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
from app.detection import combine_dom_and_visual, should_escalate_to_visual_evidence
from app.enrichment.screenshot import ScreenshotCache
from app.enrichment.visual_evidence import VisualEvidenceEnricher
from app.evidence import EvidenceStore, make_evidence_id
from app.ingestion.demo_loader import DemoLoader
from app.ingestion.identifiers import stable_id
from app.ingestion.live_youtube import build_integration, reset_search_budget
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import (
    Competitor,
    Creator,
    Evidence,
    EvidenceType,
    Integration,
    OurProfile,
    PotentialCreatorSignal,
    Publisher,
    SourceMode,
)
from app.platforms import get_platform_adapter
from app.platforms.social_connector_base import build_social_integration
from app.potential_creator import build_potential_creator_signal
from app.runtime_budget import budget_exhausted, clear_budget, start_budget
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


def _visual_evidence_inputs(platform: str, raw_item: dict) -> tuple[str | None, str]:
    """Достаёт (content_url, extracted_text) из raw_item конкретной платформы -
    используется ТОЛЬКО для screenshot+vision escalation ambiguous-кейсов
    (раздел 2-3), не для основной detect_integration-логики."""
    if platform == "youtube":
        snippet = raw_item.get("snippet", {}) or {}
        video_id = (raw_item.get("id") or {}).get("videoId")
        url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
        return url, f"{snippet.get('title', '')} {snippet.get('description', '')}"
    if platform == "articles":
        parsed = raw_item.get("parsed")
        if parsed is None:
            return None, ""
        return (parsed.canonical_url or parsed.source_url), (parsed.main_text or "")
    # instagram/tiktok
    return raw_item.get("post_url") or raw_item.get("profile_url"), raw_item.get("caption") or ""


def _maybe_escalate_with_visual_evidence(
    platform: str, raw_item: dict, detector_result, brand: ResolvedBrand,
    visual_enricher: VisualEvidenceEnricher | None, screenshot_cache: ScreenshotCache | None,
    evidence_store: EvidenceStore,
):
    """Раздел 2-3 требований: screenshot+vision ТОЛЬКО для manual_review (см.
    app.detection.should_escalate_to_visual_evidence) - "не делать screenshot
    каждой страницы". Vision result = AI_INFERENCE (EvidenceType.VISUAL_AI),
    никогда FACT, и никогда не создаёт Integration сама по себе - может только
    поднять manual_review -> confirmed через app.detection.combine_dom_and_visual,
    если DOM/API evidence уже была найдена."""
    if visual_enricher is None or screenshot_cache is None:
        return detector_result
    if not should_escalate_to_visual_evidence(detector_result.category):
        return detector_result
    if not visual_enricher.is_available():
        return detector_result

    content_url, extracted_text = _visual_evidence_inputs(platform, raw_item)
    if not content_url:
        return detector_result

    screenshot = screenshot_cache.get_or_capture(content_url)
    visual_result = visual_enricher.enrich(content_url, screenshot, extracted_text, brand.canonical_name, brand.aliases)
    if not visual_result.is_usable():
        return detector_result

    new_category, new_confidence, used = combine_dom_and_visual(
        detector_result.category, detector_result.confidence, visual_result.commercial_signal_visible,
        visual_result.confidence, default_settings.live_integration_confidence_threshold,
    )
    if not used:
        return detector_result

    evidence_store.add(Evidence(
        evidence_id=make_evidence_id("visual_ai", content_url, str(visual_result.signals)),
        source_url=content_url, observed_at=None, type=EvidenceType.VISUAL_AI,
        field="visual_commercial_signal", value=visual_result.signals,
        confidence=visual_result.confidence, raw_fragment=("; ".join(visual_result.evidence)[:500] or None),
    ))
    return replace(detector_result, category=new_category, confidence=new_confidence)


def _build_potential_creator_entry(platform: str, raw_item: dict, detector_result, adapter) -> Optional[dict]:
    """Раздел 2 доработки: brand evidence есть, hard commercial signal - нет,
    но видна organic brand affinity ("ношу", "рекомендую" и т.п.) - строит
    PotentialCreatorSignal + (когда есть) реальный Creator, чтобы платформа
    могла попасть в Creator Universe (раздел 9), НЕ становясь Integration
    (раздел 2/11: никогда не увеличивает confirmed/organic integrations)."""
    affinity_signals = [r.split("affinity:", 1)[1] for r in detector_result.reasons if r.startswith("affinity:")]
    potential_reason = affinity_signals[0] if affinity_signals else "organic_brand_affinity"

    creator: Optional[Creator] = None
    source_url: Optional[str] = None
    observed_at = None

    if platform == "articles":
        # Websites/publishers are never creator-like entities. Article affinity
        # stays visible as a finding/publisher signal, but must not enter the
        # creator universe, Next Move or hunting candidates.
        return None
    else:
        try:
            creator = adapter.extract_creator(raw_item)
        except Exception:  # noqa: BLE001 - potential-creator extraction - best effort, никогда не роняет discovery
            creator = None
        if creator is not None:
            creator = adapter.normalize_creator(creator)
            source_url = creator.canonical_url
        if platform == "youtube":
            video_id = (raw_item.get("id") or {}).get("videoId")
            if video_id:
                source_url = f"https://www.youtube.com/watch?v={video_id}"
        else:
            source_url = raw_item.get("post_url") or raw_item.get("profile_url") or source_url

    signal = build_potential_creator_signal(
        platform=platform, potential_reason=potential_reason, brand_affinity_signals=affinity_signals,
        creator_id=creator.creator_id if creator else None, creator_name=creator.name if creator else None,
        source_url=source_url, observed_at=observed_at,
    )
    return {"status": "potential_creator", "category": "potential_creator", "platform": platform,
            "creator": creator, "signal": signal}




def _youtube_content_finding_entry(raw_item: dict, detector_result) -> dict | None:
    source_url = raw_item.get("_web_source_url")
    if not source_url:
        return None
    snippet = raw_item.get("snippet", {}) or {}
    return {
        "status": "content_finding",
        "platform": "youtube",
        "source_url": source_url,
        "title": snippet.get("title") or "YouTube видео",
        "preview": (snippet.get("description") or "")[:320] or None,
        "classification": detector_result.category,
        "signals": [name for name, sig in (detector_result.signals or {}).items() if sig.get("matched")],
    }

def stage_discover_and_extract(
    platform: str, brand: ResolvedBrand, competitor_id: str, config: AnalysisConfig,
    evidence_store: EvidenceStore, visual_enricher: VisualEvidenceEnricher | None = None,
    screenshot_cache: ScreenshotCache | None = None,
) -> tuple[PlatformCoverage, list[Creator], list[Integration], list[Integration], list[dict], list[Publisher]]:
    """Возвращает (coverage, creators, confirmed_integrations, organic_mentions,
    manual_review_candidates, publishers). publishers - непусто только для
    platform=="articles" (раздел 8: Publisher != Creator)."""
    adapter = get_platform_adapter(platform)
    discovery = adapter.discover_brand_content(brand, config)

    coverage = PlatformCoverage(
        platform=platform,
        source_mode=discovery.source_mode,
        status=discovery.status,
        reason=discovery.reason,
        items_collected=(discovery.candidate_count if discovery.candidate_count is not None else len(discovery.raw_items)),
        items_checked=(discovery.accepted_count if discovery.accepted_count is not None else 0),
        search_provider=discovery.search_provider,
    )

    if not discovery.raw_items:
        return coverage, [], [], [], [], []

    brand_terms = [brand.canonical_name] + brand.aliases
    creators_by_id: dict[str, Creator] = {}
    confirmed: list[Integration] = []
    organic: list[Integration] = []
    manual_review: list[dict] = []
    publishers: list[Publisher] = []
    seen_publisher_ids: set[str] = set()

    for raw_item in discovery.raw_items:
        detector_result = adapter.detect_integration(raw_item, brand_terms)

        if detector_result.category == "rejected":
            continue

        if detector_result.category == "manual_review":
            detector_result = _maybe_escalate_with_visual_evidence(
                platform, raw_item, detector_result, brand, visual_enricher, screenshot_cache, evidence_store,
            )

        if detector_result.category == "manual_review":
            manual_review.append({
                "platform": platform,
                "confidence": detector_result.confidence,
                "reasons": detector_result.reasons,
                "status": "candidate_manual_review",
            })
            continue

        # --- Potential creator (раздел 2 доработки): brand evidence + organic
        # affinity, БЕЗ hard commercial signal - НЕ Integration, но не выбрасывается
        # (см. _build_potential_creator_entry выше; собирается в тот же список
        # manual_review, чтобы не менять сигнатуру этой функции - app/analysis/pipeline.py
        # ниже различает записи по "status") --------------------------------------
        if detector_result.category == "potential_creator":
            entry = _build_potential_creator_entry(platform, raw_item, detector_result, adapter)
            if entry is not None:
                manual_review.append(entry)
            elif platform == "youtube":
                content_entry = _youtube_content_finding_entry(raw_item, detector_result)
                if content_entry is not None:
                    manual_review.append(content_entry)
            continue

        # --- Articles: отдельный путь, БЕЗ Creator (раздел 8) -----------------
        if platform == "articles":
            integration, publisher = adapter.build_article_integration(raw_item, competitor_id, evidence_store)
            integration = adapter.normalize_integration(integration)
            if publisher.publisher_id not in seen_publisher_ids:
                seen_publisher_ids.add(publisher.publisher_id)
                publishers.append(publisher)
            if detector_result.category == "confirmed":
                confirmed.append(integration)
            else:
                organic.append(integration)
            continue

        # --- YouTube/Instagram/TikTok: обычный creator-based путь -------------
        # confirmed | organic_mention - у обоих brand evidence точно есть.
        snippet = raw_item.get("snippet", {}) or {} if platform == "youtube" else {}
        channel_id = snippet.get("channelId") if platform == "youtube" else None
        cache_key = channel_id or raw_item.get("username") or id(raw_item)

        if cache_key not in creators_by_id:
            # extract_creator сам определяет topic_tags по нескольким последним
            # публикациям канала (hotfix #2, см. app/platforms/youtube.py) -
            # НЕ переопределяем это здесь по одному триггерному видео.
            creator = adapter.extract_creator(raw_item)
            if creator is None:
                if platform == "youtube":
                    content_entry = _youtube_content_finding_entry(raw_item, detector_result)
                    if content_entry is not None:
                        manual_review.append(content_entry)
                continue
            creators_by_id[cache_key] = adapter.normalize_creator(creator)

        creator = creators_by_id.get(cache_key)
        if not creator:
            continue

        if platform == "youtube":
            integration = build_integration(competitor_id, creator, raw_item, None, detector_result, evidence_store)
        else:
            integration = build_social_integration(competitor_id, creator, raw_item, detector_result, evidence_store, platform)
        integration = adapter.normalize_integration(integration)

        if detector_result.category == "confirmed":
            confirmed.append(integration)
        else:
            organic.append(integration)

    coverage.confirmed_integrations = len(confirmed)
    coverage.items_checked = len(discovery.raw_items)
    coverage.organic_mentions = len(organic)
    coverage.potential_creators = sum(
        1 for item in manual_review
        if item.get("status") == "potential_creator" and item.get("creator") is not None
    )
    return coverage, list(creators_by_id.values()), confirmed, organic, manual_review, publishers


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
    publishers: list[Publisher] | None = None, potential_creator_ids: set[str] | None = None,
) -> tuple[dict, list[dict], list[dict], dict, dict]:
    our_profile = _load_our_profile()

    market_map = MarketMapBuilder(
        creators_for_market_map, competitors, integrations, default_settings, evidence_store,
        publishers=publishers,
    ).build()
    competitor_dna = [
        CompetitorDnaBuilder(creators_for_market_map, integrations, default_settings, evidence_store).build(c)
        for c in competitors
    ]

    candidate_buffer = max(_CANDIDATE_BUFFER_MIN, config.max_next_move_candidates * _CANDIDATE_BUFFER_MULTIPLIER)
    next_move_builder = NextMoveBuilder(
        creators_for_next_move, integrations, default_settings, evidence_store, top_n=candidate_buffer,
        potential_creator_ids=potential_creator_ids,
    )
    next_move_raw = next_move_builder.build_all(competitors)
    next_move = []
    for entry in next_move_raw:
        candidates = [
            c for c in entry.get("candidates", [])
            if c.get("similarity_score") is None or c["similarity_score"] >= config.min_strategy_match
        ]
        entry = {**entry, "candidates": candidates[: config.max_next_move_candidates]}
        next_move.append(entry)

    # Hotfix #3: White Space SUPPLY = независимый creator universe (+ brand creators,
    # чтобы их интеграции не "потерялись" при подсчёте saturation по сегментам) -
    # НЕ только креаторы из интеграций бренда/конкурентов.
    white_space_raw = WhiteSpaceBuilder(
        creators_for_white_space, competitors, integrations, our_profile, default_settings, evidence_store,
        potential_creator_ids=potential_creator_ids,
    ).build()
    filtered_segments = [
        s for s in white_space_raw.get("segments", []) if s["opportunity_score"] >= config.min_white_space_opportunity
    ][: config.max_white_space_segments]
    white_space = {**white_space_raw, "segments": filtered_segments}

    our_move = OurMoveBuilder(default_settings, our_profile).build(market_map, competitor_dna, next_move, white_space)

    return market_map, competitor_dna, next_move, white_space, our_move


def _finding_title(raw_text: str | None, platform: str, fallback: str) -> str:
    text = " ".join((raw_text or "").split())
    if platform == "youtube" and " || " in (raw_text or ""):
        text = (raw_text or "").split(" || ", 1)[0].strip()
    return (text[:160] if text else fallback)


def _finding_signals(integration: Integration) -> list[str]:
    signals: list[str] = []
    for ev in integration.evidence:
        field = ev.field or ""
        if field.startswith(("live_signal:", "social_signal:", "article_signal:")) and ev.value:
            signals.append(field.split(":", 1)[1])
    for value in (integration.detected_mechanic, integration.detected_offer, integration.detected_cta):
        if value:
            signals.append(str(value))
    return list(dict.fromkeys(signals))


def stage_build_findings(
    integrations: list[Integration], creators: list[Creator], publishers: list[Publisher],
    potential_signals: list[PotentialCreatorSignal], brand_name: str | None = None,
) -> list[dict]:
    """Build presentation rows from normalized real-data objects only."""
    creators_by_id = {creator.creator_id: creator for creator in creators}
    publishers_by_id = {publisher.publisher_id: publisher for publisher in publishers}
    findings: list[dict] = []

    for integration in integrations:
        creator = creators_by_id.get(integration.creator_id)
        publisher = publishers_by_id.get(integration.publisher_id or "")
        entity_name = (
            publisher.name if publisher else creator.name if creator else integration.publisher_id or integration.creator_id
        )
        entity_type = "publisher" if publisher or integration.platform == "articles" else "creator"
        classification = integration.article_category or integration.category
        if integration.platform == "articles":
            host = urlparse(integration.content_url or "").netloc.lower().removeprefix("www.")
            brand_slug = "".join(ch for ch in (brand_name or "").lower() if ch.isalnum())
            host_slug = "".join(ch for ch in host if ch.isalnum())
            if brand_slug and brand_slug in host_slug:
                entity_type = "brand_owned"
                classification = "brand_owned"
            elif integration.article_category == "affiliate":
                entity_type = "affiliate_publisher"
                classification = "affiliate_publisher"
            else:
                entity_type = "editorial_publisher"
                classification = "editorial_publisher"
        finding = {
            "finding_id": integration.integration_id,
            "entity_id": publisher.publisher_id if publisher else creator.creator_id if creator else integration.creator_id,
            "entity_name": entity_name,
            "entity_type": entity_type,
            "platform": integration.platform,
            "source_url": integration.content_url,
            "content_title": _finding_title(
                integration.raw_text, integration.platform, entity_name or "Материал",
            ),
            "content_preview": " ".join((integration.raw_text or "").split())[:320] or None,
            "topic": creator.topic_tags[0] if creator and creator.topic_tags else None,
            "format": integration.content_type or integration.detected_mechanic,
            "detected_signals": _finding_signals(integration),
            "classification": classification,
            "classification_group": integration.category,
            "published_at": integration.published_at.isoformat() if integration.published_at else None,
            "metrics": {
                "followers": creator.followers if creator else None,
                "median_views": creator.median_views if creator else None,
                "avg_views": creator.avg_views if creator else None,
                "engagement_rate": creator.engagement_rate if creator else None,
            },
            "evidence_ids": [ev.evidence_id for ev in integration.evidence],
            "source_mode": integration.source_mode.value,
            "source_platform": "youtube_web_search" if integration.ingestion_source == "youtube_web_search" else integration.platform,
        }
        findings.append(finding)

    for signal in potential_signals:
        creator = creators_by_id.get(signal.creator_id or "")
        source_host = urlparse(signal.source_url or "").netloc.removeprefix("www.")
        entity_name = signal.creator_name or (creator.name if creator else None) or source_host or "Страница"
        finding_id = stable_id(
            "potential", signal.platform, signal.source_url or signal.creator_id or signal.potential_reason,
        )
        findings.append({
            "finding_id": finding_id,
            "entity_id": signal.creator_id,
            "entity_name": entity_name,
            "entity_type": "creator" if signal.creator_id else "page",
            "platform": signal.platform,
            "source_url": signal.source_url,
            "content_title": signal.potential_reason,
            "content_preview": None,
            "topic": creator.topic_tags[0] if creator and creator.topic_tags else None,
            "format": "organic_affinity",
            "detected_signals": signal.brand_affinity_signals,
            "classification": "potential_creator",
            "classification_group": "potential_creator",
            "published_at": signal.observed_at.isoformat() if signal.observed_at else None,
            "metrics": {
                "followers": creator.followers if creator else None,
                "median_views": creator.median_views if creator else None,
                "avg_views": creator.avg_views if creator else None,
                "engagement_rate": creator.engagement_rate if creator else None,
            },
            "evidence_ids": [ev.evidence_id for ev in signal.evidence],
            "source_mode": "live",
        })

    findings.sort(key=lambda item: (item.get("published_at") or "", item["finding_id"]), reverse=True)
    return findings


# ---------------------------------------------------------------------------
# Главный оркестратор
# ---------------------------------------------------------------------------


def _process_brand(
    brand_input: str, platforms: list[str], config: AnalysisConfig, evidence_store: EvidenceStore,
    visual_enricher: VisualEvidenceEnricher | None = None, screenshot_cache: ScreenshotCache | None = None,
) -> tuple[ResolvedBrand, Competitor, list[PlatformCoverage], list[Creator], list[Integration], list[dict], list[Publisher]]:
    """Resolve + discover + extract для ОДНОГО бренда (основного или optional
    конкурента, hotfix #6). Возвращает (brand, competitor, coverages, creators,
    integrations_all_categories, manual_review_candidates, publishers)."""
    brand = stage_resolve_brand(brand_input)
    brand = stage_resolve_youtube_channel(brand, platforms)
    competitor = stage_build_competitor(brand)

    coverages: list[PlatformCoverage] = []
    creators: list[Creator] = []
    integrations: list[Integration] = []
    manual_review: list[dict] = []
    publishers: list[Publisher] = []

    for platform in platforms:
        if budget_exhausted(20):
            coverages.append(PlatformCoverage(platform=platform, source_mode="none", status="degraded", reason="Общий лимит анализа достигнут; источник не успел обработаться."))
            continue
        try:
            coverage, plat_creators, confirmed, organic, plat_manual_review, plat_publishers = stage_discover_and_extract(
                platform, brand, competitor.competitor_id, config, evidence_store, visual_enricher, screenshot_cache,
            )
        except Exception as exc:  # one source must never fail the whole analysis
            coverages.append(PlatformCoverage(
                platform=platform, source_mode="none", status="degraded",
                reason=f"Источник временно недоступен: {type(exc).__name__}",
            ))
            continue
        coverages.append(coverage)
        creators.extend(plat_creators)
        integrations.extend(confirmed)
        integrations.extend(organic)
        manual_review.extend(plat_manual_review)
        publishers.extend(plat_publishers)

    return brand, competitor, coverages, creators, integrations, manual_review, publishers


def _run_analysis_internal(
    request: AnalyzeRequest, analysis_id: str,
) -> tuple[AnalysisResult, list[str], dict[str, dict]]:
    config = request.settings
    start_budget(295)
    reset_search_budget(5)
    evidence_store = EvidenceStore()
    # Один shared VisualEvidenceEnricher/ScreenshotCache на весь analysis run
    # (раздел 3 требований: одинаковый screenshot/URL не должен обрабатываться
    # повторно). Оба failsafe при отсутствии OPENROUTER_API_KEY/Playwright -
    # см. app/enrichment/*.
    visual_enricher = VisualEvidenceEnricher()
    screenshot_cache = ScreenshotCache()

    platform_coverages: list[PlatformCoverage] = []
    all_creators: list[Creator] = []
    all_integrations: list[Integration] = []
    manual_review_total: list[dict] = []
    all_publishers: list[Publisher] = []
    seen_publisher_ids: set[str] = set()
    live_sources: list[str] = []
    imported_sources: list[str] = []
    degraded_sources: list[str] = []
    competitors: list[Competitor] = []

    def _merge_publishers(pubs: list[Publisher]) -> None:
        for p in pubs:
            if p.publisher_id not in seen_publisher_ids:
                seen_publisher_ids.add(p.publisher_id)
                all_publishers.append(p)

    # Stage 1-5: основной бренд
    brand, primary_competitor, primary_coverages, primary_creators, primary_integrations, primary_manual_review, primary_publishers = (
        _process_brand(request.brand, request.platforms, config, evidence_store, visual_enricher, screenshot_cache)
    )
    competitors.append(primary_competitor)
    platform_coverages.extend(primary_coverages)
    all_creators.extend(primary_creators)
    all_integrations.extend(primary_integrations)
    manual_review_total.extend(primary_manual_review)
    _merge_publishers(primary_publishers)

    # Hotfix #6: optional competitor_brands[] - single-brand mode работает честно,
    # если пусто (см. limitations ниже).
    for competitor_brand_input in request.competitor_brands:
        if budget_exhausted(45):
            degraded_sources.append("time_budget")
            break
        _, extra_competitor, extra_coverages, extra_creators, extra_integrations, extra_manual_review, extra_publishers = (
            _process_brand(competitor_brand_input, request.platforms, config, evidence_store, visual_enricher, screenshot_cache)
        )
        competitors.append(extra_competitor)
        platform_coverages.extend(extra_coverages)
        all_creators.extend(extra_creators)
        all_integrations.extend(extra_integrations)
        manual_review_total.extend(extra_manual_review)
        _merge_publishers(extra_publishers)

    for coverage in platform_coverages:
        if coverage.source_mode == "live" and coverage.platform not in live_sources:
            live_sources.append(coverage.platform)
        if coverage.status == "degraded" and coverage.platform not in degraded_sources:
            degraded_sources.append(coverage.platform)

    # Раздел 2 доработки: manual_review_total реально содержит ДВА разных вида
    # записей (см. _build_potential_creator_entry/status выше) - настоящие
    # "требует ручной проверки" (status=candidate_manual_review) и "potential
    # creator" (status=potential_creator, раздел 2/9/10/11: НЕ должны считаться
    # ручной проверкой и НЕ должны увеличивать integrations_found).
    pure_manual_review = [e for e in manual_review_total if e.get("status") not in {"potential_creator", "content_finding"}]
    content_findings = [e for e in manual_review_total if e.get("status") == "content_finding"]
    potential_creator_entries = [e for e in manual_review_total if e.get("status") == "potential_creator"]
    potential_creators: list[Creator] = [
        e["creator"] for e in potential_creator_entries if e.get("creator") is not None
    ]
    potential_creator_signals: list[PotentialCreatorSignal] = [
        e["signal"] for e in potential_creator_entries
        if e.get("signal") is not None and e.get("creator") is not None
        and e["signal"].platform in {"youtube", "instagram", "tiktok"}
    ]
    potential_creator_ids = {c.creator_id for c in potential_creators}

    # Stage 7 (+ date filter)
    filtered_creators, filtered_integrations = stage_apply_config_filters(all_creators, all_integrations, config)

    # Stage 8: DYNAMIC universe - seeds из include_topics ИЛИ observed topics бренда
    # (hotfix #1) - НЕ захардкоженная тема.
    observed_topics = _observed_topics(all_creators)
    if budget_exhausted(35):
        universe_creators, universe_status, universe_notes, universe_queries = [], "degraded", ["Общий лимит анализа достигнут до расширенного поиска авторов."], []
    else:
        universe_creators, universe_status, universe_notes, universe_queries = stage_build_universe_pool(
            request.platforms, config, observed_topics,
        )
    if universe_status == "degraded":
        degraded_sources.append("creator_universe")

    # Stage 9: next_move_candidates = creator_universe MINUS brand_used_creators;
    # white_space supply = universe (+ brand creators, чтобы их интеграции не
    # "терялись" при атрибуции по сегментам).
    filtered_creator_ids = {creator.creator_id for creator in filtered_creators}
    used_creator_ids = {i.creator_id for i in filtered_integrations if i.creator_id in filtered_creator_ids}
    universe_pool_placeholder = type("U", (), {"creators": universe_creators})()
    universe_minus_used = next_move_candidate_pool(universe_pool_placeholder, used_creator_ids)
    creators_for_next_move = filtered_creators + universe_minus_used

    seen_ids = {c.creator_id for c in filtered_creators}
    creators_for_white_space = list(filtered_creators) + [c for c in universe_creators if c.creator_id not in seen_ids]

    # Раздел 9 доработки: Creator Universe = A) confirmed creators (filtered_creators
    # уже внутри) + B) potential creators с organic affinity + C) независимо
    # найденные (universe_minus_used, уже внутри) - добавляем B туда, где его
    # раньше не хватало, без дублей.
    nm_ids = {c.creator_id for c in creators_for_next_move}
    creators_for_next_move = creators_for_next_move + [c for c in potential_creators if c.creator_id not in nm_ids]
    ws_ids = {c.creator_id for c in creators_for_white_space}
    creators_for_white_space = creators_for_white_space + [c for c in potential_creators if c.creator_id not in ws_ids]

    # Stage 10 - существующие 5 слоёв, без изменений (+ publishers - новая,
    # опциональная секция Market Map, раздел 9; potential_creator_ids - раздел 9/10,
    # чтобы Next Move мог отдельно пометить кандидатов с organic affinity).
    market_map, competitor_dna, next_move, white_space, our_move = stage_run_analytical_layers(
        creators_for_next_move, creators_for_white_space, filtered_creators, competitors,
        filtered_integrations, config, evidence_store, publishers=all_publishers,
        potential_creator_ids=potential_creator_ids,
    )
    findings = stage_build_findings(
        filtered_integrations,
        all_creators + potential_creators,
        all_publishers,
        potential_creator_signals,
        brand_name=brand.canonical_name,
    )
    for item in content_findings:
        findings.append({
            "finding_id": stable_id("content_finding", item.get("source_url") or item.get("title")),
            "entity_id": None, "entity_name": "YouTube видео", "entity_type": "content",
            "platform": "youtube", "source_url": item.get("source_url"),
            "content_title": item.get("title"), "content_preview": item.get("preview"),
            "topic": None, "format": "video", "detected_signals": item.get("signals") or [],
            "classification": item.get("classification") or "organic_mention",
            "classification_group": item.get("classification") or "organic_mention",
            "published_at": None,
            "metrics": {"followers": None, "median_views": None, "avg_views": None, "engagement_rate": None},
            "evidence_ids": [], "source_mode": "live", "source_platform": "youtube_web_search",
        })
    findings.sort(key=lambda item: (item.get("published_at") or "", item["finding_id"]), reverse=True)

    # Stage 11 - честная coverage/summary/limitations (раздел 19: "не скрывать
    # ошибки источников" - каждый нестандартный статус явно объясняется).
    limitations: list[str] = []
    for coverage in platform_coverages:
        if coverage.status == "unavailable":
            limitations.append(
                f"{coverage.platform}: live-данные недоступны ({coverage.reason}). "
                f"Доступен импорт вручную собранных данных (CSV/JSON)."
            )
        elif coverage.status == "connector_offline":
            limitations.append(
                f"{coverage.platform}: local connector offline ({coverage.reason}). "
                f"Запустите local_connector/run.py на своём Mac (см. LOCAL_CONNECTOR.md) "
                f"или используйте импорт вручную собранных данных (CSV/JSON)."
            )
        elif coverage.status == "manual_intervention_required":
            limitations.append(
                f"{coverage.platform}: требуется ручной вход/подтверждение CAPTCHA на Mac "
                f"({coverage.reason}). Проект осознанно не обходит anti-bot защиту."
            )
        elif coverage.status == "degraded":
            limitations.append(f"{coverage.platform}: данные собраны частично ({coverage.reason})")
    if universe_status != "ok":
        limitations.extend(universe_notes)
    if pure_manual_review:
        limitations.append(
            f"{len(pure_manual_review)} найденных материалов требуют ручной проверки "
            f"(brand+commercial evidence есть, но confidence ниже порога) - не включены в результат."
        )
    if potential_creator_signals:
        limitations.append(
            f"{len(potential_creator_signals)} авторов показывают органический интерес к бренду "
            f"без подтверждённого коммерческого сигнала — они учтены как потенциальные авторы, "
            f"а не как подтверждённые интеграции."
        )
    if not request.competitor_brands:
        limitations.append("Конкурентная насыщенность пока рассчитана только по данным анализируемого бренда.")

    coverage_obj = AnalysisCoverage(
        sources=list(dict.fromkeys(c.platform for c in platform_coverages)),
        live_sources=live_sources,
        imported_sources=imported_sources,
        degraded_sources=degraded_sources,
        platforms=platform_coverages,
    )
    # Раздел 10 доработки: "Подтверждённые интеграции" и "Авторы с органическим
    # brand affinity" - два отдельных, НИКОГДА не смешиваемых числа.
    # confirmed_integrations/potential_creators_count - НОВЫЕ, additive поля
    # (integrations_found не меняет смысл - обратная совместимость с текущим UI).
    summary = AnalysisSummary(
        integrations_found=len(filtered_integrations),
        creators_used=len(used_creator_ids),
        creator_universe_size=len(universe_creators),
        confirmed_integrations=sum(1 for i in filtered_integrations if i.category == "confirmed"),
        potential_creators_count=len(potential_creator_signals),
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
        potential_creators=potential_creator_signals,
        findings=findings,
    )
    reset_search_budget(None)
    clear_budget()
    return result, universe_queries, evidence_store.as_dict()


def run_analysis(request: AnalyzeRequest, analysis_id: str) -> AnalysisResult:
    result, _queries, _evidence = _run_analysis_internal(request, analysis_id)
    return result


def run_analysis_with_debug(request: AnalyzeRequest, analysis_id: str) -> tuple[AnalysisResult, list[str]]:
    """Как run_analysis(), но дополнительно возвращает queries, реально
    использованные Creator Universe discovery (hotfix #1 verification), без
    изменения публичной схемы AnalysisResult."""
    result, queries, _evidence = _run_analysis_internal(request, analysis_id)
    return result, queries


def run_analysis_with_evidence(
    request: AnalyzeRequest, analysis_id: str,
) -> tuple[AnalysisResult, dict[str, dict]]:
    """Запускает тот же real-analysis pipeline и возвращает полный evidence map.

    Публичная схема ``AnalysisResult`` остаётся неизменной: evidence сохраняется
    analysis-scoped store-ом и разрешается отдельным API endpoint.
    """
    result, _queries, evidence = _run_analysis_internal(request, analysis_id)
    return result, evidence
