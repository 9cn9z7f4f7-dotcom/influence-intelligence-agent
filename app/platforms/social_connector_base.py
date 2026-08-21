"""
Общая база для Instagram/TikTok platform adapters через local connector
(раздел 10-19 требований). Раньше обе платформы были ЖЁСТКО hardcoded
unavailable (never obtaining real data); теперь они реально маршрутизируют
через local_connector/run.py, если он зарегистрирован и жив, и честно
остаются connector_offline/manual_intervention_required иначе - НИКОГДА не
имитируют live-данные без реального connector (раздел 3, 19).

/api/analyze остаётся синхронным MVP-эндпоинтом (раздел 33 запрещает
добавлять отдельный scheduler/queue) - поэтому discover_brand_content()
делает короткий bounded poll результата (см. ConnectorRegistry.wait_for_result).
Если connector не успел ответить за это окно - честно возвращается status=
"degraded" с job_id (данные появятся в следующем прогоне), а НЕ подмешиваются
synthetic результаты.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.brand_domain import BrandDomainProfile, build_brand_domain_profile_from_terms
from app.connectors.registry import ConnectorRegistry, registry as default_connector_registry
from app.detection import escalate_with_affinity, escalate_with_hard_signals
from app.evidence import EvidenceStore, computed, fact
from app.hard_signals import detect_hard_commercial_signals
from app.ingestion.identifiers import stable_id
from app.ingestion.live_youtube import DetectorResult
from app.links_extractor import classify_links, extract_links
from app.models import Creator, Integration, SourceMode
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult
from app.potential_creator import detect_brand_affinity_signals
from config.settings import settings as default_settings
from app.runtime_budget import remaining_seconds
from app.topic_classifier import classify_topic


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = raw.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (ValueError, AttributeError):
        return None


def build_social_integration(
    competitor_id: str, creator: Creator, raw_item: dict, detector_result: DetectorResult,
    evidence_store: EvidenceStore, platform: str,
) -> Integration:
    """Раздел 15-16 требований: normalize social result -> Integration.

    Только реально присланные local connector-ом поля (raw_item) - недоступные
    поля уже null на уровне ConnectorResultItem, здесь ничего не додумывается."""
    post_url = raw_item.get("post_url") or raw_item.get("profile_url")
    published_at = _parse_dt(raw_item.get("published_at"))

    evidence_ids: list[str] = []
    for name, sig in detector_result.signals.items():
        if not sig.get("matched"):
            continue
        ev = fact(field=f"social_signal:{name}", value=True, source_url=post_url, observed_at=published_at,
                   raw_fragment=sig.get("raw_fragment"))
        evidence_ids.append(evidence_store.add(ev))
    conf_ev = evidence_store.add(computed(
        field=f"{platform}_integration_confidence", value=detector_result.confidence,
        supporting_note=f"reasons={detector_result.reasons}",
    ))
    evidence_ids.append(conf_ev)

    detected_mechanic = (
        "paid_partnership" if raw_item.get("paid_partnership_label")
        else ("collaboration" if raw_item.get("collaboration_label") else "mention")
    )
    integration_id = stable_id(f"{platform}_live", post_url or creator.creator_id)

    return Integration(
        integration_id=integration_id, competitor_id=competitor_id, creator_id=creator.creator_id,
        platform=platform, content_url=post_url, published_at=published_at, content_type="post",
        detected_offer=None, detected_cta=None, detected_mechanic=detected_mechanic,
        campaign_tags=list(raw_item.get("hashtags") or [])[:10],
        raw_text=(raw_item.get("caption") or "")[:2000],
        evidence=[evidence_store.resolve(eid) for eid in evidence_ids if evidence_store.resolve(eid)],
        is_synthetic=False, source_mode=SourceMode.LIVE, confidence=detector_result.confidence,
        ingestion_source=f"{platform}_local_connector", category=detector_result.category,
    )

DEFAULT_CONNECTOR_WAIT_SECONDS = 120.0
IMPORT_HINT_TEMPLATE = "manage.py import-integrations --file <csv|json> (platform={platform})"


class SocialConnectorPlatformAdapter(PlatformAdapter):
    platform_name: str = ""

    def __init__(self, connector_registry: ConnectorRegistry | None = None,
                 wait_seconds: float = DEFAULT_CONNECTOR_WAIT_SECONDS, settings=None) -> None:
        self.registry = connector_registry or default_connector_registry
        self.settings = settings or default_settings
        self.wait_seconds = (
            wait_seconds if wait_seconds != DEFAULT_CONNECTOR_WAIT_SECONDS
            else self.settings.connector_job_wait_seconds
        )
        self._domain_profile_cache: dict[tuple, BrandDomainProfile] = {}

    def _domain_profile(self, brand_terms: list[str]) -> BrandDomainProfile:
        key = tuple(brand_terms)
        if key not in self._domain_profile_cache:
            self._domain_profile_cache[key] = build_brand_domain_profile_from_terms(brand_terms)
        return self._domain_profile_cache[key]

    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        status, detail = self.registry.platform_status(self.platform_name)
        import_hint = IMPORT_HINT_TEMPLATE.format(platform=self.platform_name)

        if status == "connector_offline":
            return PlatformDiscoveryResult(
                platform=self.platform_name, status="connector_offline", source_mode="none",
                reason=detail or f"{self.platform_name} local connector offline", import_hint=import_hint,
            )
        if status == "manual_intervention_required":
            return PlatformDiscoveryResult(
                platform=self.platform_name, status="manual_intervention_required", source_mode="none",
                reason=detail or "CAPTCHA/challenge - требуется ручной вход пользователя на Mac",
                import_hint=import_hint,
            )

        # status == "online" - реальный connector зарегистрирован и жив -> enqueue job
        # (job_id уникален; analysis_id здесь - контекстный id, а не обязательно тот же
        # AnalysisResult.analysis_id верхнего уровня - интерфейс PlatformAdapter не
        # прокидывает его, а менять сигнатуру абстрактного метода ради этого не стоит).
        job_context_id = stable_id("job_ctx", brand.canonical_name, self.platform_name)
        brand_source_url = brand.source_url
        brand_handle = brand.normalized_handle
        if self.platform_name == "instagram" and config.instagram_brand_url:
            brand_source_url = config.instagram_brand_url.strip()
            try:
                from urllib.parse import urlparse
                path = urlparse(brand_source_url).path.strip("/")
                if path:
                    brand_handle = path.split("/")[0].lstrip("@")
            except Exception:
                pass

        job = self.registry.enqueue_job(
            analysis_id=job_context_id, platform=self.platform_name, brand=brand.canonical_name,
            aliases=brand.aliases, settings={
                "search_level": config.search_level,
                "date_range": config.date_range,
                "custom_start": config.custom_start.isoformat() if config.custom_start else None,
                "custom_end": config.custom_end.isoformat() if config.custom_end else None,
                "min_followers": config.min_followers,
                "max_followers": config.max_followers,
                "min_avg_views": config.min_avg_views,
                "include_topics": list(config.include_topics),
                "exclude_topics": list(config.exclude_topics),
                "brand_source_url": brand_source_url,
                "brand_handle": brand_handle,
            },
        )
        remaining = remaining_seconds()
        wait_seconds = self.wait_seconds if remaining is None else max(1.0, min(self.wait_seconds, max(1.0, remaining - 30)))
        submission = self.registry.wait_for_result(job.job_id, timeout_seconds=wait_seconds)

        if submission is None:
            return PlatformDiscoveryResult(
                platform=self.platform_name, status="degraded", source_mode="live",
                reason=(f"Local connector online, job {job.job_id} создан, но не вернул результат "
                        f"в течение {wait_seconds:.0f}s этого запроса - повторите анализ, "
                        f"когда connector завершит job."),
                import_hint=import_hint,
            )
        if submission.status == "manual_intervention_required":
            return PlatformDiscoveryResult(
                platform=self.platform_name, status="manual_intervention_required", source_mode="none",
                reason=submission.detail or "CAPTCHA/challenge во время job - требуется ручной вход",
                import_hint=import_hint,
            )
        if submission.status == "error":
            return PlatformDiscoveryResult(
                platform=self.platform_name, status="degraded", source_mode="live",
                reason=submission.detail or "local connector вернул ошибку", import_hint=import_hint,
            )

        raw_items = [item.model_dump() for item in submission.items]
        return PlatformDiscoveryResult(platform=self.platform_name, status="ok", source_mode="live", raw_items=raw_items)

    def detect_integration(self, raw_item: dict, brand_terms: list[str]) -> DetectorResult:
        """DOM evidence (раздел 17): brand_mention/paid_partnership_label/
        collaboration_label - именно то, что local connector реально увидел в
        DOM (не придумано). Категории общие с остальным pipeline (confirmed/
        manual_review/organic_mention/rejected)."""
        caption = raw_item.get("caption") or ""
        brand_hit = next((t for t in brand_terms if t and t.lower() in caption.lower()), None)
        explicit_brand_mention = bool(raw_item.get("brand_mention"))
        discovery_context = (raw_item.get("discovery_context") or "").strip().lower()
        # A result returned by the authenticated Instagram brand/search flow is
        # a real relevant observation even when Instagram hides caption text from
        # the current DOM selectors. Context NEVER confirms sponsorship by itself.
        contextual_match = discovery_context in {"brand_post", "tagged_brand", "search"}
        signals = {
            "brand_mention": {"matched": bool(brand_hit) or explicit_brand_mention, "raw_fragment": brand_hit},
            "platform_search_match": {"matched": contextual_match, "raw_fragment": discovery_context or None},
            "paid_partnership_label": {"matched": bool(raw_item.get("paid_partnership_label")), "raw_fragment": None},
            "collaboration_label": {"matched": bool(raw_item.get("collaboration_label")), "raw_fragment": None},
        }
        has_brand_evidence = signals["brand_mention"]["matched"] or contextual_match
        has_commercial_evidence = (
            signals["paid_partnership_label"]["matched"] or signals["collaboration_label"]["matched"]
        )

        if not has_brand_evidence:
            category = "rejected"
        elif has_commercial_evidence:
            category = "confirmed"
        else:
            category = "organic_mention"

        confidence = 0.9 if has_commercial_evidence else (0.4 if signals["brand_mention"]["matched"] else (0.25 if contextual_match else 0.0))
        reasons = [k for k, v in signals.items() if v["matched"]]

        # Раздел 1/2 доработки: те же ДОПОЛНИТЕЛЬНЫЕ слои, что и для YouTube/Articles
        # (app/platforms/youtube.py, app/platforms/articles.py) - hard commercial
        # signal (промокод/affiliate-ссылка/CTA+brand URL/bio-ссылка на бренд и
        # т.п., включая ссылки в caption и bio/profile_url) поднимает до
        # "confirmed" независимо от confidence; без hard signal, но с organic
        # affinity ("ношу", "рекомендую" и т.п.) - до "potential_creator".
        profile = self._domain_profile(brand_terms)
        # ``profile_url`` is the creator's Instagram/TikTok profile, not a link
        # in bio.  Treat only links actually observed in the content/connector
        # payload as commercial link evidence.
        observed_links = list(extract_links(caption)) + list(raw_item.get("links") or [])
        content_links = classify_links(observed_links, profile)
        hard = detect_hard_commercial_signals(
            caption, brand_name=brand_terms[0] if brand_terms else "",
            brand_aliases=brand_terms[1:] if len(brand_terms) > 1 else [],
            links=content_links, bio_links=[],
        )
        new_category = escalate_with_hard_signals(category, has_brand_evidence, hard.matched)

        affinity_signals: list[str] = []
        if new_category == category:
            affinity_signals = detect_brand_affinity_signals(caption, brand_terms)
            new_category = escalate_with_affinity(new_category, has_brand_evidence, affinity_signals)

        if new_category != category:
            for name, sig in hard.signals.items():
                if sig.get("matched"):
                    signals[f"hard:{name}"] = sig
            for phrase in affinity_signals:
                signals[f"affinity:{phrase}"] = {"matched": True, "raw_fragment": phrase}
            reasons = list(dict.fromkeys(reasons + hard.reasons + [f"affinity:{p}" for p in affinity_signals]))
            category = new_category
            has_commercial_evidence = has_commercial_evidence or hard.matched
            confidence = max(confidence, 0.9) if category == "confirmed" else confidence

        return DetectorResult(
            is_integration=category == "confirmed", confidence=confidence, reasons=reasons, signals=signals,
            category=category, has_brand_evidence=has_brand_evidence, has_commercial_evidence=has_commercial_evidence,
        )

    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        username = raw_item.get("username")
        if not username:
            return None
        creator_id = stable_id(self.platform_name, username)
        caption = raw_item.get("caption") or ""
        topic = classify_topic(caption, use_llm_for_ambiguous=False) if caption else None
        topic_tags = list(topic.topic_tags) if topic is not None else []
        return Creator(
            creator_id=creator_id, name=username, canonical_url=raw_item.get("profile_url"),
            platform=self.platform_name, followers=raw_item.get("followers"), source_mode=SourceMode.LIVE,
            source_refs=[u for u in [raw_item.get("profile_url")] if u],
            topic_tags=topic_tags,
        )

    def normalize_creator(self, creator: Creator) -> Creator:
        creator.platform = self.platform_name
        return creator

    def normalize_integration(self, integration: Integration) -> Integration:
        integration.platform = self.platform_name
        return integration
