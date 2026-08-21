"""
LIVE YouTube ingestion: превращает публичные видео в реальные Integration/Creator.

Пайплайн (см. требования live ingestion):
    competitor (name/aliases) -> query builder -> YouTube discovery ->
    integration detector (deterministic score) -> creator extraction ->
    Integration creation -> dedup (по video_id).

Ничего в этом модуле не пишет "конкурент точно сделает X" - это чисто
детерминированный, объяснимый rule-based детектор (никакого LLM), поэтому
все evidence здесь - FACT (конкретный сигнал найден в конкретном тексте)
или COMPUTED (агрегированный confidence).
"""
from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.detection import categorize_signals
from app.evidence import EvidenceStore, computed, fact
from app.health import health_registry
from app.ingestion.youtube_adapter import YouTubeAdapter, _parse_dt
from app.models import Creator, Integration, SourceMode
from config.settings import Settings, settings as default_settings
from app.runtime_budget import budget_exhausted

# ---------------------------------------------------------------------------
# A. Competitor query builder
# ---------------------------------------------------------------------------

_search_budget: ContextVar[int | None] = ContextVar("youtube_search_budget", default=None)


def reset_search_budget(limit: int | None = 5) -> None:
    _search_budget.set(None if limit is None else max(0, limit))


def _take_search_slot() -> bool:
    remaining = _search_budget.get()
    if remaining is None:
        return True
    if remaining <= 0:
        return False
    _search_budget.set(remaining - 1)
    return True


DEFAULT_BRAND_KEYWORDS = [
    "интеграция", "реклама", "промокод", "спонсор", "обзор",
    "review", "sponsored", "промо код", "партнёрский материал",
]


class CompetitorQueryBuilder:
    """Строит варианты поисковых запросов из canonical name + aliases + brand keywords."""

    def __init__(self, competitor_name: str, aliases: list[str] | None = None,
                 brand_keywords: list[str] | None = None) -> None:
        self.competitor_name = competitor_name.strip()
        self.aliases = [a.strip() for a in (aliases or []) if a.strip()]
        self.brand_keywords = brand_keywords if brand_keywords is not None else DEFAULT_BRAND_KEYWORDS

    def build_queries(self) -> list[str]:
        names = [self.competitor_name] + [a for a in self.aliases if a.lower() != self.competitor_name.lower()]
        queries: list[str] = []
        for name in names:
            if not name:
                continue
            queries.append(name)  # голое имя бренда
            for kw in self.brand_keywords:
                queries.append(f"{name} {kw}")

        seen: set[str] = set()
        unique: list[str] = []
        for q in queries:
            key = q.lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(q)
        return unique

    def brand_terms(self) -> list[str]:
        """Все имена/алиасы, по которым детектор ищет буквальное упоминание бренда."""
        return [self.competitor_name] + self.aliases


# ---------------------------------------------------------------------------
# B. YouTube discovery
# ---------------------------------------------------------------------------

@dataclass
class DiscoveryResult:
    videos: list[dict] = field(default_factory=list)   # raw video search items, deduped по videoId
    queries_run: list[str] = field(default_factory=list)
    queries_failed: list[str] = field(default_factory=list)
    quota_exceeded: bool = False


def discover_videos(adapter: YouTubeAdapter, queries: list[str], max_results_per_query: int = 15) -> DiscoveryResult:
    """Ищет видео по всем запросам, дедуплицирует по videoId.

    Сам факт попадания в поисковую выдачу НЕ считается интеграцией - это
    только сырой кандидат-пул для детектора (раздел B требований).
    """
    result = DiscoveryResult()
    if not adapter.is_available():
        health_registry.unavailable(adapter.source_name, "YOUTUBE_API_KEY не задан - live discovery недоступен")
        return result

    seen_video_ids: set[str] = set()
    for query in queries:
        if budget_exhausted(35):
            result.queries_failed.append("time_budget")
            break
        if result.quota_exceeded:
            break
        if not _take_search_slot():
            result.queries_failed.append(query)
            break
        try:
            items = adapter.search_videos(query, max_results_per_query)
            result.queries_run.append(query)
            for item in items:
                video_id = (item.get("id") or {}).get("videoId")
                if not video_id or video_id in seen_video_ids:
                    continue
                seen_video_ids.add(video_id)
                result.videos.append(item)
        except httpx.HTTPStatusError as exc:
            result.queries_failed.append(query)
            if exc.response.status_code in (403, 429):
                result.quota_exceeded = True
                health_registry.degraded(
                    adapter.source_name,
                    f"YouTube API квота/лимит превышен (HTTP {exc.response.status_code}) - "
                    f"остановлено после {len(result.queries_run)} успешных запросов из {len(queries)}",
                )
            else:
                health_registry.degraded(adapter.source_name, f"HTTP {exc.response.status_code} на запрос '{query}'")
        except Exception as exc:  # noqa: BLE001 - discovery не должен ронять live-run
            result.queries_failed.append(query)
            health_registry.degraded(adapter.source_name, f"ошибка запроса '{query}': {exc}")

    if not result.quota_exceeded:
        status = "ok" if result.videos or not result.queries_failed else "degraded"
        detail = f"{len(result.videos)} уникальных видео по {len(result.queries_run)}/{len(queries)} запросам"
        if status == "ok":
            health_registry.ok(adapter.source_name, detail)
        else:
            health_registry.degraded(adapter.source_name, detail)
    return result


# ---------------------------------------------------------------------------
# C. Integration detector - deterministic evidence score
# ---------------------------------------------------------------------------

CTA_PHRASES = [
    "по ссылке в описании", "переходи по ссылке", "ссылка в описании", "скидка по коду",
    "используй промокод", "успей купить", "перейти по ссылке", "промокод в описании",
    "link in description", "sign up now", "use code", "swipe up",
]

SPONSOR_WORDING = [
    "на правах рекламы", "партнёрский материал", "партнерский материал", "#реклама",
    "спонсор этого видео", "спонсор видео", "sponsored by", "in partnership with",
    "promoted by", "paid partnership", "на коммерческой основе",
]

PROMO_CODE_PATTERN = re.compile(
    r"(промо[\s-]?код|промокод|discount\s*code|promo\s*code)\s*[:\-]?\s*([A-ZА-Я0-9_]{3,20})",
    re.IGNORECASE,
)
BRAND_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def _find_ci(haystack: str | None, needle: str) -> bool:
    if not haystack or not needle:
        return False
    return needle.lower() in haystack.lower()


def _count_ci(haystack: str | None, needle: str) -> int:
    if not haystack or not needle:
        return 0
    return haystack.lower().count(needle.lower())


@dataclass
class DetectorResult:
    is_integration: bool
    confidence: float
    reasons: list[str]
    signals: dict[str, dict]  # signal_name -> {"matched": bool, "weight": float, "raw_fragment": str|None}
    # confirmed | manual_review | organic_mention | rejected (раздел 10 требований)
    category: str = "rejected"
    has_brand_evidence: bool = False
    has_commercial_evidence: bool = False


class IntegrationDetector:
    """Деterministic rule-based детектор - без LLM, без "может быть"."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or default_settings

    def detect(self, title: str, description: str, brand_terms: list[str]) -> DetectorResult:
        weights = self.settings.live_detector_weights
        text_all = f"{title or ''} {description or ''}"
        signals: dict[str, dict] = {}

        canonical = brand_terms[0] if brand_terms else ""
        aliases = brand_terms[1:] if len(brand_terms) > 1 else []

        brand_in_title = any(_find_ci(title, term) for term in brand_terms)
        signals["brand_in_title"] = {
            "matched": brand_in_title, "weight": weights.get("brand_in_title", 0.0),
            "raw_fragment": title if brand_in_title else None,
        }

        brand_in_description = any(_find_ci(description, term) for term in brand_terms)
        signals["brand_in_description"] = {
            "matched": brand_in_description, "weight": weights.get("brand_in_description", 0.0),
            "raw_fragment": (description or "")[:300] if brand_in_description else None,
        }

        alias_hit = any(_find_ci(text_all, a) for a in aliases) and not _find_ci(text_all, canonical)
        signals["alias_match"] = {
            "matched": alias_hit, "weight": weights.get("alias_match", 0.0),
            "raw_fragment": next((a for a in aliases if _find_ci(text_all, a)), None) if alias_hit else None,
        }

        promo_match = PROMO_CODE_PATTERN.search(text_all)
        signals["promo_code"] = {
            "matched": bool(promo_match), "weight": weights.get("promo_code", 0.0),
            "raw_fragment": promo_match.group(0) if promo_match else None,
        }

        brand_slug = re.sub(r"\s+", "", canonical.lower()) if canonical else ""
        brand_url_match = None
        if brand_slug:
            for url in BRAND_URL_PATTERN.findall(description or ""):
                if brand_slug in url.lower():
                    brand_url_match = url
                    break
        signals["brand_url"] = {
            "matched": bool(brand_url_match), "weight": weights.get("brand_url", 0.0),
            "raw_fragment": brand_url_match,
        }

        cta_hit = next((p for p in CTA_PHRASES if _find_ci(text_all, p)), None)
        signals["cta_phrase"] = {
            "matched": bool(cta_hit), "weight": weights.get("cta_phrase", 0.0),
            "raw_fragment": cta_hit,
        }

        sponsor_hit = next((p for p in SPONSOR_WORDING if _find_ci(text_all, p)), None)
        signals["sponsor_wording"] = {
            "matched": bool(sponsor_hit), "weight": weights.get("sponsor_wording", 0.0),
            "raw_fragment": sponsor_hit,
        }

        mention_count = sum(_count_ci(text_all, term) for term in brand_terms)
        repeated = mention_count >= 2
        signals["repeated_mention"] = {
            "matched": repeated, "weight": weights.get("repeated_mention", 0.0),
            "raw_fragment": f"{mention_count} упоминаний" if repeated else None,
        }

        confidence = round(min(1.0, sum(s["weight"] for s in signals.values() if s["matched"])), 3)
        reasons = [name for name, s in signals.items() if s["matched"]]
        threshold = self.settings.live_integration_confidence_threshold

        category, has_brand_evidence, has_commercial_evidence = categorize_signals(signals, confidence, threshold)

        return DetectorResult(
            is_integration=category == "confirmed",
            confidence=confidence,
            reasons=reasons,
            signals=signals,
            category=category,
            has_brand_evidence=has_brand_evidence,
            has_commercial_evidence=has_commercial_evidence,
        )


# ---------------------------------------------------------------------------
# D + E. Creator extraction + Integration creation
# ---------------------------------------------------------------------------

def build_creator_from_channel(channel_item: dict, video_stats: dict | None = None) -> Creator:
    creator = YouTubeAdapter.channel_to_creator(channel_item)
    if video_stats:
        stats = video_stats.get("statistics", {}) or {}
        view_count = stats.get("viewCount")
        if view_count is not None:
            try:
                creator.avg_views = float(view_count)
            except (TypeError, ValueError):
                pass
    creator.source_mode = SourceMode.LIVE
    return creator


def build_integration(competitor_id: str, creator: Creator, video_item: dict, video_stats: dict | None,
                       detector_result: DetectorResult, evidence_store: EvidenceStore) -> Integration:
    snippet = (video_item.get("snippet") or {}) if "snippet" in video_item else (video_stats or {}).get("snippet", {})
    video_id = (video_item.get("id") or {}).get("videoId") or (video_stats or {}).get("id")
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    published_at = _parse_dt(snippet.get("publishedAt"))
    content_url = video_item.get("_web_source_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)

    evidence_ids = []
    for signal_name, sig in detector_result.signals.items():
        if not sig["matched"]:
            continue
        ev = fact(
            field=f"live_signal:{signal_name}",
            value=True,
            source_url=content_url,
            observed_at=published_at,
            raw_fragment=sig.get("raw_fragment"),
        )
        evidence_ids.append(evidence_store.add(ev))

    conf_ev = evidence_store.add(computed(
        field="live_integration_confidence",
        value=detector_result.confidence,
        supporting_note=f"reasons={detector_result.reasons}",
    ))
    evidence_ids.append(conf_ev)

    detected_offer = "discount_code" if detector_result.signals["promo_code"]["matched"] else None
    detected_cta = detector_result.signals["cta_phrase"]["raw_fragment"]
    detected_mechanic = (
        "dedicated_video" if detector_result.signals["brand_in_title"]["matched"]
        else ("mention" if detector_result.signals["brand_in_description"]["matched"] else None)
    )

    integration_id = f"yt_live_{video_id}" if video_id else f"yt_live_unknown_{id(video_item)}"

    return Integration(
        integration_id=integration_id,
        competitor_id=competitor_id,
        creator_id=creator.creator_id,
        platform="youtube",
        content_url=content_url,
        published_at=published_at,
        content_type="video",
        detected_offer=detected_offer,
        detected_cta=detected_cta,
        detected_mechanic=detected_mechanic,
        raw_text=f"{title} || {description}"[:2000],
        evidence=[evidence_store.resolve(eid) for eid in evidence_ids if evidence_store.resolve(eid)],
        is_synthetic=False,
        source_mode=SourceMode.LIVE,
        confidence=detector_result.confidence,
        ingestion_source="youtube_web_search" if video_item.get("_web_source_url") else "youtube_api_v3",
        category=detector_result.category,
    )


# ---------------------------------------------------------------------------
# Оркестратор: F (dedup через deterministic id + upsert), G (не падать при ошибках)
# ---------------------------------------------------------------------------

@dataclass
class LiveIngestionReport:
    competitor_name: str
    queries: list[str] = field(default_factory=list)
    videos_found: int = 0
    videos_filtered_out: int = 0
    confirmed_integrations: list[Integration] = field(default_factory=list)
    manual_review_candidates: list[dict] = field(default_factory=list)
    organic_mentions: list[Integration] = field(default_factory=list)
    rejected_count: int = 0
    creators: list[Creator] = field(default_factory=list)
    quota_exceeded: bool = False
    notes: list[str] = field(default_factory=list)


def run_youtube_ingestion(competitor_id: str, competitor_name: str, aliases: list[str] | None = None,
                           brand_keywords: list[str] | None = None, adapter: YouTubeAdapter | None = None,
                           settings: Settings | None = None,
                           evidence_store: EvidenceStore | None = None) -> LiveIngestionReport:
    settings = settings or default_settings
    adapter = adapter or YouTubeAdapter()
    evidence_store = evidence_store or EvidenceStore()
    detector = IntegrationDetector(settings)

    query_builder = CompetitorQueryBuilder(competitor_name, aliases, brand_keywords)
    queries = query_builder.build_queries()
    brand_terms = query_builder.brand_terms()

    report = LiveIngestionReport(competitor_name=competitor_name, queries=queries)

    if not adapter.is_available():
        report.notes.append("YOUTUBE_API_KEY не задан - live ingestion недоступен, пропущено")
        return report

    discovery = discover_videos(adapter, queries, settings.live_max_results_per_query)
    report.videos_found = len(discovery.videos)
    report.quota_exceeded = discovery.quota_exceeded
    if discovery.queries_failed:
        report.notes.append(f"неудачные запросы: {discovery.queries_failed}")

    seen_creator_ids: dict[str, Creator] = {}

    for video_item in discovery.videos:
        snippet = video_item.get("snippet", {}) or {}
        title = snippet.get("title", "")
        description = snippet.get("description", "")
        channel_id = snippet.get("channelId")
        video_id = (video_item.get("id") or {}).get("videoId")

        detector_result = detector.detect(title, description, brand_terms)

        if detector_result.category == "rejected":
            # Ни одного brand evidence - это вообще не про наш бренд, даже не кандидат.
            report.rejected_count += 1
            continue

        if detector_result.category == "manual_review":
            report.videos_filtered_out += 1
            report.manual_review_candidates.append({
                "video_id": video_id,
                "title": title,
                "content_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else None,
                "confidence": detector_result.confidence,
                "reasons": detector_result.reasons,
                "status": "candidate_manual_review",
                "category": "manual_review",
            })
            continue

        # confirmed или organic_mention - в обоих случаях у нас есть brand evidence
        # и стоит извлечь реального creator, просто разные категории интеграции.
        try:
            video_stats = adapter._run_with_retries(adapter.get_video_stats, video_id) if video_id else None
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"video_stats error for {video_id}: {exc}")
            video_stats = None

        if channel_id and channel_id not in seen_creator_ids:
            try:
                channel_item = adapter._run_with_retries(adapter.get_channel_stats, channel_id)
            except Exception as exc:  # noqa: BLE001
                report.notes.append(f"channel_stats error for {channel_id}: {exc}")
                channel_item = None
            if channel_item:
                seen_creator_ids[channel_id] = build_creator_from_channel(channel_item, video_stats)
            else:
                # Не удалось получить канал - создаём минимального Creator по snippet,
                # чтобы не терять найденную интеграцию (никакого silent drop).
                seen_creator_ids[channel_id] = Creator(
                    creator_id=f"yt_{channel_id}",
                    name=snippet.get("channelTitle", "Unknown channel"),
                    canonical_url=f"https://www.youtube.com/channel/{channel_id}",
                    platform="youtube",
                    source_refs=[f"https://www.youtube.com/channel/{channel_id}"],
                    is_synthetic=False,
                    source_mode=SourceMode.LIVE,
                    last_seen_at=datetime.now(timezone.utc),
                )
        creator = seen_creator_ids.get(channel_id)
        if not creator:
            report.notes.append(f"video {video_id} пропущен - нет channel_id")
            continue

        integration = build_integration(competitor_id, creator, video_item, video_stats, detector_result, evidence_store)
        if detector_result.category == "confirmed":
            report.confirmed_integrations.append(integration)
        else:
            report.organic_mentions.append(integration)

    report.creators = list(seen_creator_ids.values())
    return report
