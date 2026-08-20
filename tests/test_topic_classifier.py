from __future__ import annotations

from app.topic_classifier import TAXONOMY, classify_topic


def test_taxonomy_is_fixed_and_matches_spec():
    # Раздел 2 hotfix: широкая, не завязанная на одну вертикаль таксономия.
    assert TAXONOMY == [
        "education", "student", "beauty", "fashion", "fitness", "sports", "food", "travel",
        "finance", "tech", "gaming", "career", "health", "parenting", "automotive",
        "entertainment", "lifestyle", "other",
    ]


def test_confident_match_health():
    result = classify_topic("Как я готовилась к ординатуре в мед вузе: анатомия и медицинский разбор")
    assert result.topic_tags == ["health"]
    assert result.topic_confidence > 0.5
    assert result.topic_evidence
    assert result.is_ambiguous is False


def test_confident_match_education():
    result = classify_topic("Как сдать сессию без стресса: подготовка к экзамену и ЕГЭ, зачёт с первого раза")
    assert "education" in result.topic_tags


def test_confident_match_fitness_sports_for_nike_style_content():
    """Раздел hotfix acceptance scenario 2 (Nike) - fitness/sports/sneakers content
    должен классифицироваться в fitness/sports, НЕ в education/student."""
    result = classify_topic("Утренняя тренировка workout и обзор новых sneakers для бега running")
    assert result.topic_tags[0] in ("fitness", "sports")
    assert "education" not in result.topic_tags
    assert "student" not in result.topic_tags


def test_confident_match_beauty():
    result = classify_topic("Сегодня туториал по макияжу makeup и уход за кожей skincare")
    assert result.topic_tags == ["beauty"]


def test_no_signal_falls_back_to_other():
    result = classify_topic("случайный текст без ключевых слов ничего по теме")
    assert result.topic_tags == ["other"]
    assert result.topic_confidence == 0.0


def test_ambiguous_case_without_llm_uses_deterministic_top_candidate(monkeypatch):
    from config.settings import settings as global_settings
    monkeypatch.setattr(global_settings, "anthropic_api_key", "")  # явно нет LLM в этом тесте

    # Два разных сигнала с одинаковым счётом ("курс" -> education, "карьера" -> career) -
    # margin между топ-1 и топ-2 равен 0, что делает случай неоднозначным.
    result = classify_topic("сегодня расскажу про курс и карьера")
    assert result.is_ambiguous is True
    assert result.used_llm is False
    assert result.topic_tags[0] in TAXONOMY
    assert 0.0 < result.topic_confidence < 0.5


def test_never_returns_topic_outside_taxonomy():
    samples = [
        "просто видео ни о чём",
        "обзор гаджета и нейросети",
        "финансовая грамотность и инвестиции",
        "мемы и юмор на выходных",
    ]
    for text in samples:
        result = classify_topic(text)
        for tag in result.topic_tags:
            assert tag in TAXONOMY
