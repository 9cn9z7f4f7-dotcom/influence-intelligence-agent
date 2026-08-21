"""
Our Move — объединяет Market Map, Competitor DNA, Next Move и White Space
в 3-5 конкретных, проверяемых гипотез (раздел 11 мастер-промпта).

Правило: Our Move не придумывает новых фактов - каждая гипотеза ссылается
ТОЛЬКО на evidence, посчитанный в предыдущих слоях. LLM (если доступна)
только переформулирует summary, не меняя цифры и не завышая уверенность.
"""
from __future__ import annotations

from typing import Any

from app.analytics.guardrails import enforce_confidence_wording, sanitize_statement
from app.analytics.llm import generate_our_move_summary
from app.models import OurProfile
from config.settings import Settings


def _confidence_wording(confidence: float, threshold: float, action: str, cautious: str) -> str:
    return action if confidence >= threshold else cautious


class OurMoveBuilder:
    def __init__(self, settings: Settings, our_profile: OurProfile | None = None) -> None:
        self.settings = settings
        self.our_profile = our_profile or OurProfile()

    def build(self, market_map: dict, competitor_dna: list[dict], next_moves: list[dict],
              white_space: dict) -> dict:
        candidates: list[dict[str, Any]] = []

        candidates.extend(self._from_white_space(white_space))
        candidates.extend(self._from_next_move(next_moves, white_space))
        candidates.extend(self._from_dna_shifts(competitor_dna))
        candidates.extend(self._from_saturation_warning(market_map))

        if len(candidates) < self.settings.our_move_min_items:
            publishers = (market_map.get("publishers") or {}).get("publishers") or []
            for pub in publishers[: self.settings.our_move_min_items - len(candidates)]:
                candidates.append({
                    "title": f"Проверить размещения: {pub.get('name') or pub.get('domain') or 'издание'}",
                    "priority": "medium",
                    "why_now": (
                        f"В наблюдаемой выборке найдено {pub.get('placements', 0)} размещение(я) у этого издания. "
                        "Стоит проверить формат и коммерческий сигнал вручную перед выводами о стратегии."
                    ),
                    "evidence": [],
                    "suggested_test": "Открыть исходные материалы и проверить тип размещения и релевантность аудитории.",
                    "creators": [],
                    "confidence": 0.4,
                })

        # Сортируем по confidence, но гарантируем минимум/максимум количество гипотез из конфига
        candidates.sort(key=lambda c: c["confidence"], reverse=True)
        min_items = self.settings.our_move_min_items
        max_items = self.settings.our_move_max_items
        selected = candidates[:max_items]
        if len(selected) < min_items:
            selected = candidates[:min_items]

        for item in selected:
            llm_summary = generate_our_move_summary({
                "title": item["title"], "why_now": item["why_now"],
                "confidence": item["confidence"], "suggested_test": item["suggested_test"],
            })
            # Guardrails: откатываемся на детерминированный текст, если LLM написала
            # запрещённую формулировку, и подстраховываем уверенную формулировку
            # низкой confidence припиской "стоит исследовать".
            safe_text = sanitize_statement(llm_summary, item["why_now"])
            item["why_now"] = enforce_confidence_wording(
                safe_text, item["confidence"], self.settings.low_confidence_threshold
            )

        return {"opportunities": selected, "generated_count": len(selected)}

    # ------------------------------------------------------------------
    def _from_white_space(self, white_space: dict) -> list[dict]:
        out = []
        segments = [s for s in white_space.get("segments", []) if s["our_relevance"] > 0]
        for seg in segments[:3]:
            confidence = round(min(0.95, seg["opportunity_score"] / 100), 2)
            if seg.get("insufficient_data"):
                # Малая выборка - никогда не даём уверенную формулировку, даже если score высокий.
                confidence = min(confidence, self.settings.low_confidence_threshold - 0.01)
            action = (
                f"Занять сегмент «{seg['segment']['label']}»: {seg['available_creators']} релевантных креаторов, "
                f"в наблюдаемой выборке конкурентная насыщенность {seg['saturation_score']}/100, "
                f"а релевантность профилю {seg['our_relevance']}/100."
            )
            cautious = (
                f"Сегмент «{seg['segment']['label']}» выглядит перспективным (opportunity {seg['opportunity_score']}/100), "
                f"но данных пока немного ({seg['available_creators']} креаторов) - стоит исследовать перед вложением бюджета."
            )
            unused = [c for c in seg["top_creators"] if not c["already_used_by_competitor"]]
            out.append({
                "title": f"Проверить сегмент: {seg['segment']['label']}",
                "priority": "high" if seg["opportunity_score"] >= 70 and not seg.get("insufficient_data") else "medium",
                "why_now": _confidence_wording(confidence, self.settings.low_confidence_threshold, action, cautious),
                "evidence": seg["evidence_ids"],
                "suggested_test": (
                    f"Пилотное размещение с {min(2, len(unused)) or 1} автором(ами) из списка сегмента, "
                    f"замерить CTR/конверсию оффера за 2 недели перед масштабированием."
                ),
                "creators": [c["name"] for c in unused[:3]] or [c["name"] for c in seg["top_creators"][:3]],
                "confidence": confidence,
                "related_type": "segment",
                "related_id": seg["segment"].get("key"),
                "related_label": seg["segment"]["label"],
            })
        return out

    def _from_next_move(self, next_moves: list[dict], white_space: dict) -> list[dict]:
        out = []
        all_candidates = []
        for nm in next_moves:
            for cand in nm.get("candidates", []):
                topic = cand.get("topic")
                # Наши гипотезы должны быть релевантны НАШЕЙ стратегии, а не только
                # стратегии конкурента - иначе "Our Move" превращается в чужой план закупки.
                if topic and self.our_profile.excluded_topics and topic in self.our_profile.excluded_topics:
                    continue
                if self.our_profile.preferred_topics and topic and topic not in self.our_profile.preferred_topics:
                    continue
                all_candidates.append((nm["competitor"], cand))
        all_candidates.sort(
            key=lambda pair: (pair[1].get("similarity_score") is not None, pair[1].get("similarity_score") or 0, pair[1].get("has_organic_brand_affinity", False)),
            reverse=True,
        )

        for competitor_name, cand in all_candidates[:2]:
            score = cand.get("similarity_score")
            if score is None:
                confidence = 0.45
                action = (
                    f"Проверить автора {cand['candidate']}: есть наблюдаемый органический интерес к бренду, "
                    "но метрик пока недостаточно для числового Strategy Match."
                )
                cautious = action
            else:
                confidence = round(min(0.95, score / 100), 2)
                action = (
                    f"Опередить {competitor_name}: {cand['candidate']} максимально соответствует их наблюдаемому "
                    f"профилю закупки (Strategy Match {score}/100), но ещё не использован(а) ими."
                )
                cautious = (
                    f"{cand['candidate']} умеренно соответствует профилю {competitor_name} "
                    f"(Strategy Match {score}/100) - стоит исследовать перед контактом."
                )
            out.append({
                "title": f"Проверить автора: {cand['candidate']}",
                "priority": "high" if (score is not None and score >= 75) else "medium",
                "why_now": _confidence_wording(confidence, self.settings.low_confidence_threshold, action, cautious),
                "evidence": cand["evidence_ids"],
                "suggested_test": "Тестовое размещение до появления конкурента в этом сегменте, отследить отклик аудитории.",
                "creators": [cand["candidate"]],
                "confidence": confidence,
                "related_type": "creator",
                "related_id": cand.get("creator_id"),
                "related_label": cand["candidate"],
            })
        return out

    def _from_dna_shifts(self, competitor_dna: list[dict]) -> list[dict]:
        out = []
        all_shifts = []
        for dna in competitor_dna:
            for shift in dna.get("recent_shifts", []):
                all_shifts.append((dna["competitor"], shift))
        all_shifts.sort(key=lambda pair: abs(pair[1]["delta"]), reverse=True)

        for competitor_name, shift in all_shifts[:1]:
            confidence = round(min(0.9, 0.4 + abs(shift["delta"])), 2)
            action = (
                f"{competitor_name} наблюдаемо смещается в сторону '{shift['key']}' ({shift['dimension']}): "
                f"{shift['statement']} Стоит проверить этот же вектор раньше, чем рынок насытится."
            )
            cautious = f"Возможный сдвиг у {competitor_name}: {shift['statement']} Недостаточно данных для уверенных действий - стоит исследовать."
            out.append({
                "title": f"Реакция на сдвиг стратегии {competitor_name}",
                "priority": "medium",
                "why_now": _confidence_wording(confidence, self.settings.low_confidence_threshold, action, cautious),
                "evidence": shift["evidence_ids"],
                "suggested_test": "Двухнедельный тест в том же направлении с ограниченным бюджетом, прежде чем расширяться.",
                "creators": [],
                "confidence": confidence,
            })
        return out

    def _from_saturation_warning(self, market_map: dict) -> list[dict]:
        out = []
        segments = market_map.get("market", {}).get("segment_saturation", [])
        overheated = [s for s in segments if s["saturation_score"] >= 70]
        if not overheated:
            return out
        seg = overheated[0]
        confidence = round(min(0.9, seg["saturation_score"] / 100), 2)
        action = (
            f"Сегмент «{seg['label']}» перегрет ({seg['saturation_score']}/100 saturation, "
            f"{seg['unique_competitors']} конкурентов, {seg['competitor_integrations']} интеграций) - "
            f"не стоит входить туда без явного дифференцированного оффера."
        )
        out.append({
            "title": f"Не входить в перегретый сегмент: {seg['label']}",
            "priority": "medium",
            "why_now": action,
            "evidence": seg.get("evidence_ids", []),
            "suggested_test": "Если всё же тестировать, использовать нестандартный оффер/механику, отличную от доминирующих в сегменте.",
            "creators": [],
            "confidence": confidence,
            "related_type": "segment",
            "related_id": seg.get("segment_key"),
            "related_label": seg["label"],
        })
        return out
