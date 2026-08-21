"""
Next Move — креаторы, которых конкурент ещё не использовал, но которые
максимально соответствуют его НАБЛЮДАЕМОМУ (не предсказанному) профилю закупки.

Важно: это Strategy Match, а не Prediction Probability (см. раздел 9
мастер-промпта). LLM здесь не участвует - score полностью deterministic
и объясняется через факторы (`why`).
"""
from __future__ import annotations

from collections import Counter

from app.analytics.aggregates import aggregate_integrations, creator_content_type_profile, top_key
from app.evidence import EvidenceStore, computed
from app.models import Competitor, Creator, Integration
from config.settings import Settings

FOLLOWER_BUCKET_ORDER = ["nano", "micro", "mid", "macro"]
VIEWS_BUCKET_ORDER = ["low", "medium", "high", "viral"]


def _bucket_distance_score(a: str | None, b: str | None, order: list[str]) -> float:
    if a is None or b is None:
        return 0.0
    if a == b:
        return 1.0
    try:
        ia, ib = order.index(a), order.index(b)
    except ValueError:
        return 0.0
    return 0.5 if abs(ia - ib) == 1 else 0.0


class NextMoveBuilder:
    def __init__(self, creators: list[Creator], integrations: list[Integration],
                 settings: Settings, evidence_store: EvidenceStore | None = None,
                 top_n: int = 10, potential_creator_ids: set[str] | None = None) -> None:
        self.creators = creators
        self.creators_by_id = {c.creator_id: c for c in creators}
        self.integrations = integrations
        self.settings = settings
        self.evidence = evidence_store or EvidenceStore()
        self.top_n = top_n
        self.content_type_profile = creator_content_type_profile(integrations)
        # Раздел 9/10 доработки: креаторы с organic brand affinity (см.
        # app/potential_creator.py), но без confirmed интеграции - Next Move
        # должен уметь отдельно пометить их как сильный candidate pool
        # ("Уже органически упоминает бренд, но подтверждённых интеграций не найдено.").
        self.potential_creator_ids = potential_creator_ids or set()

    def build_for_competitor(self, competitor: Competitor) -> dict:
        comp_integrations = [i for i in self.integrations if i.competitor_id == competitor.competitor_id]
        used_creator_ids = {i.creator_id for i in comp_integrations if i.category == "confirmed"}

        if not comp_integrations:
            # Organic-affinity creator-like entities remain useful hunting
            # candidates even when no confirmed integration exists. No numeric
            # Strategy Match is fabricated in this case.
            candidates = []
            for creator in self.creators:
                if creator.creator_id not in self.potential_creator_ids:
                    continue
                candidates.append({
                    "candidate": creator.name, "creator_id": creator.creator_id,
                    "platform": creator.platform, "topic": creator.topic_tags[0] if creator.topic_tags else None,
                    "topics": creator.topic_tags, "followers_bucket": self.settings.bucket_for_value(creator.followers, self.settings.follower_buckets),
                    "followers": creator.followers, "median_views": creator.median_views, "avg_views": creator.avg_views,
                    "engagement_rate": creator.engagement_rate, "canonical_url": creator.canonical_url,
                    "source_mode": creator.source_mode.value, "similarity_score": None, "match_label": "Недостаточно метрик",
                    "why": [], "evidence_ids": [], "has_organic_brand_affinity": True,
                    "not_used_by_brand": True,
                    "note": "Уже органически упоминает бренд, но подтверждённых интеграций не найдено.",
                })
            return {
                "competitor": competitor.name, "competitor_id": competitor.competitor_id,
                "candidates": candidates[: self.top_n],
                "insufficient_data": ["insufficient_confirmed_integrations"],
            }

        agg = aggregate_integrations(comp_integrations, self.creators_by_id, self.settings)
        preferred_size = top_key(agg["size"])
        preferred_topic = top_key(agg["topic"])
        preferred_platform = top_key(agg["platform"])
        preferred_content_type = top_key(agg["content_type"])
        preferred_views_bucket = top_key(agg["views_bucket"])

        now = max((i.published_at for i in comp_integrations if i.published_at), default=None)
        recent_days = self.settings.dna_windows.get("recent_days", 30)
        recent = [i for i in comp_integrations if now and i.published_at and (now - i.published_at).days <= recent_days]
        recent_agg = aggregate_integrations(recent, self.creators_by_id, self.settings) if recent else None

        profile_ev_id = self.evidence.add(computed(
            field=f"next_move_profile:{competitor.competitor_id}",
            value={
                "preferred_size": preferred_size, "preferred_topic": preferred_topic,
                "preferred_platform": preferred_platform, "preferred_content_type": preferred_content_type,
                "preferred_views_bucket": preferred_views_bucket,
            },
            supporting_note=f"Посчитано из {len(comp_integrations)} наблюдаемых интеграций {competitor.name}",
        ))

        candidates = []
        for creator in self.creators:
            if creator.creator_id in used_creator_ids:
                continue
            candidate_bucket = self.settings.bucket_for_value(creator.followers, self.settings.follower_buckets)
            candidate_views_bucket = self.settings.bucket_for_value(creator.avg_views, self.settings.views_buckets)
            candidate_topic = creator.topic_tags[0] if creator.topic_tags else None
            candidate_content_type = self.content_type_profile.get(creator.creator_id)

            factors = {
                "creator_size_match": _bucket_distance_score(candidate_bucket, preferred_size, FOLLOWER_BUCKET_ORDER),
                "topic_match": 1.0 if candidate_topic and candidate_topic == preferred_topic else 0.0,
                "platform_match": 1.0 if creator.platform == preferred_platform else 0.0,
                "content_type_match": (
                    1.0 if candidate_content_type and candidate_content_type == preferred_content_type
                    else (0.5 if candidate_content_type is None else 0.0)
                ),
                "views_profile_match": _bucket_distance_score(candidate_views_bucket, preferred_views_bucket, VIEWS_BUCKET_ORDER),
                "recent_strategy_match": self._recent_strategy_match(recent_agg, candidate_topic, creator.platform, candidate_bucket),
            }
            weights = self.settings.next_move_weights
            score = sum(factors[k] * weights.get(k, 0.0) for k in factors)
            similarity_score = round(score * 100)

            why = [
                {
                    "factor": factor_name,
                    "weight": weights.get(factor_name, 0.0),
                    "factor_score": round(factor_value, 3),
                    "contribution": round(factor_value * weights.get(factor_name, 0.0) * 100, 2),
                }
                for factor_name, factor_value in factors.items()
            ]

            has_organic_affinity = creator.creator_id in self.potential_creator_ids
            candidates.append({
                "candidate": creator.name,
                "creator_id": creator.creator_id,
                "platform": creator.platform,
                "topic": candidate_topic,
                "topics": creator.topic_tags,
                "followers_bucket": candidate_bucket,
                "followers": creator.followers,
                "median_views": creator.median_views,
                "avg_views": creator.avg_views,
                "engagement_rate": creator.engagement_rate,
                "canonical_url": creator.canonical_url,
                "source_mode": creator.source_mode.value,
                "similarity_score": similarity_score,
                "match_label": ("Высокое соответствие" if similarity_score >= 70 else "Среднее соответствие"),
                "why": why,
                "evidence_ids": [profile_ev_id],
                "has_organic_brand_affinity": has_organic_affinity,
                "not_used_by_brand": True,
                "note": (
                    "Уже органически упоминает бренд, но подтверждённых интеграций не найдено."
                    if has_organic_affinity else None
                ),
            })

        candidates.sort(key=lambda c: c["similarity_score"], reverse=True)
        return {
            "competitor": competitor.name,
            "competitor_id": competitor.competitor_id,
            "profile": {
                "preferred_size": preferred_size, "preferred_topic": preferred_topic,
                "preferred_platform": preferred_platform, "preferred_content_type": preferred_content_type,
                "preferred_views_bucket": preferred_views_bucket,
            },
            "candidates": candidates[: self.top_n],
            "insufficient_data": [] if recent_agg else ["no_recent_window_data_for_strategy_match"],
        }

    def build_all(self, competitors: list[Competitor]) -> list[dict]:
        return [self.build_for_competitor(c) for c in competitors]

    @staticmethod
    def _recent_strategy_match(recent_agg: dict[str, Counter] | None, topic: str | None,
                                platform: str | None, bucket: str | None) -> float:
        if not recent_agg:
            return 0.0
        recent_top_topic = top_key(recent_agg["topic"])
        recent_top_platform = top_key(recent_agg["platform"])
        recent_top_size = top_key(recent_agg["size"])
        matches = sum([
            1 for a, b in [(topic, recent_top_topic), (platform, recent_top_platform), (bucket, recent_top_size)]
            if a and b and a == b
        ])
        return matches / 3
