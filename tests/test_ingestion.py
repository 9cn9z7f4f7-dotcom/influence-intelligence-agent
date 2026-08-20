from __future__ import annotations

from app.health import health_registry
from app.ingestion.demo_loader import DemoLoader
from app.ingestion.optional_adapters import InstagramAdapter, TelegramAdapter
from app.ingestion.web_adapter import WebAdapter
from app.ingestion.youtube_adapter import YouTubeAdapter
from app.models import SourceStatus


def test_youtube_adapter_unavailable_without_key():
    health_registry.reset()
    adapter = YouTubeAdapter(api_key="")
    assert not adapter.is_available()
    result = adapter.fetch(query="test")
    assert result.creators == []
    snapshot = health_registry.snapshot()
    assert any(h["source"] == "youtube" and h["status"] == SourceStatus.UNAVAILABLE.value for h in snapshot)


def test_web_adapter_degraded_on_bad_url():
    health_registry.reset()
    adapter = WebAdapter(timeout=2.0)
    # Заведомо недостижимый хост - адаптер обязан не бросить исключение наружу.
    result = adapter.fetch(url="http://127.0.0.1:1/definitely-not-a-real-host-xyz")
    assert isinstance(result.notes, list)
    snapshot = health_registry.snapshot()
    assert any(h["source"] == "web" for h in snapshot)


def test_demo_loader_marks_synthetic():
    loader = DemoLoader()
    if not loader.is_available():
        return
    result = loader.fetch()
    assert result.creators, "demo dataset должен быть непустым"
    assert all(c.is_synthetic for c in result.creators)
    assert all(i.is_synthetic for i in result.integrations)


def test_optional_adapters_never_crash():
    tg = TelegramAdapter()
    ig = InstagramAdapter()
    assert tg.fetch().creators == []
    assert ig.fetch().creators == []
