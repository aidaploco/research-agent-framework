import pytest

from research_agent.tools import wikipedia
from research_agent.tools.base import ToolError
from research_agent.tools.wikipedia import WikipediaTool
from tests.fakes import FakeHTTPResponse

MISSING = {"query": {"pages": {"-1": {"title": "Zzz", "missing": ""}}}}


FakeResponse = FakeHTTPResponse


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(wikipedia.time, "sleep", sleeps.append)
    return sleeps


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


def test_429_is_retried_with_short_backoff(monkeypatch, fast):
    replies = iter([FakeHTTPResponse(status=429), FakeHTTPResponse(MISSING)])
    monkeypatch.setattr(wikipedia.requests, "get", lambda *a, **k: next(replies))
    assert WikipediaTool()._get({"action": "query"}) == MISSING
    assert fast == [1.0]


def test_gives_up_after_three_attempts_and_names_the_reason(monkeypatch, fast):
    timeouts = []

    def limited(*a, **k):
        timeouts.append(k["timeout"])
        return FakeHTTPResponse(status=429)

    monkeypatch.setattr(wikipedia.requests, "get", limited)
    with pytest.raises(ToolError, match=r"unavailable after 3 attempts \(rate limited\)"):
        WikipediaTool().run("x")
    assert timeouts == [10.0] * 3 and fast == [1.0, 2.0]


def test_timeouts_and_connection_errors_are_retried(monkeypatch, fast):
    errors = iter([wikipedia.requests.ReadTimeout("slow"), wikipedia.requests.ConnectionError("x")])
    replies = {"n": 0}

    def flaky(*a, **k):
        replies["n"] += 1
        try:
            raise next(errors)
        except StopIteration:
            return FakeHTTPResponse(MISSING)

    monkeypatch.setattr(wikipedia.requests, "get", flaky)
    assert WikipediaTool()._get({}) == MISSING and replies["n"] == 3
