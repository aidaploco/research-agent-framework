from research_agent.tools import wikipedia
from research_agent.tools.base import ToolError
from research_agent.tools.wikipedia import WikipediaTool

MISSING = {"query": {"pages": {"-1": {"title": "Zzz", "missing": ""}}}}


class FakeResponse:
    status_code = 200
    headers: dict = {}

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
        if "titles" in kwargs["params"]:
            return FakeResponse(MISSING)
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
    sources = WikipediaTool().run("discount window")
    assert [s.title for s in sources] == ["Discount window", "Federal Reserve"]
    assert sources[0].url == "https://en.wikipedia.org/wiki/Discount_window"
    assert sources[0].tool == "wikipedia"
    assert len(calls) == 3  # title lookup, search, extracts


def test_no_hits_returns_empty(monkeypatch):
    monkeypatch.setattr(
        wikipedia.requests,
        "get",
        lambda url, **k: FakeResponse(
            MISSING if "titles" in k["params"] else {"query": {"search": []}}
        ),
    )
    assert WikipediaTool().run("zzzz") == []


def test_top_article_gets_larger_budget_than_others(monkeypatch):
    fake = make_fake(
        {"title": "Top", "extract": "x" * 500},
        [{"pageid": 5}],
        {"5": {"title": "Other", "extract": "y" * 500}},
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
    top, other = WikipediaTool(max_chars=200, other_chars=50).run("Top")
    assert (len(top.text), len(other.text)) == (200, 50)


def test_text_is_truncated(monkeypatch):
    def fake_get(url, **kwargs):
        if "titles" in kwargs["params"]:
            return FakeResponse(MISSING)
        if kwargs["params"].get("list") == "search":
            return FakeResponse({"query": {"search": [{"pageid": 1}]}})
        return FakeResponse({"query": {"pages": {"1": {"title": "T", "extract": "x" * 9000}}}})

    monkeypatch.setattr(wikipedia.requests, "get", fake_get)
    assert len(WikipediaTool(max_chars=100, other_chars=100).run("t")[0].text) == 100


def test_network_failure_becomes_tool_error(monkeypatch):
    def boom(*a, **k):
        raise wikipedia.requests.ConnectionError("down")

    monkeypatch.setattr(wikipedia.requests, "get", boom)
    try:
        WikipediaTool().run("x")
    except ToolError as e:
        assert "wikipedia" in str(e)
    else:
        raise AssertionError("expected ToolError")


def make_fake(title_page, search_hits, pages):
    def fake_get(url, **kwargs):
        p = kwargs["params"]
        if "titles" in p:
            return FakeResponse({"query": {"pages": {"1": title_page}}})
        if p.get("list") == "search":
            return FakeResponse({"query": {"search": search_hits}})
        return FakeResponse({"query": {"pages": pages}})

    return fake_get


def test_exact_title_match_comes_first_and_is_not_duplicated(monkeypatch):
    fake = make_fake(
        {"title": "Basel III", "extract": "Basel III is a framework."},
        [{"pageid": 5}, {"pageid": 6}],
        {
            "5": {"title": "Basel Committee", "extract": "About the committee."},
            "6": {"title": "Basel III", "extract": "Basel III is a framework."},
        },
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
    sources = WikipediaTool().run("Basel III")
    assert [s.title for s in sources] == ["Basel III", "Basel Committee"]


def test_disambiguation_page_is_skipped(monkeypatch):
    fake = make_fake(
        {"title": "Mercury", "extract": "Mercury may refer to: the planet, the element."},
        [],
        {},
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
    assert WikipediaTool().run("Mercury") == []


def test_429_is_retried_with_backoff(monkeypatch):
    monkeypatch.setattr(wikipedia.time, "sleep", lambda s: None)
    statuses = iter([429, 200])

    class R:
        def __init__(self, code):
            self.status_code, self.headers = code, {}

        def raise_for_status(self):
            pass

        def json(self):
            return MISSING

    monkeypatch.setattr(wikipedia.requests, "get", lambda *a, **k: R(next(statuses)))
    assert WikipediaTool()._get({"action": "query"}) == MISSING


def test_429_forever_raises_tool_error(monkeypatch):
    monkeypatch.setattr(wikipedia.time, "sleep", lambda s: None)

    class R:
        status_code, headers = 429, {}

        def raise_for_status(self):
            raise wikipedia.requests.HTTPError("429")

    monkeypatch.setattr(wikipedia.requests, "get", lambda *a, **k: R())
    try:
        WikipediaTool().run("x")
    except ToolError:
        pass
    else:
        raise AssertionError("expected ToolError")
