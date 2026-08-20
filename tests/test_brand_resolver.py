from __future__ import annotations

import pytest

from app.analysis.brand_resolver import resolve_brand


def test_bare_name_resolves_without_platform_or_config():
    result = resolve_brand("Автор24")
    assert result.brand_name == "Автор24"
    assert result.canonical_name == "Автор24"
    assert result.input_type == "name"
    assert result.detected_platform is None
    assert result.source_url is None
    assert result.aliases == []


@pytest.mark.parametrize("url,expected_platform,expected_handle", [
    ("https://www.youtube.com/@Avtor24Official", "youtube", "Avtor24Official"),
    ("https://youtube.com/channel/UC1234567890", "youtube", "UC1234567890"),
    ("https://www.youtube.com/c/Avtor24", "youtube", "Avtor24"),
    ("https://www.youtube.com/user/avtor24", "youtube", "avtor24"),
    ("https://www.instagram.com/avtor24/", "instagram", "avtor24"),
    ("https://www.tiktok.com/@avtor24", "tiktok", "avtor24"),
])
def test_url_input_detects_platform_and_handle(url, expected_platform, expected_handle):
    result = resolve_brand(url)
    assert result.input_type == "url"
    assert result.source_url == url
    assert result.detected_platform == expected_platform
    assert result.normalized_handle == expected_handle
    assert result.brand_name == expected_handle


def test_instagram_url_ignores_reserved_path_segments():
    result = resolve_brand("https://www.instagram.com/p/Cabc123/")
    # "/p/..." - это пост, не хэндл аккаунта - handle не должен быть "p".
    assert result.normalized_handle != "p"


def test_unknown_host_falls_back_to_hostname_as_brand_name():
    result = resolve_brand("https://competitor-blog.example.com/some/path")
    assert result.detected_platform is None
    assert result.normalized_handle is None
    assert result.brand_name == "competitor-blog.example.com"


def test_empty_input_raises_value_error():
    with pytest.raises(ValueError):
        resolve_brand("")
    with pytest.raises(ValueError):
        resolve_brand("   ")


def test_no_hardcoded_brand_configs_any_name_works():
    """Раздел 1 требований: никаких захардкоженных конфигов конкурентов -
    произвольное имя должно резолвиться одинаково честно."""
    for name in ["Совершенно Новый Бренд 12345", "XYZ Corp", "рандомная студия"]:
        result = resolve_brand(name)
        assert result.brand_name == name
        assert result.input_type == "name"
