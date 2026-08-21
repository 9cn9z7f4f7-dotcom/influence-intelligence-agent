from pathlib import Path
from types import SimpleNamespace

from app.connectors.registry import ConnectorRegistry
from app.platforms.social_connector_base import SocialConnectorPlatformAdapter
from config.settings import Settings
from local_connector import social_auth


def test_social_connector_default_wait_is_long_enough_for_real_job_barrier(monkeypatch):
    monkeypatch.setenv("CONNECTOR_JOB_WAIT_SECONDS", "120")
    settings = Settings()
    adapter = SocialConnectorPlatformAdapter(connector_registry=ConnectorRegistry(settings=settings), settings=settings)
    assert adapter.wait_seconds == 120


def test_saved_instagram_session_is_reused_without_manual_login(tmp_path, monkeypatch):
    state = tmp_path / "instagram_state.json"
    state.write_text("{}")

    class FakePage:
        url = "https://www.instagram.com/"
        def goto(self, *args, **kwargs): self.url = "https://www.instagram.com/"
        def wait_for_timeout(self, *_): pass
        def query_selector(self, *_): return None

    class FakeContext:
        def __init__(self): self.page = FakePage()
        def new_page(self): return self.page

    class FakeBrowser:
        def close(self): pass

    class FakeChromium:
        def launch(self, headless=True): return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    ctx = FakeContext()
    # browser.new_context lives on browser in real Playwright; attach for test.
    browser = FakeBrowser()
    browser.new_context = lambda **kwargs: ctx
    FakeChromium.launch = lambda self, headless=True: browser

    monkeypatch.setattr("builtins.input", lambda *_: (_ for _ in ()).throw(AssertionError("manual login should not be requested")))
    b, c, p = social_auth.ensure_authenticated_context("instagram", state, FakePlaywright())
    assert p.url == "https://www.instagram.com/"


def test_invalid_saved_instagram_session_refreshes_auth_once(tmp_path, monkeypatch):
    state = tmp_path / "instagram_state.json"
    state.write_text("{}")
    calls = {"interactive": 0}

    class FakePage:
        url = "https://www.instagram.com/accounts/login/"
        def goto(self, *args, **kwargs): self.url = "https://www.instagram.com/accounts/login/"
        def wait_for_timeout(self, *_): pass
        def query_selector(self, selector): return object() if "username" in selector else None

    class FakeContext:
        def new_page(self): return FakePage()

    class FakeBrowser:
        def new_context(self, **kwargs): return FakeContext()
        def close(self): pass

    class FakeChromium:
        def launch(self, headless=True): return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    sentinel = (object(), object(), object())
    def fake_interactive(*args, **kwargs):
        calls["interactive"] += 1
        return sentinel

    monkeypatch.setattr(social_auth, "_interactive_login", fake_interactive)
    result = social_auth.ensure_authenticated_context("instagram", state, FakePlaywright())
    assert result == sentinel
    assert calls["interactive"] == 1
