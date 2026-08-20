"""
Market Map — что реально происходит на рынке.

Только код, без LLM (см. раздел 7 мастер-промпта). Все метрики - COMPUTED,
и остаются объяснимыми: их всегда можно пересчитать вручную из
Integration/Creator записей.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime

from app.evidence import EvidenceStore, computed
from app.models import Competitor, Creator, CreatorSegment, Integration, Publisher
from config.settings import Settings

# Раздел 9 требований - Publisher Map НЕ отдельный продукт, но Market Map должен
# уметь дополнительно показывать publishers used/placement frequency/topics/mix,
# не трогая существующую creator-аналитику ниже (repeated_creator_rate/
# platform_distribution/segment_saturation и т.п. остаются без изменений).
_ARTICLE_REVIEW_SIGNAL_NAMES = {"review_wording"}


def _article_content_format(integration: Integration) -> str:
    """Лёгкая производная метка формата контента статьи (раздел 9: "content
    format") - НЕ то же самое, что sponsored/affiliate/editorial category mix
    (раздел 7/9 явно перечисляют их как два разных срез)."""
    signals = {}
    for ev in integration.evidence or []:
        if ev.field and ev.field.startswith("article_signal:"):
            signals[ev.field.split(":", 1)[1]] = ev.value
    if signals.get("sponsor_wording"):
        return "sponsored_post"
    if signals.get("review_wording"):
        return "review"
    if signals.get("affiliate_pattern"):
        return "affiliate_post"
    return "mention"


def _iso_week(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _segment_for_creator(creator: Creator, settings: Settings) -> CreatorSegment:
    bucket = settings.bucket_for_value(creator.followers, settings.follower_buckets)
    topic = creator.topic_tags[0] if creator.topic_tags else None
    return CreatorSegment(platform=creator.platform, topic=topic, followers_bucket=bucket)


class MarketMapBuilder:
    def __init__(self, creators: list[Creator], competitors: list[Competitor],
                 integrations: list[Integration], settings: Settings,
                 evidence_store: EvidenceStore | None = None,
                 publishers: list[Publisher] | None = None) -> None:
        self.creators = creators
        self.competitors = competitors
        self.integrations = integrations
        self.settings = settings
        self.evidence = evidence_store or EvidenceStore()
        self.creators_by_id = {c.creator_id: c for c in creators}
        # Раздел 8-9: publishers - НЕ creators, отдельный список, влияющий
        # только на дополнительную publishers-секцию ниже.
        self.publishers = publishers or []
        self.publishers_by_id = {p.publisher_id: p for p in self.publishers}

    def build(self) -> dict:
        per_competitor = [self._competitor_stats(c) for c in self.competitors]
        market = self._market_stats()
        result = {
            "generated_from": {
                "creators": len(self.creators),
                "competitors": len(self.competitors),
                "integrations": len(self.integrations),
            },
            "competitors": per_competitor,
            "market": market,
            "evidence": self.evidence.as_dict(),
        }
        publishers_section = self._publisher_stats()
        if publishers_section is not None:
            result["publishers"] = publishers_section
        return result

    # ------------------------------------------------------------------
    # Раздел 9: publishers used / placement frequency / topics / mix - НЕ
    # отдельный продукт, дополнительная секция существующего Market Map.
    # Existing creator-аналитика (_competitor_stats/_market_stats) не изменена.
    # ------------------------------------------------------------------
    def _publisher_stats(self) -> dict | None:
        article_integrations = [i for i in self.integrations if i.platform == "articles" and i.publisher_id]
        if not article_integrations and not self.publishers:
            return None

        placement_freq = Counter(i.publisher_id for i in article_integrations if i.publisher_id)
        category_mix = Counter(i.article_category or "unknown" for i in article_integrations)
        content_format_dist = Counter(_article_content_format(i) for i in article_integrations)

        publishers_used = []
        for pub_id, count in placement_freq.most_common():
            pub = self.publishers_by_id.get(pub_id)
            publishers_used.append({
                "publisher_id": pub_id,
                "name": pub.name if pub else pub_id,
                "domain": pub.domain if pub else None,
                "placements": count,
            })
        repeated_publishers = [p for p in publishers_used if p["placements"] > 1]

        return {
            "publishers_used_count": len(placement_freq),
            "publishers": publishers_used,
            "repeated_publishers": repeated_publishers,
            "category_mix": dict(category_mix),  # sponsored/affiliate/editorial/organic
            "content_format_distribution": dict(content_format_dist),
            "total_article_integrations": len(article_integrations),
        }

    # ------------------------------------------------------------------
    def _competitor_stats(self, competitor: Competitor) -> dict:
        comp_integrations = [i for i in self.integrations if i.competitor_id == competitor.competitor_id]
        creator_ids = [i.creator_id for i in comp_integrations]
        unique_creators = set(creator_ids)
        counts = Counter(creator_ids)
        repeat_creators = sum(1 for _cid, n in counts.items() if n > 1)
        repeat_rate = round(repeat_creators / len(unique_creators), 4) if unique_creators else 0.0

        platform_dist = Counter(i.platform for i in comp_integrations if i.platform)
        content_type_dist = Counter(i.content_type for i in comp_integrations if i.content_type)
        offer_dist = Counter(i.detected_offer for i in comp_integrations if i.detected_offer)
        mechanic_dist = Counter(i.detected_mechanic for i in comp_integrations if i.detected_mechanic)

        size_dist: Counter = Counter()
        topic_dist: Counter = Counter()
        for i in comp_integrations:
            creator = self.creators_by_id.get(i.creator_id)
            if not creator:
                continue
            bucket = self.settings.bucket_for_value(creator.followers, self.settings.follower_buckets)
            if bucket:
                size_dist[bucket] += 1
            if creator.topic_tags:
                topic_dist[creator.topic_tags[0]] += 1

        by_week: Counter = Counter()
        for i in comp_integrations:
            if i.published_at:
                by_week[_iso_week(i.published_at)] += 1

        stats = {
            "competitor_id": competitor.competitor_id,
            "name": competitor.name,
            "total_integrations": len(comp_integrations),
            "unique_creators": len(unique_creators),
            "repeat_creator_rate": repeat_rate,
            "platform_distribution": dict(platform_dist),
            "creator_size_distribution": dict(size_dist),
            "topic_distribution": dict(topic_dist),
            "content_type_distribution": dict(content_type_dist),
            "offer_distribution": dict(offer_dist),
            "mechanic_distribution": dict(mechanic_dist),
            "integrations_by_week": dict(sorted(by_week.items())),
        }

        ev_id = self.evidence.add(computed(
            field=f"competitor_stats:{competitor.competitor_id}",
            value={"total_integrations": stats["total_integrations"], "unique_creators": stats["unique_creators"]},
            supporting_note=f"Посчитано из {len(comp_integrations)} integration-записей для {competitor.name}",
        ))
        stats["evidence_ids"] = [ev_id]
        return stats

    def _market_stats(self) -> dict:
        competitor_creator_matrix: dict[str, dict[str, int]] = defaultdict(dict)
        competitor_segment_matrix: dict[str, dict[str, dict]] = defaultdict(dict)
        segment_supply: Counter = Counter()          # available creators per segment
        segment_demand: dict[str, dict] = defaultdict(lambda: {"integrations": 0, "competitors": set()})

        for i in self.integrations:
            creator_counts = competitor_creator_matrix[i.competitor_id]
            creator_counts[i.creator_id] = creator_counts.get(i.creator_id, 0) + 1

        for creator in self.creators:
            seg = _segment_for_creator(creator, self.settings)
            segment_supply[seg.key()] += 1

        segment_labels: dict[str, str] = {}
        for i in self.integrations:
            creator = self.creators_by_id.get(i.creator_id)
            if not creator:
                continue
            seg = _segment_for_creator(creator, self.settings)
            segment_labels[seg.key()] = seg.label()
            demand = segment_demand[seg.key()]
            demand["integrations"] += 1
            demand["competitors"].add(i.competitor_id)

            comp_seg = competitor_segment_matrix[i.competitor_id]
            entry = comp_seg.setdefault(seg.key(), {"label": seg.label(), "integrations": 0, "unique_creators": set()})
            entry["integrations"] += 1
            entry["unique_creators"].add(i.creator_id)

        for comp_id, segs in competitor_segment_matrix.items():
            for seg_key, entry in segs.items():
                entry["unique_creators"] = len(entry["unique_creators"])

        segment_saturation = []
        total_competitors = max(1, len(self.competitors))
        for seg_key, supply in segment_supply.items():
            demand = segment_demand.get(seg_key, {"integrations": 0, "competitors": set()})
            unique_competitors = len(demand["competitors"])
            integrations_count = demand["integrations"]
            coverage_ratio = unique_competitors / total_competitors
            density = integrations_count / supply if supply else 0.0
            saturation_score = round(100 * min(1.0, 0.6 * coverage_ratio + 0.4 * min(1.0, density)), 1)
            sat_ev_id = self.evidence.add(computed(
                field=f"segment_saturation:{seg_key}",
                value={"saturation_score": saturation_score, "unique_competitors": unique_competitors,
                       "competitor_integrations": integrations_count, "available_creators": supply},
                supporting_note=f"coverage_ratio={round(coverage_ratio, 3)}, density={round(density, 3)}",
            ))
            segment_saturation.append({
                "segment_key": seg_key,
                "label": segment_labels.get(seg_key, seg_key),
                "available_creators": supply,
                "competitor_integrations": integrations_count,
                "unique_competitors": unique_competitors,
                "saturation_score": saturation_score,
                "evidence_ids": [sat_ev_id],
            })
        segment_saturation.sort(key=lambda s: s["saturation_score"], reverse=True)

        platform_share = Counter(i.platform for i in self.integrations if i.platform)
        size_share: Counter = Counter()
        for i in self.integrations:
            creator = self.creators_by_id.get(i.creator_id)
            if creator:
                bucket = self.settings.bucket_for_value(creator.followers, self.settings.follower_buckets)
                if bucket:
                    size_share[bucket] += 1

        recent_cutoff_days = self.settings.dna_windows.get("recent_days", 30)
        now = max((i.published_at for i in self.integrations if i.published_at), default=None)
        recent_trend = Counter()
        if now:
            for i in self.integrations:
                if i.published_at and (now - i.published_at).days <= recent_cutoff_days:
                    recent_trend[i.competitor_id] += 1

        return {
            "competitor_creator_matrix": {
                comp: dict(counts) for comp, counts in competitor_creator_matrix.items()
            },
            "competitor_segment_matrix": {
                comp: {seg: entry for seg, entry in segs.items()}
                for comp, segs in competitor_segment_matrix.items()
            },
            "segment_saturation": segment_saturation,
            "share_by_platform": dict(platform_share),
            "share_by_creator_size": dict(size_share),
            "recent_activity_trend": dict(recent_trend),
            "recent_window_days": recent_cutoff_days,
        }
