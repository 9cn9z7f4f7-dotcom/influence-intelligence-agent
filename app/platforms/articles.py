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

from app.analysis.models import AnalysisConfig, ResolvedBrand
from app.article_classifier import ArticleClassifier
from app.article_parser import ArticleParseResult, ArticleParser
from app.evidence import EvidenceStore, computed, fact
from app.ingestion.identifiers import stable_id
from app.ingestion.live_youtube import DetectorResult
from app.models import Creator, Integration, Publisher, SourceMode
from app.platforms.base import PlatformAdapter, PlatformDiscoveryResult
from app.query_generator import generate_article_queries
from app.search_client import SearchClient, get_default_search_client
from config.settings import settings as default_settings

MAX_CANDIDATE_URLS = 25
MAX_URLS_PER_QUERY = 6

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
            )

        raw_items: list[dict] = []
        for url in candidate_urls[:MAX_CANDIDATE_URLS]:
            parsed = self.parser.parse(url)
            if parsed.status != "ok" or not parsed.main_text:
                # Страница недоступна/пустая - раздел 4: "не придумывать содержимое".
                # ArticleParser остаётся единственным source of truth для текста -
                # если он не смог получить страницу, кандидат просто отбрасывается
                # (даже если search-провайдер вернул snippet/content для него).
                continue
            classification = self.classifier.classify(parsed.title, parsed.main_text, brand_terms)
            search_evidence = search_results_by_url.get(url)
            raw_items.append({"parsed": parsed, "classification": classification, "search_evidence": search_evidence})

        status = "ok" if raw_items else ("degraded" if queries_failed else "ok")
        return PlatformDiscoveryResult(
            platform="articles", status=status, source_mode="live",
            reason=f"часть запросов не выполнена: {queries_failed}" if queries_failed else None,
            raw_items=raw_items, queries_run=queries_run, search_provider=search_provider,
        )

    def detect_integration(self, raw_item: dict, brand_terms: list[str]) -> DetectorResult:
        classification = raw_item["classification"]
        pipeline_category = ARTICLE_CATEGORY_TO_PIPELINE_CATEGORY.get(classification.category, "rejected")
        return DetectorResult(
            is_integration=pipeline_category == "confirmed",
            confidence=classification.confidence,
            reasons=classification.reasons,
            signals=classification.signals,
            category=pipeline_category,
            has_brand_evidence=classification.has_brand_evidence,
            has_commercial_evidence=classification.has_commercial_evidence,
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
