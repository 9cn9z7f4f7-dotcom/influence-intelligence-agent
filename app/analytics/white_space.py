"""
White Space — сегменты с релевантными креаторами, но низкой конкурентной
насыщенностью и высоким fit под наш профиль (раздел 10 мастер-промпта).

Никакого LLM. Opportunity score - явно объяснимая взвешенная формула из
config (settings.white_space_weights), не "чёрный ящик".
"""
from __future__ import annotations

from collections import defaultdict

from app.evidence import EvidenceStore, computed
from app.models import Competitor, Creator, CreatorSegment, Integration, OurProfile
from config.settings import Settings

MIN_SEGMENT_SAMPLE_SIZE = 5  # ниже этого числа available_creators - сегмент помечается insufficient_data


def _segment_for_creator(creator: Creator, settings: Settings) -> CreatorSegment:
    bucket = settings.bucket_for_value(creator.followers, settings.follower_buckets)
    topic = creator.topic_tags[0] if creator.topic_tags else None
    return CreatorSegment(platform=creator.platform, topic=topic, followers_bucket=bucket)


class WhiteSpaceBuilder:
    def __init__(self, creators: list[Creator], competitors: list[Competitor],
                 integrations: list[Integration], our_profile: OurProfile, settings: Settings,
                 evidence_store: EvidenceStore | None = None,
                 potential_creator_ids: set[str] | None = None) -> None:
        self.creators = creators
        self.competitors = competitors
        self.integrations = integrations
        self.our_profile = our_profile
        self.settings = settings
        self.evidence = evidence_store or EvidenceStore()
        self.creators_by_id = {c.creator_id: c for c in creators}
        self.competitors_by_id = {c.competitor_id: c for c in competitors}
        self.potential_creator_ids = potential_creator_ids or set()

    def build(self) -> dict:
        segments: dict[str, list[Creator]] = defaultdict(list)
        segment_obj: dict[str, CreatorSegment] = {}
        for creator in self.creators:
            seg = _segment_for_creator(creator, self.settings)
            segments[seg.key()].append(creator)
            segment_obj[seg.key()] = seg

        integrations_by_segment: dict[str, list[Integration]] = defaultdict(list)
        for i in self.integrations:
            creator = self.creators_by_id.get(i.creator_id)
            if not creator:
                continue
            seg = _segment_for_creator(creator, self.settings)
            integrations_by_segment[seg.key()].append(i)

        max_available = max((len(v) for v in segments.values()), default=1)
        total_competitors = max(1, len(self.competitors))
        recent_days = self.settings.dna_windows.get("recent_days", 30)
        historical_days = self.settings.dna_windows.get("historical_days", 90)
        # Ожидаемая доля recent-интеграций при равномерном потоке - выводится из
        # тех же окон, что и весь остальной pipeline (а не захардкожена отдельно),
        # чтобы при изменении dna_windows через config эта база не разъехалась.
        recent_baseline_share = recent_days / max(1, recent_days + historical_days)

        results = []
        for seg_key, seg_creators in segments.items():
            seg = segment_obj[seg_key]
            seg_integrations = integrations_by_segment.get(seg_key, [])
            available_creators = len(seg_creators)
            competitor_integrations = len(seg_integrations)
            confirmed_integrations = sum(1 for integration in seg_integrations if integration.category == "confirmed")
            active_competitor_ids = sorted({i.competitor_id for i in seg_integrations})
            unique_competitors = len(active_competitor_ids)

            now = max((i.published_at for i in seg_integrations if i.published_at), default=None)
            if now and seg_integrations:
                recent = [i for i in seg_integrations if i.published_at and (now - i.published_at).days <= recent_days]
                historical = [
                    i for i in seg_integrations
                    if i.published_at and recent_days < (now - i.published_at).days <= recent_days + historical_days
                ]
                total_rh = len(recent) + len(historical)
                recent_share = (len(recent) / total_rh) if total_rh else None
                recent_competitor_growth = (
                    round(recent_share - recent_baseline_share, 4) if recent_share is not None else None
                )
            else:
                recent_competitor_growth = None

            creator_supply_score = round(100 * available_creators / max_available, 1)

            our_relevance, relevance_notes = self._relevance_score(seg, seg_creators)

            coverage_ratio = unique_competitors / total_competitors
            density = competitor_integrations / available_creators if available_creators else 0.0
            base_saturation = 100 * min(1.0, 0.5 * coverage_ratio + 0.5 * min(1.0, density))
            growth_bump = max(0.0, recent_competitor_growth or 0.0) * 40
            saturation_score = round(min(100.0, base_saturation + growth_bump), 1)

            active_recent = sum(
                1 for c in seg_creators
                if c.last_seen_at and now and (now - c.last_seen_at).days <= recent_days
            ) if now else 0
            momentum_raw = (active_recent / available_creators) if available_creators else 0.0
            momentum_score = max(0.0, min(1.0, momentum_raw - max(0.0, recent_competitor_growth or 0.0)))

            weights = self.settings.white_space_weights
            opportunity_score = round(100 * (
                weights.get("supply", 0) * (creator_supply_score / 100)
                + weights.get("low_saturation", 0) * (1 - saturation_score / 100)
                + weights.get("our_relevance", 0) * (our_relevance / 100)
                + weights.get("momentum", 0) * momentum_score
            ), 1)

            used_creator_ids = {i.creator_id for i in seg_integrations}
            top_creators = sorted(
                seg_creators,
                key=lambda c: (c.creator_id not in used_creator_ids, c.engagement_rate or 0, c.avg_views or 0),
                reverse=True,
            )[:5]

            ev_id = self.evidence.add(computed(
                field=f"white_space:{seg_key}",
                value={
                    "available_creators": available_creators,
                    "competitor_integrations": competitor_integrations,
                    "confirmed_integrations": confirmed_integrations,
                    "unique_competitors": unique_competitors,
                    "saturation_score": saturation_score,
                    "opportunity_score": opportunity_score,
                },
                supporting_note=(
                    f"supply={creator_supply_score}, relevance={our_relevance} ({relevance_notes}), "
                    f"saturation={saturation_score}, momentum={round(momentum_score, 3)}"
                ),
            ))

            insufficient_data = available_creators < MIN_SEGMENT_SAMPLE_SIZE

            results.append({
                "segment": {
                    "key": seg_key,
                    "topic": seg.topic, "platform": seg.platform, "followers_bucket": seg.followers_bucket,
                    "label": seg.label(),
                },
                "available_creators": available_creators,
                "competitor_integrations": competitor_integrations,
                "confirmed_integrations": confirmed_integrations,
                "unique_competitors": unique_competitors,
                "active_competitors": [
                    self.competitors_by_id[competitor_id].name
                    if competitor_id in self.competitors_by_id else competitor_id
                    for competitor_id in active_competitor_ids
                ],
                "recent_competitor_growth": recent_competitor_growth,
                "creator_supply_score": creator_supply_score,
                "our_relevance": our_relevance,
                "our_relevance_notes": relevance_notes,
                "saturation_score": saturation_score,
                "opportunity_score": opportunity_score,
                "insufficient_data": insufficient_data,
                "insufficient_data_reason": (
                    f"available_creators={available_creators} < {MIN_SEGMENT_SAMPLE_SIZE} - score может быть шумным"
                    if insufficient_data else None
                ),
                "top_creators": [
                    {
                        "creator_id": c.creator_id, "name": c.name, "followers": c.followers,
                        "platform": c.platform, "median_views": c.median_views, "avg_views": c.avg_views,
                        "engagement_rate": c.engagement_rate, "canonical_url": c.canonical_url,
                        "topic_tags": c.topic_tags, "segment_match": 100,
                        "already_used_by_competitor": c.creator_id in used_creator_ids,
                        "has_organic_brand_affinity": c.creator_id in self.potential_creator_ids,
                    }
                    for c in top_creators
                ],
                "observed_sources": [
                    {
                        "source_url": integration.content_url,
                        "platform": integration.platform,
                        "creator": self.creators_by_id[integration.creator_id].name
                        if integration.creator_id in self.creators_by_id else integration.creator_id,
                        "published_at": integration.published_at.isoformat() if integration.published_at else None,
                        "classification": integration.article_category or integration.category,
                    }
                    for integration in seg_integrations if integration.content_url
                ],
                "evidence_ids": [ev_id],
            })

        results.sort(key=lambda r: r["opportunity_score"], reverse=True)
        return {
            "segments": results,
            "weights_used": self.settings.white_space_weights,
            "evidence": self.evidence.as_dict(),
        }

    def _relevance_score(self, seg: CreatorSegment, seg_creators: list[Creator]) -> tuple[float, str]:
        notes = []
        if seg.topic and seg.topic in self.our_profile.excluded_topics:
            return 0.0, f"тематика '{seg.topic}' явно исключена в our_profile.excluded_topics"

        score = 0.0
        if seg.topic and seg.topic in self.our_profile.preferred_topics:
            score += 40
            notes.append("topic match")
        if seg.platform and seg.platform in self.our_profile.platforms:
            score += 25
            notes.append("platform match")
        if seg.followers_bucket and seg.followers_bucket in self.our_profile.creator_size:
            score += 20
            notes.append("creator_size match")

        geos = {c.geo for c in seg_creators if c.geo}
        if self.our_profile.geo and geos & set(self.our_profile.geo):
            score += 15
            notes.append("geo match")
        elif not self.our_profile.geo:
            score += 15  # geo не задан в профиле - не штрафуем

        if self.our_profile.minimum_views is not None:
            avg_views = [c.avg_views for c in seg_creators if c.avg_views is not None]
            median_avg = sorted(avg_views)[len(avg_views) // 2] if avg_views else None
            if median_avg is not None and median_avg < self.our_profile.minimum_views:
                score *= 0.5
                notes.append(f"below minimum_views ({median_avg} < {self.our_profile.minimum_views})")

        return round(min(100.0, score), 1), "; ".join(notes) if notes else "no relevance signals"
