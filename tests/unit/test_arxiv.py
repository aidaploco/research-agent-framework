from pathlib import Path

import pytest

from research_agent.tools import arxiv
from research_agent.tools.base import ToolError
from tests.fakes import FakeHTTPResponse

FIXTURE = Path(__file__).parent.parent / "fixtures" / "arxiv_sample.xml"


def test_build_query_ands_keywords_and_strips_punctuation():
    assert arxiv.build_query("credit-risk, machine learning!") == (
        "all:credit-risk AND all:machine AND all:learning"
    )


def test_build_query_rejects_empty():
    with pytest.raises(ToolError):
        arxiv.build_query("?!")


def test_parse_feed_extracts_ids_authors_dates_and_collapses_whitespace():
    sources = arxiv.parse_feed(FIXTURE.read_text())
    assert [s.native_id for s in sources] == ["arXiv:2501.01234v2", "arXiv:2410.99999v1"]
    assert sources[0].title == "Deep Learning for Credit Risk Assessment"
    assert sources[0].url == "https://arxiv.org/abs/2501.01234v2"
    assert sources[0].meta == {"authors": ["Ada Example", "Bo Sample"], "published": "2025-01-05"}
    assert sources[0].text == "We study neural networks for default prediction."
    assert "arXiv:2501.01234v2" in sources[0].citation()


def test_parse_feed_rejects_non_xml():
    with pytest.raises(ToolError, match="unparseable"):
        arxiv.parse_feed("Rate exceeded.")


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    """No real waiting; record the backoff sleeps."""
    sleeps = []
    monkeypatch.setattr(arxiv, "_space_requests", lambda: None)
    monkeypatch.setattr(arxiv.time, "sleep", sleeps.append)
    return sleeps


def feed():
    return FakeHTTPResponse(text=FIXTURE.read_text())


def test_retries_when_rate_limited_then_succeeds(monkeypatch, fast):
    replies = iter([FakeHTTPResponse(text="Rate exceeded."), feed()])
    monkeypatch.setattr(arxiv.requests, "get", lambda *a, **k: next(replies))
    assert len(arxiv.ArxivTool().run("credit risk")) == 2
    assert fast == [2.0]  # one short backoff


def test_gives_up_after_three_attempts_with_short_backoff(monkeypatch, fast):
    calls = []

    def always_limited(*a, **k):
        calls.append(k["timeout"])
        return FakeHTTPResponse(text="Rate exceeded.")

    monkeypatch.setattr(arxiv.requests, "get", always_limited)
    with pytest.raises(ToolError, match=r"unavailable after 3 attempts \(rate limited\)"):
        arxiv.ArxivTool().run("credit risk")
    assert calls == [10.0, 10.0, 10.0]  # short per-request timeout
    assert fast == [2.0, 4.0]  # worst case: 3 x 10s timeouts + 6s backoff


def test_timeouts_are_retried_and_reported_by_name(monkeypatch, fast):
    def timeout(*a, **k):
        raise arxiv.requests.ReadTimeout("slow")

    monkeypatch.setattr(arxiv.requests, "get", timeout)
    with pytest.raises(ToolError, match=r"\(ReadTimeout\)"):
        arxiv.ArxivTool().run("credit risk")
    assert fast == [2.0, 4.0]


def test_http_error_status_is_reported(monkeypatch):
    monkeypatch.setattr(
        arxiv.requests, "get", lambda *a, **k: FakeHTTPResponse(text="x", status=503)
    )
    with pytest.raises(ToolError, match=r"\(HTTP 503\)"):
        arxiv.ArxivTool(attempts=1).run("credit risk")


def test_recent_option_sorts_by_date(monkeypatch):
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs["params"])
        return feed()

    monkeypatch.setattr(arxiv.requests, "get", fake_get)
    arxiv.ArxivTool().run("credit risk", recent=True)
    assert seen["sortBy"] == "submittedDate"
    arxiv.ArxivTool().run("credit risk")
    assert seen["sortBy"] == "relevance"
