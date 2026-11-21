from pathlib import Path

import pytest

from research_agent.tools import arxiv
from research_agent.tools.base import ToolError

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
    assert [s.id for s in sources] == ["arXiv:2501.01234v2", "arXiv:2410.99999v1"]
    assert sources[0].title == "Deep Learning for Credit Risk Assessment"
    assert sources[0].url == "https://arxiv.org/abs/2501.01234v2"
    assert sources[0].meta == {"authors": ["Ada Example", "Bo Sample"], "published": "2025-01-05"}
    assert sources[0].text == "We study neural networks for default prediction."
    assert "arXiv:2501.01234v2" in sources[0].citation()


def test_parse_feed_rejects_non_xml():
    with pytest.raises(ToolError, match="unparseable"):
        arxiv.parse_feed("Rate exceeded.")


class Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text


def no_sleep(monkeypatch):
    monkeypatch.setattr(arxiv.time, "sleep", lambda s: None)


def test_retries_when_rate_limited_then_succeeds(monkeypatch):
    no_sleep(monkeypatch)
    replies = iter([Resp(200, "Rate exceeded."), Resp(200, FIXTURE.read_text())])
    monkeypatch.setattr(arxiv.requests, "get", lambda *a, **k: next(replies))
    assert len(arxiv.ArxivTool().run("credit risk")) == 2


def test_gives_up_after_retries(monkeypatch):
    no_sleep(monkeypatch)
    monkeypatch.setattr(arxiv.requests, "get", lambda *a, **k: Resp(200, "Rate exceeded."))
    with pytest.raises(ToolError, match="rate limited"):
        arxiv.ArxivTool(retries=2).run("credit risk")


def test_recent_option_sorts_by_date(monkeypatch):
    no_sleep(monkeypatch)
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs["params"])
        return Resp(200, FIXTURE.read_text())

    monkeypatch.setattr(arxiv.requests, "get", fake_get)
    arxiv.ArxivTool().run("credit risk", recent=True)
    assert seen["sortBy"] == "submittedDate"
    arxiv.ArxivTool().run("credit risk")
    assert seen["sortBy"] == "relevance"
