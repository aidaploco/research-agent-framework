import pytest

from research_agent import retry
from research_agent.tools import wikipedia
from research_agent.tools.base import ToolError
from research_agent.tools.wikipedia import WikipediaTool
from tests.fakes import FakeHTTPResponse

MISSING = {"query": {"pages": {"-1": {"title": "Zzz", "missing": ""}}}}


FakeResponse = FakeHTTPResponse


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    sleeps = []
    monkeypatch.setattr(retry.time, "sleep", sleeps.append)
    return sleeps


def test_search_returns_ranked_sources_with_urls(monkeypatch):
    log = []
    fake = make_fake(
        MISSING["query"]["pages"]["-1"],
        [{"pageid": 1}, {"pageid": 2}],
        {
            "1": {"title": "Discount window", "extract": "A" * 10},
            "2": {"title": "Federal Reserve", "extract": "B" * 10},
        },
        log,
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
    sources = WikipediaTool().run("discount window")
    assert [s.title for s in sources] == ["Discount window", "Federal Reserve"]
    assert sources[0].url == "https://en.wikipedia.org/wiki/Discount_window"
    assert sources[0].tool == "wikipedia"
    assert len(log) == 4  # title lookup, search, then one extract request per hit


def test_every_top_hit_gets_its_text_in_rank_order_despite_the_one_page_quirk(monkeypatch):
    # Regression: the tool used to request all extracts in one call; the API returns just one page
    # for that, so most hits silently had no text (found when a reference question refused).
    log = []
    fake = make_fake(
        MISSING["query"]["pages"]["-1"],
        [{"pageid": 10}, {"pageid": 20}, {"pageid": 30}, {"pageid": 40}],
        {str(i): {"title": f"Article {i}", "extract": f"text {i}"} for i in (10, 20, 30, 40)},
        log,
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
    sources = WikipediaTool().run("a question")
    assert [s.title for s in sources] == ["Article 10", "Article 20", "Article 30"]  # the top three
    extract_requests = [p for p in log if "pageids" in p]
    assert [p["pageids"] for p in extract_requests] == [
        10,
        20,
        30,
    ]  # one page per request, in order


def test_a_third_ranked_article_is_found(monkeypatch):
    fake = make_fake(
        MISSING["query"]["pages"]["-1"],
        [{"pageid": 1}, {"pageid": 2}, {"pageid": 3}],
        {
            "1": {"title": "2008 financial crisis", "extract": "a"},
            "2": {"title": "Causes of the Great Recession", "extract": "b"},
            "3": {"title": "Federal Reserve responses to the subprime crisis", "extract": "c"},
        },
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
    titles = [
        s.title for s in WikipediaTool().run("Federal Reserve response to the financial crisis")
    ]
    assert "Federal Reserve responses to the subprime crisis" in titles


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
    fake = make_fake(
        MISSING["query"]["pages"]["-1"],
        [{"pageid": 1}],
        {"1": {"title": "T", "extract": "x" * 9000}},
    )
    monkeypatch.setattr(wikipedia.requests, "get", fake)
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


def make_fake(title_page, search_hits, pages, requests_log=None):
    """A fake MediaWiki API that behaves like the real one where it matters.

    For whole-article text the real API lowers `exlimit` to 1: asked for several pages at once it
    returns text for just one of them (and not the first). The fake reproduces that, so a tool
    that batches its extract requests gets the wrong pages here, as it did in production.
    """

    def fake_get(url, **kwargs):
        p = kwargs["params"]
        if requests_log is not None:
            requests_log.append(p)
        if "titles" in p:
            return FakeResponse({"query": {"pages": {"1": title_page}}})
        if p.get("list") == "search":
            return FakeResponse({"query": {"search": search_hits}})
        ids = str(p["pageids"]).split("|")
        keep = ids[-1:]  # the quirk: one page, and not the top-ranked one
        return FakeResponse({"query": {"pages": {i: pages[i] for i in keep if i in pages}}})

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
