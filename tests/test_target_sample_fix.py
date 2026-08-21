from types import SimpleNamespace

from app.creator_universe import expand_creator_universe_web


class FakeSearch:
    def is_available(self):
        return True

    def search(self, query, max_results=10):
        return [SimpleNamespace(url=f"https://www.youtube.com/watch?v=v{i}") for i in range(8)]


class FakeYT:
    def is_available(self):
        return True

    def _run_with_retries(self, fn, value):
        return fn(value)

    def get_video_stats(self, video_id):
        i = video_id.removeprefix("v")
        return {"snippet": {"channelId": f"c{i}", "channelTitle": f"Creator {i}"}}

    def get_channel_stats(self, channel_id):
        i = channel_id.removeprefix("c")
        return {
            "id": channel_id,
            "snippet": {"title": f"Creator {i}"},
            "statistics": {"subscriberCount": "1000"},
        }


def test_web_universe_expansion_targets_real_channels(monkeypatch):
    monkeypatch.setattr("app.creator_universe.get_default_search_client", lambda settings: FakeSearch())
    creators, queries, notes = expand_creator_universe_web(
        [], observed_topics=["sports", "fitness", "fashion"], target=15, adapter=FakeYT()
    )
    # Fake search repeats the same 8 videos across queries, so dedup must keep
    # only 8 real channels instead of fabricating rows to hit the target.
    assert len(creators) == 8
    assert all(c.name.startswith("Creator ") for c in creators)
    assert all(c.canonical_url.startswith("https://www.youtube.com/channel/") for c in creators)
    assert queries
    assert notes == []
