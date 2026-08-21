"""
Competitor DNA — reverse engineering наблюдаемой стратегии конкурента.

Порядок работы (строго по мастер-промпту, раздел 8):
  1. Кодом считаем агрегаты (preferred sizes/platforms/topics, repeat usage,
     mechanics, offers, content formats, recent vs historical).
  2. ТОЛЬКО агрегаты + evidence_ids передаём в LLM (app/analytics/llm.py).
  3. Если LLM недоступна - возвращаем computed patterns без "красивого текста".
  4. Каждая гипотеза требует >= min_hypothesis_observations supporting observations,
     иначе confidence занижается и это явно видно.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from app.analytics.aggregates import aggregate_integrations
from app.analytics.guardrails import sanitize_statement
from app.analytics.llm import generate_patterns
from app.evidence import EvidenceStore, ai_inference, computed
from app.models import Competitor, Creator, Integration
from config.settings import Settings


def _share_dict(counter: Counter) -> dict[str, float]:
    total = sum(counter.values())
    if not total:
        return {}
    return {k: round(v / total, 4) for k, v in counter.items()}


def _top_key(counter: Counter) -> tuple[str, float] | None:
    if not counter:
        return None
    total = sum(counter.values())
    key, count = counter.most_common(1)[0]
    return key, round(count / total, 4) if total else 0.0


class CompetitorDnaBuilder:
    def __init__(self, creators: list[Creator], integrations: list[Integration],
                 settings: Settings, evidence_store: EvidenceStore | None = None) -> None:
        self.creators_by_id = {c.creator_id: c for c in creators}
        self.integrations = integrations
        self.settings = settings
        self.evidence = evidence_store or EvidenceStore()

    def build(self, competitor: Competitor) -> dict[str, Any]:
        comp_integrations = [i for i in self.integrations if i.competitor_id == competitor.competitor_id]

        if len(comp_integrations) == 0:
            return {
                "competitor": competitor.name,
                "competitor_id": competitor.competitor_id,
                "observed_patterns": [],
                "recent_shifts": [],
                "insufficient_data": ["no_integrations_observed"],
            }

        now = max((i.published_at for i in comp_integrations if i.published_at), default=None)
        recent_days = self.settings.dna_windows.get("recent_days", 30)
        historical_days = self.settings.dna_windows.get("historical_days", 90)

        recent, historical = self._split_windows(comp_integrations, now, recent_days, historical_days)

        overall_agg = self._aggregate(comp_integrations)
        recent_agg = self._aggregate(recent)
        historical_agg = self._aggregate(historical)

        insufficient_data: list[str] = []
        if len(comp_integrations) < 5:
            insufficient_data.append("low_sample_size_overall")
        if not historical:
            insufficient_data.append("no_historical_window_data")
        if not recent:
            insufficient_data.append("no_recent_window_data")

        # Percentage-based strategy statements are unsafe on tiny samples.
        # Only creator-like confirmed integrations can support a creator strategy.
        creator_like_confirmed = [
            i for i in comp_integrations if i.category == "confirmed" and i.platform != "articles"
        ]
        if len(creator_like_confirmed) < 3:
            recent_shifts = []
            # Do not pretend this is a stable buying strategy.  Still describe
            # what is visible in the broader observed sample (confirmed + organic
            # brand mentions) using absolute counts, not 100%-style conclusions.
            observed_patterns = self._build_sample_observations(competitor.name, overall_agg, comp_integrations)
        else:
            recent_shifts = self._detect_shifts(recent_agg, historical_agg, len(recent), len(historical))
            candidate_patterns = self._build_candidate_patterns(competitor.name, overall_agg, creator_like_confirmed)
            observed_patterns = self._resolve_patterns_via_llm_or_fallback(competitor.name, candidate_patterns)

        return {
            "competitor": competitor.name,
            "competitor_id": competitor.competitor_id,
            "observed_patterns": observed_patterns,
            "recent_shifts": recent_shifts,
            "insufficient_data": insufficient_data,
            "windows": {"recent_days": recent_days, "historical_days": historical_days},
            "confirmed_creator_integrations": len(creator_like_confirmed),
            "strategy_message": (
                "Устойчивый рекламный паттерн пока не подтверждён. Ниже — описательные сигналы по найденной выборке."
                if len(creator_like_confirmed) < 3 else None
            ),
        }


    def _build_sample_observations(self, competitor_name: str, agg: dict[str, Counter], integrations: list[Integration]) -> list[dict]:
        """Safe descriptive fallback for a small/non-confirmed sample.

        It answers "what do we see" without claiming a proven brand strategy.
        """
        if not integrations:
            return []
        rows: list[dict] = []
        labels = [("platform", "площадка"), ("topic", "тематика"), ("content_type", "формат") ]
        for dimension, label in labels:
            counter = agg.get(dimension) or Counter()
            if not counter:
                continue
            key, count = counter.most_common(1)[0]
            if not key or key in {"unknown", "other", "-"}:
                continue
            ev_id = self.evidence.add(computed(
                field=f"sample:{dimension}:{key}",
                value={"count": count, "sample_size": len(integrations)},
                supporting_note=f"{competitor_name}: {count} из {len(integrations)} наблюдаемых материалов; {label}={key}",
            ))
            rows.append({
                "statement": f"В наблюдаемой выборке чаще встречается {label} «{key}»: {count} из {len(integrations)} материалов.",
                "type": "computed",
                "confidence": min(0.75, 0.4 + 0.05 * count),
                "supporting_metrics": [{"dimension": dimension, "key": key, "supporting_observations": count}],
                "evidence_ids": [ev_id],
            })
        return rows[:3]

    # ------------------------------------------------------------------
    def _split_windows(self, integrations: list[Integration], now: datetime | None,
                        recent_days: int, historical_days: int) -> tuple[list[Integration], list[Integration]]:
        if now is None:
            return [], []
        recent = [i for i in integrations if i.published_at and (now - i.published_at).days <= recent_days]
        historical = [
            i for i in integrations
            if i.published_at and recent_days < (now - i.published_at).days <= recent_days + historical_days
        ]
        return recent, historical

    def _aggregate(self, integrations: list[Integration]) -> dict[str, Counter]:
        return aggregate_integrations(integrations, self.creators_by_id, self.settings)

    def _detect_shifts(self, recent_agg: dict[str, Counter], historical_agg: dict[str, Counter],
                        recent_n: int, historical_n: int) -> list[dict]:
        shifts: list[dict] = []
        if recent_n < 2 or historical_n < 2:
            return shifts  # недостаточно данных для честного сравнения

        for dimension in ["platform", "topic", "size"]:
            recent_share = _share_dict(recent_agg[dimension])
            historical_share = _share_dict(historical_agg[dimension])
            keys = set(recent_share) | set(historical_share)
            for key in keys:
                r = recent_share.get(key, 0.0)
                h = historical_share.get(key, 0.0)
                delta = round(r - h, 4)
                if abs(delta) >= 0.25:  # порог значимого сдвига, вынесен как константа модуля
                    ev_id = self.evidence.add(computed(
                        field=f"shift:{dimension}:{key}",
                        value={"recent_share": r, "historical_share": h, "delta": delta},
                        supporting_note=f"recent_n={recent_n}, historical_n={historical_n}",
                    ))
                    direction = "выросла" if delta > 0 else "снизилась"
                    shifts.append({
                        "dimension": dimension,
                        "key": key,
                        "recent_share": r,
                        "historical_share": h,
                        "delta": delta,
                        "statement": (
                            f"За последние {recent_n} наблюдений доля '{key}' в измерении "
                            f"'{dimension}' {direction} с {round(h * 100, 1)}% до {round(r * 100, 1)}%."
                        ),
                        "evidence_ids": [ev_id],
                    })
        shifts.sort(key=lambda s: abs(s["delta"]), reverse=True)
        return shifts

    def _build_candidate_patterns(self, competitor_name: str, agg: dict[str, Counter],
                                   integrations: list[Integration]) -> list[dict]:
        """Формирует кандидатов на паттерн + считает supporting observations и evidence."""
        candidates = []
        dims = [
            ("platform", "площадка", agg["platform"]),
            ("topic", "тематика", agg["topic"]),
            ("size", "размер креатора", agg["size"]),
            ("content_type", "формат контента", agg["content_type"]),
            ("offer", "офер", agg["offer"]),
            ("mechanic", "механика", agg["mechanic"]),
        ]
        for dim_key, dim_label, counter in dims:
            top = _top_key(counter)
            if not top:
                continue
            key, share = top
            supporting_count = counter[key]
            ev_id = self.evidence.add(computed(
                field=f"dna:{dim_key}:{key}",
                value={"count": supporting_count, "share": share},
                supporting_note=f"{competitor_name}: {supporting_count} наблюдений с {dim_key}={key}",
            ))
            candidates.append({
                "dimension": dim_key,
                "dimension_label": dim_label,
                "key": key,
                "share": share,
                "supporting_observations": supporting_count,
                "evidence_ids": [ev_id],
            })

        repeat_counter = agg["creator_repeat"]
        repeated = {cid: n for cid, n in repeat_counter.items() if n > 1}
        if repeated:
            ev_id = self.evidence.add(computed(
                field="dna:repeat_creators",
                value={"repeated_creator_count": len(repeated)},
                supporting_note=f"{competitor_name}: {len(repeated)} креаторов использованы повторно",
            ))
            candidates.append({
                "dimension": "repeat_usage",
                "dimension_label": "повторное использование креаторов",
                "key": "repeat",
                "share": round(len(repeated) / max(1, len(repeat_counter)), 4),
                "supporting_observations": sum(repeated.values()),
                "evidence_ids": [ev_id],
            })
        return candidates

    def _resolve_patterns_via_llm_or_fallback(self, competitor_name: str,
                                               candidates: list[dict]) -> list[dict]:
        min_obs = self.settings.min_hypothesis_observations
        aggregates_for_llm = [
            {
                "dimension": c["dimension"], "key": c["key"], "share": c["share"],
                "supporting_observations": c["supporting_observations"],
            }
            for c in candidates
        ]

        llm_patterns = generate_patterns(competitor_name, {"top_patterns": aggregates_for_llm})

        patterns: list[dict] = []
        if llm_patterns:
            # LLM доступна: используем её формулировки, но confidence и evidence всё равно
            # привязаны к computed-кандидатам, а не к тому, что "сказала" модель.
            for idx, cand in enumerate(candidates):
                llm_item = llm_patterns[idx] if idx < len(llm_patterns) else None
                base_confidence = min(0.95, 0.4 + 0.15 * cand["supporting_observations"])
                if cand["supporting_observations"] < min_obs:
                    base_confidence = min(base_confidence, 0.35)
                llm_statement = llm_item.get("statement") if isinstance(llm_item, dict) else None
                # Последний рубеж защиты: если LLM всё же написала запрещённую
                # формулировку ("конкурент точно сделает X"), откатываемся на
                # детерминированный шаблон, а не показываем это жюри.
                statement = sanitize_statement(llm_statement, self._fallback_statement(cand))
                confidence = base_confidence
                if isinstance(llm_item, dict) and isinstance(llm_item.get("confidence"), (int, float)):
                    confidence = round(min(base_confidence, float(llm_item["confidence"])), 3)
                ev = ai_inference(
                    field=f"pattern:{cand['dimension']}",
                    value=cand["key"],
                    confidence=confidence,
                    raw_fragment=statement,
                )
                ev_id = self.evidence.add(ev)
                patterns.append({
                    "statement": statement,
                    "type": "ai_inference",
                    "confidence": confidence,
                    "supporting_metrics": [{
                        "dimension": cand["dimension"], "key": cand["key"], "share": cand["share"],
                        "supporting_observations": cand["supporting_observations"],
                    }],
                    "evidence_ids": cand["evidence_ids"] + [ev_id],
                })
        else:
            # LLM недоступна/упала -> computed patterns без "красивого текста"
            for cand in candidates:
                confidence = 1.0 if cand["supporting_observations"] >= min_obs else 0.4
                patterns.append({
                    "statement": self._fallback_statement(cand),
                    "type": "computed",
                    "confidence": confidence,
                    "supporting_metrics": [{
                        "dimension": cand["dimension"], "key": cand["key"], "share": cand["share"],
                        "supporting_observations": cand["supporting_observations"],
                    }],
                    "evidence_ids": cand["evidence_ids"],
                })
        return patterns

    @staticmethod
    def _fallback_statement(cand: dict) -> str:
        pct = round(cand["share"] * 100, 1)
        return (
            f"В наблюдаемой выборке чаще встречается {cand['dimension_label']} = '{cand['key']}' "
            f"({pct}% интеграций, {cand['supporting_observations']} наблюдений)."
        )
