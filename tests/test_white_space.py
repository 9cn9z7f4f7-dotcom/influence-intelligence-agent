from __future__ import annotations

from app.analytics.white_space import WhiteSpaceBuilder
from app.ingestion.demo_loader import DemoLoader
from app.models import Competitor, Creator, Integration, OurProfile


def test_excluded_topic_forces_zero_relevance(sample_creators, sample_competitors, sample_integrations,
                                                sample_our_profile, settings):
    builder = WhiteSpaceBuilder(sample_creators, sample_competitors, sample_integrations, sample_our_profile, settings)
    result = builder.build()
    fitness_segments = [s for s in result["segments"] if s["segment"]["topic"] == "fitness"]
    assert fitness_segments, "должен быть хотя бы один fitness-сегмент"
    for seg in fitness_segments:
        assert seg["our_relevance"] == 0.0


def test_relevant_low_saturation_segment_has_high_opportunity(sample_creators, sample_competitors,
                                                                 sample_integrations, sample_our_profile, settings):
    builder = WhiteSpaceBuilder(sample_creators, sample_competitors, sample_integrations, sample_our_profile, settings)
    result = builder.build()
    med_segments = [s for s in result["segments"] if s["segment"]["topic"] == "medical_students"]
    assert med_segments
    seg = med_segments[0]
    assert seg["our_relevance"] > 0
    assert seg["opportunity_score"] > 0
    assert seg["evidence_ids"]


def test_small_segment_flagged_insufficient_data(sample_creators, sample_competitors, sample_integrations,
                                                    sample_our_profile, settings):
    builder = WhiteSpaceBuilder(sample_creators, sample_competitors, sample_integrations, sample_our_profile, settings)
    result = builder.build()
    # Все сегменты в этой маленькой фикстуре имеют < 5 креаторов -> должны быть insufficient_data.
    for seg in result["segments"]:
        assert seg["insufficient_data"] is True
        assert seg["insufficient_data_reason"]


def test_known_demo_white_space_exists():
    """Проверяет реальный demo dataset (data/demo/*.json), а не фикстуры:
    сегмент medical_students/telegram/nano должен быть top-опортьюнити с релевантностью > 0."""
    loader = DemoLoader()
    if not loader.is_available():
        return  # demo dataset ещё не сгенерирован в этом окружении - тест не применим
    result = loader.fetch()
    our_profile_raw = loader.load_our_profile()
    our_profile = OurProfile.model_validate(our_profile_raw)

    from config.settings import settings as global_settings

    builder = WhiteSpaceBuilder(result.creators, result.competitors, result.integrations, our_profile, global_settings)
    ws = builder.build()
    target = next(
        (s for s in ws["segments"]
         if s["segment"]["topic"] == "medical_students" and s["segment"]["platform"] == "telegram"
         and s["segment"]["followers_bucket"] == "nano"),
        None,
    )
    assert target is not None, "known demo white space сегмент не найден"
    assert target["our_relevance"] > 0
    assert target["opportunity_score"] >= 50
    assert target["available_creators"] >= 20
