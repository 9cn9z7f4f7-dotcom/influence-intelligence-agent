"""
ArticlesPlatformAdapter - раздел 5 требований.

Flow: brand -> web discovery (реальный search API через app/search_client.py,
НЕ fake/scrape) -> candidate URLs -> ArticleParser -> ArticleClassifier ->
normalized Integration (+ Publisher).

Если реальный search API не настроен (нет SERPAPI_KEY) - discover_brand_content
честно возвращает status="unavailable" (симметрично YouTube без
YOUTUBE_API_KEY, Instagram/TikTok без auth) - НЕ имитирует найденные статьи.
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.article_classifier import ArticleClassifier
from app.article_parser import ArticleParseResult, ArticleParser
from app.brand_domain import BrandDomainProfile, build_brand_domain_profile_from_terms
from app.detection import escalate_with_affinity, escalate_with_hard_signals
from app.evidence import EvidenceStore, computed, fact
from app.hard_signals import detect_hard_commercial_signals
from app.ingestion.identifiers import stable_id
from app.ingestion.live_youtube import DetectorResult
from app.links_extractor import classify_links
from app.models import Creator, Integration, Publisher, SourceMode
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult
from app.potential_creator import detect_brand_affinity_signals
from app.query_generator import generate_article_queries
from app.search_client import SearchClient, get_default_search_client
from app.runtime_budget import budget_exhausted
from config.settings import settings as default_settings

MAX_CANDIDATE_URLS = 18
MAX_URLS_PER_QUERY = 5
MIN_ARTICLE_TEXT_LEN = 80

_POSITIVE_PATH_MARKERS = ("article", "blog", "news", "review", "journal", "story", "case")
_NEGATIVE_PATH_MARKERS = ("/product/", "/products/", "/shop/", "/cart", "/catalog", "/category/", "/buy/")
_NON_ARTICLE_TEXT_MARKERS = ("add to cart", "buy now", "shopping bag", "select size", "choose size", "добавить в корзину", "купить сейчас")


def _is_article_like(parsed: ArticleParseResult, search_evidence=None) -> bool:
    """Small deterministic content gate: keep editorial/article pages, reject storefront pages."""
    title = (parsed.title or getattr(search_evidence, "title", None) or "").strip()
    text = (parsed.main_text or "").strip()
    if not title:
        return False
    if len(text) < MIN_ARTICLE_TEXT_LEN and not (parsed.author or parsed.published_at):
        return False

    path = urlparse(parsed.canonical_url or parsed.source_url).path.lower()
    positive_path = any(marker in path for marker in _POSITIVE_PATH_MARKERS)
    negative_path = any(marker in path for marker in _NEGATIVE_PATH_MARKERS)
    lowered = f"{title} {text[:2500]}".lower()
    commerce_hits = sum(marker in lowered for marker in _NON_ARTICLE_TEXT_MARKERS)

    # A clearly editorial URL can still discuss products; otherwise reject
    # strong storefront structure before classification.
    if negative_path and not positive_path:
        return False
    if commerce_hits >= 2 and not positive_path:
        return False

    # Long prose is sufficient even when the URL has no semantic marker.
    return positive_path or len(text) >= 100 or bool(parsed.author) or bool(parsed.published_at)

# Раздел 5/23: "просто упоминание бренда - НЕ реклама" - маппинг детальной
# article_category (см. app/article_classifier.py) в "грубую" общепроектную
# category, которую понимает AnalysisConfig.allowed_integration_categories()
# и весь остальной pipeline (одинаково для youtube/instagram/tiktok/articles).
ARTICLE_CATEGORY_TO_PIPELINE_CATEGORY = {
    "confirmed_sponsored": "confirmed",
    "affiliate": "confirmed",
    "partner_content": "confirmed",
    "editorial_review": "organic_mention",
    "organic_mention": "organic_mention",
    "manual_review": "manual_review",
    "rejected": "rejected",
}


class ArticlesPlatformAdapter(PlatformAdapter):
    platform_name = "articles"

    def __init__(self, search_client: Optional[SearchClient] = None, parser: Optional[ArticleParser] = None,
                 classifier: Optional[ArticleClassifier] = None, settings=None) -> None:
        self.settings = settings or default_settings
        self.search_client = search_client or get_default_search_client(self.settings)
        self.parser = parser or ArticleParser()
        self.classifier = classifier or ArticleClassifier()
        self._domain_profile_cache: dict[tuple, BrandDomainProfile] = {}

    def _domain_profile(self, brand_terms: list[str]) -> BrandDomainProfile:
        key = tuple(brand_terms)
        if key not in self._domain_profile_cache:
            self._domain_profile_cache[key] = build_brand_domain_profile_from_terms(brand_terms)
        return self._domain_profile_cache[key]

    def discover_brand_content(self, brand: ResolvedBrand, config: AnalysisConfig) -> PlatformDiscoveryResult:
        if not self.search_client.is_available():
            return PlatformDiscoveryResult(
                platform="articles", status="unavailable", source_mode="none",
                reason="Search API не настроен (SERPAPI_KEY отсутствует) - live web discovery "
                       "для статей недоступен",
                import_hint="manage.py import-integrations --file <csv|json> (platform=articles)",
            )

        brand_terms = [brand.canonical_name] + brand.aliases
        queries = generate_article_queries(brand.canonical_name, brand.aliases)

        candidate_urls: list[str] = []
        # url -> исходный SearchResultItem - раздел 4 доработки: Tavily
        # snippet/content сохраняется как вспомогательный search_provider_evidence,
        # но НИКОГДА не подменяет ArticleParser как source of truth (см. ниже).
        search_results_by_url: dict[str, object] = {}
        queries_run: list[str] = []
        queries_failed: list[str] = []
        # Какие провайдеры реально ответили хоть раз за этот discovery-прогон
        # (раздел 6 доработки: coverage.search_provider) - приоритет tavily > serpapi,
        # т.к. tavily - PRIMARY.
        providers_used: set[str] = set()
        for query in queries:
            if budget_exhausted(75):
                queries_failed.append("time_budget")
                break
            if len(candidate_urls) >= MAX_CANDIDATE_URLS:
                break
            try:
                results = self.search_client.search(query, max_results=MAX_URLS_PER_QUERY)
                queries_run.append(query)
                used = getattr(self.search_client, "last_used_provider", None)
                if used:
                    providers_used.add(used)
                for r in results:
                    if r.url not in candidate_urls:
                        candidate_urls.append(r.url)
                        search_results_by_url[r.url] = r
            except Exception:  # noqa: BLE001 - search-провайдер (включая fallback) не должен ронять discovery
                queries_failed.append(query)

        search_provider = "tavily" if "tavily" in providers_used else ("serpapi" if "serpapi" in providers_used else None)

        if not candidate_urls:
            status = "degraded" if queries_failed else "ok"
            return PlatformDiscoveryResult(
                platform="articles", status=status, source_mode="live",
                reason=(f"часть запросов не выполнена: {queries_failed}" if queries_failed
                        else "Поиск не вернул кандидатов"),
                queries_run=queries_run, search_provider=search_provider,
                candidate_count=0, accepted_count=0,
            )

        raw_items: list[dict] = []
        for url in candidate_urls[:MAX_CANDIDATE_URLS]:
            if budget_exhausted(45):
                queries_failed.append("time_budget")
                break
            parsed = self.parser.parse(url)
            if parsed.status != "ok" or not parsed.main_text:
                continue
            search_evidence = search_results_by_url.get(url)
            if not _is_article_like(parsed, search_evidence):
                continue
            classification = self.classifier.classify(parsed.title or getattr(search_evidence, "title", None), parsed.main_text, brand_terms)
            raw_items.append({"parsed": parsed, "classification": classification, "search_evidence": search_evidence})

        status = "ok" if raw_items else ("degraded" if queries_failed else "ok")
        return PlatformDiscoveryResult(
            platform="articles", status=status, source_mode="live",
            reason=f"часть запросов не выполнена: {queries_failed}" if queries_failed else None,
            raw_items=raw_items, queries_run=queries_run, search_provider=search_provider,
            candidate_count=len(candidate_urls), accepted_count=len(raw_items),
        )

    def detect_integration(self, raw_item: dict, brand_terms: list[str]) -> DetectorResult:
        """Раздел 1/2/3/7/9 доработки, поверх НЕ ИЗМЕНЁННОГО ArticleClassifier:

          1. Links-first discovery (раздел 3/9): если текст статьи вообще не
             упоминает бренд (classification.category=="rejected"), но среди
             ArticleParser.outbound_links есть ссылка на brand/product domain -
             страница всё равно "обнаружена" (has_brand_evidence=True), а НЕ
             отбрасывается - раздел 9: "product link without explicit brand
             name still discovered".
          2. Hard commercial signal (раздел 1/7): promo-код/affiliate-ссылка/
             "в партнёрстве с BRAND"/явный commercial CTA + brand-ссылка и т.п.
             (включая ссылки со страницы, не только текст) поднимают категорию
             до "confirmed" НЕЗАВИСИМО от classification.confidence.
          3. Organic brand affinity без hard signal (раздел 2) поднимает
             "organic_mention" до "potential_creator" - см. app/analysis/pipeline.py,
             который для этой категории строит PotentialCreatorSignal, а НЕ Integration
             (раздел 2/11: "не увеличивать число confirmed integrations").
        """
        classification = raw_item["classification"]
        parsed: Optional[ArticleParseResult] = raw_item.get("parsed")
        pipeline_category = ARTICLE_CATEGORY_TO_PIPELINE_CATEGORY.get(classification.category, "rejected")
        has_brand_evidence = classification.has_brand_evidence
        has_commercial_evidence = classification.has_commercial_evidence

        profile = self._domain_profile(brand_terms)
        outbound_links = list(getattr(parsed, "outbound_links", None) or [])
        links = classify_links(outbound_links, profile)
        text_all = f"{getattr(parsed, 'title', '') or ''} {getattr(parsed, 'main_text', '') or ''}"

        discovered_via_link_url: Optional[str] = None
        if not has_brand_evidence:
            link_hit = next((l for l in links if l.is_brand_or_product), None)
            if link_hit is not None:
                has_brand_evidence = True
                discovered_via_link_url = link_hit.url
                # Раздел 9: честный минимум - "обнаружено", не "подтверждённая реклама",
                # пока hard signal (ниже) не докажет обратное.
                pipeline_category = "organic_mention"

        hard = detect_hard_commercial_signals(
            text_all, brand_name=brand_terms[0] if brand_terms else "",
            brand_aliases=brand_terms[1:] if len(brand_terms) > 1 else [], links=links,
        )
        new_category = escalate_with_hard_signals(pipeline_category, has_brand_evidence, hard.matched)

        affinity_signals: list[str] = []
        if new_category == pipeline_category:
            affinity_signals = detect_brand_affinity_signals(text_all, brand_terms)
            new_category = escalate_with_affinity(new_category, has_brand_evidence, affinity_signals)

        merged_signals = dict(classification.signals)
        for name, sig in hard.signals.items():
            if sig.get("matched"):
                merged_signals[f"hard:{name}"] = sig
        for phrase in affinity_signals:
            merged_signals[f"affinity:{phrase}"] = {"matched": True, "raw_fragment": phrase}
        if discovered_via_link_url:
            merged_signals["discovered_via_link"] = {"matched": True, "raw_fragment": discovered_via_link_url}

        reasons = list(dict.fromkeys(
            classification.reasons + hard.reasons + [f"affinity:{p}" for p in affinity_signals]
            + (["discovered_via_link"] if discovered_via_link_url else [])
        ))

        return DetectorResult(
            is_integration=new_category == "confirmed",
            confidence=classification.confidence,
            reasons=reasons,
            signals=merged_signals,
            category=new_category,
            has_brand_evidence=has_brand_evidence,
            has_commercial_evidence=has_commercial_evidence or hard.matched,
        )

    def extract_creator(self, raw_item: dict) -> Optional[Creator]:
        # Раздел 8: Publisher != Creator. Articles-платформа никогда не создаёт
        # Creator - см. build_publisher()/build_article_integration() ниже,
        # которые app/analysis/pipeline.py вызывает отдельным путём для
        # platform == "articles" (а не через extract_creator/normalize_creator).
        return None

    def normalize_creator(self, creator: Creator) -> Creator:
        creator.platform = "articles"
        return creator

    def normalize_integration(self, integration: Integration) -> Integration:
        integration.platform = "articles"
        return integration

    # ------------------------------------------------------------------
    # Публичные хелперы, используемые app/analysis/pipeline.py для articles
    # (отдельный путь от общего adapter.extract_creator()-флоу, т.к. у статьи
    # нет Creator - только Publisher, раздел 8).
    # ------------------------------------------------------------------
    @staticmethod
    def build_publisher(parsed: ArticleParseResult) -> Publisher:
        domain = parsed.domain or "unknown"
        return Publisher(
            publisher_id=stable_id("pub", domain),
            name=domain,
            domain=domain,
            platform="web_article",
            source_url=parsed.canonical_url or parsed.source_url,
        )

    def build_article_integration(
        self, raw_item: dict, competitor_id: str, evidence_store: EvidenceStore,
    ) -> tuple[Integration, Publisher]:
        parsed: ArticleParseResult = raw_item["parsed"]
        classification = raw_item["classification"]
        pipeline_category = ARTICLE_CATEGORY_TO_PIPELINE_CATEGORY.get(classification.category, "rejected")
        publisher = self.build_publisher(parsed)

        evidence_ids: list[str] = []
        for name, sig in classification.signals.items():
            if not sig.get("matched"):
                continue
            ev = fact(
                field=f"article_signal:{name}", value=True,
                source_url=parsed.canonical_url or parsed.source_url, observed_at=parsed.observed_at,
                raw_fragment=sig.get("raw_fragment"),
            )
            evidence_ids.append(evidence_store.add(ev))
        conf_ev = evidence_store.add(computed(
            field="article_classification_confidence", value=classification.confidence,
            supporting_note=f"category={classification.category}, reasons={classification.reasons}",
        ))
        evidence_ids.append(conf_ev)

        # Раздел 4 доработки: если discovery шёл через Tavily/SerpAPI и провайдер
        # вернул snippet/content для этого URL - сохраняем это как отдельное,
        # честно помеченное по источнику evidence (НЕ подменяет parsed.main_text -
        # ArticleParser остаётся canonical extraction).
        search_evidence = raw_item.get("search_evidence")
        if search_evidence is not None:
            provider_name = getattr(search_evidence, "source_provider", None) or "unknown"
            snippet_or_content = getattr(search_evidence, "content", None) or getattr(search_evidence, "snippet", None)
            if snippet_or_content:
                search_ev = fact(
                    field="search_provider_evidence", value=f"[{provider_name}] {snippet_or_content[:500]}",
                    source_url=parsed.canonical_url or parsed.source_url, observed_at=parsed.observed_at,
                    raw_fragment=f"source_provider={provider_name}",
                )
                evidence_ids.append(evidence_store.add(search_ev))

        integration_id = stable_id("article", parsed.canonical_url or parsed.source_url)
        integration = Integration(
            integration_id=integration_id, competitor_id=competitor_id,
            # creator_id - обязательное FK-поле в общей схеме Integration; для
            # статей мы намеренно НЕ создаём Creator (раздел 8), поэтому здесь
            # переиспользуется тот же id, что publisher_id - это НЕ регистрирует
            # publisher как Creator (он не попадает в список `creators`,
            # используемый Creator Universe/Next Move/White Space), это только
            # ключ группировки для Market Map (competitor_creator_matrix и т.п.).
            creator_id=publisher.publisher_id,
            platform="articles", content_url=parsed.canonical_url or parsed.source_url,
            published_at=parsed.published_at, content_type="article",
            detected_offer=None, detected_cta=None, detected_mechanic=classification.category,
            campaign_tags=[classification.category], raw_text=(parsed.main_text or "")[:2000],
            evidence=[evidence_store.resolve(eid) for eid in evidence_ids if evidence_store.resolve(eid)],
            is_synthetic=False, source_mode=SourceMode.LIVE, confidence=classification.confidence,
            ingestion_source="articles_web_search", category=pipeline_category,
            article_category=classification.category, publisher_id=publisher.publisher_id,
        )
        return integration, publisher
