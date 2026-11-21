from research_agent.tools import wikipedia


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_search_returns_ranked_sources_with_urls(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs["params"])
        if kwargs["params"].get("list") == "search":
            return FakeResponse({"query": {"search": [{"pageid": 1}, {"pageid": 2}]}})
        return FakeResponse(
            {
                "query": {
                    "pages": {
                        "1": {"title": "Discount window", "extract": "A" * 10},
                        "2": {"title": "Federal Reserve", "extract": "B" * 10},
                    }
                }
            }
        )

    monkeypatch.setattr(wikipedia.requests, "get", fake_get)
    sources = wikipedia.search("discount window")
    assert [s["title"] for s in sources] == ["Discount window", "Federal Reserve"]
    assert sources[0]["url"] == "https://en.wikipedia.org/wiki/Discount_window"
    assert len(calls) == 2


def test_search_no_hits_returns_empty(monkeypatch):
    monkeypatch.setattr(
        wikipedia.requests, "get", lambda *a, **k: FakeResponse({"query": {"search": []}})
    )
    assert wikipedia.search("zzzz") == []


def test_search_truncates_text(monkeypatch):
    def fake_get(url, **kwargs):
        if kwargs["params"].get("list") == "search":
            return FakeResponse({"query": {"search": [{"pageid": 1}]}})
        return FakeResponse({"query": {"pages": {"1": {"title": "T", "extract": "x" * 9000}}}})

    monkeypatch.setattr(wikipedia.requests, "get", fake_get)
    assert len(wikipedia.search("t", max_chars=100)[0]["text"]) == 100
