from __future__ import annotations

from app.analytics.guardrails import contains_forbidden_claim, enforce_confidence_wording, sanitize_statement


def test_contains_forbidden_claim_detects_banned_phrases():
    assert contains_forbidden_claim("Конкурент точно сделает X в следующем месяце")
    assert contains_forbidden_claim("Они хотят зайти в этот сегмент")
    assert not contains_forbidden_claim("В наблюдаемой выборке чаще встречается X")


def test_contains_forbidden_claim_detects_new_overclaiming_phrases():
    """Раздел 15 требований нового flow: запрещённые преувеличивающие формулировки."""
    assert contains_forbidden_claim("Мы нашли весь рынок инфлюенсеров в этой нише")
    assert contains_forbidden_claim("Рынок свободен для входа")
    assert contains_forbidden_claim("Конкурент пойдёт к этому блогеру в следующем месяце")
    assert not contains_forbidden_claim("В наблюдаемом creator universe есть свободные сегменты")
    assert not contains_forbidden_claim("Creator соответствует наблюдаемому профилю закупки бренда")


def test_sanitize_statement_falls_back_on_forbidden_claim():
    fallback = "fallback text"
    result = sanitize_statement("Конкурент точно сделает X", fallback)
    assert result == fallback


def test_sanitize_statement_keeps_safe_text():
    safe = "Это может указывать на смещение стратегии"
    assert sanitize_statement(safe, "fallback") == safe


def test_sanitize_statement_falls_back_on_empty():
    assert sanitize_statement(None, "fallback") == "fallback"
    assert sanitize_statement("", "fallback") == "fallback"


def test_enforce_confidence_wording_appends_caveat_for_confident_low_confidence_text():
    text = "Нужно делать X немедленно"
    result = enforce_confidence_wording(text, confidence=0.3, threshold=0.55)
    assert "стоит исследовать" in result.lower()


def test_enforce_confidence_wording_leaves_high_confidence_untouched():
    text = "Нужно делать X немедленно"
    result = enforce_confidence_wording(text, confidence=0.9, threshold=0.55)
    assert result == text
