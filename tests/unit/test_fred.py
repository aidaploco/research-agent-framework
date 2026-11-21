"""The FRED tool, on recorded-style responses: no network, and the API key never leaks."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from research_agent import retry
from research_agent.tools import fred
from research_agent.tools.base import ToolError
from research_agent.tools.fred import FredTool, build_text
from tests.fakes import FakeHTTPResponse

KEY = "SECRETKEY0123456789abcdef01234567"

UNRATE = {
    "id": "UNRATE",
    "title": "Unemployment Rate",
    "frequency": "Monthly",
    "units": "Percent",
    "seasonal_adjustment": "Seasonally Adjusted",
    "last_updated": "2026-11-06 09:43:15-06",
    "observation_end": "2026-10-01",
}


def obs(*pairs):
    return {"observations": [{"date": d, "value": v} for d, v in pairs]}


UNRATE_OBS = obs(
    ("2026-10-01", "4.1"), ("2026-09-01", "4.2"), ("2026-08-01", "."), ("2025-10-01", "4.4")
)


@pytest.fixture(autouse=True)
def fast_and_keyed(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", KEY)
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)


def serve(monkeypatch, routes):
    """Fake FRED: routes maps an API path to a response (or a list of them, used in turn)."""
    calls = []

    def get(url, **kw):
        path = url.removeprefix(fred.API + "/")
        calls.append((path, kw["params"]))
        reply = routes[path]
        if isinstance(reply, list):
            reply = reply.pop(0)
        return reply if isinstance(reply, FakeHTTPResponse) else FakeHTTPResponse(reply)

    monkeypatch.setattr(fred.requests, "get", get)
    return calls


def standard(monkeypatch, meta=UNRATE, observations=UNRATE_OBS):
    return serve(monkeypatch, {"series": {"seriess": [meta]}, "series/observations": observations})


# ---- what the tool returns -----------------------------------------------------------


def test_returns_one_source_with_the_series_page_as_its_url(monkeypatch):
    standard(monkeypatch)
    (src,) = FredTool().run("unrate")  # case does not matter
    assert src.tool == "fred" and src.native_id == "UNRATE"
    assert src.title == "FRED: Unemployment Rate (UNRATE)"
    assert (
        src.url == "https://fred.stlouisfed.org/series/UNRATE"
    )  # the public page, not the API URL
    assert src.meta["published"] == "2026-10-01"


def test_text_states_the_latest_value_the_year_earlier_and_a_computed_change(monkeypatch):
    standard(monkeypatch)
    text = FredTool().run("UNRATE")[0].text
    assert "Unemployment Rate on 2026-10-01: 4.1 (Percent)" in text
    assert "About 1 year earlier, Unemployment Rate on 2025-10-01: 4.4 (Percent)" in text
    assert (
        "Change over the past 1 year in Unemployment Rate: -0.3 percentage points, from 4.4 on 2025-10-01 to 4.1 on 2026-10-01."
        in text
    )


def test_missing_values_marked_with_a_dot_are_skipped(monkeypatch):
    standard(monkeypatch)
    text = FredTool().run("UNRATE")[0].text
    assert "2026-08-01" not in text  # FRED wrote "." for it


def test_the_request_asks_for_the_window_and_sends_the_key_only_as_a_parameter(monkeypatch):
    calls = standard(monkeypatch)
    FredTool().run("UNRATE", years=2)
    path, params = calls[-1]
    assert path == "series/observations" and params["series_id"] == "UNRATE"
    assert params["observation_start"] < "2024-10-01" and params["sort_order"] == "desc"
    assert params["api_key"] == KEY and params["file_type"] == "json"


def test_change_for_an_index_series_includes_a_percentage():
    meta = {**UNRATE, "id": "CPI", "title": "CPI", "units": "Index 1982-1984=100"}
    rows = [(date(2026, 10, 1), Decimal("330")), (date(2025, 10, 1), Decimal("300"))]
    text = build_text(meta, rows, years=1)
    assert "Change over the past 1 year in CPI: +30 Index 1982-1984=100 (+10%)" in text


def test_daily_series_show_five_recent_observations_not_a_year_of_them():
    meta = {**UNRATE, "frequency": "Daily"}
    rows = [(date(2026, 11, 20) - timedelta(days=i), Decimal("0.3")) for i in range(300)]
    text = build_text(meta, rows, years=1)
    assert text.count("Unemployment Rate on ") >= 5
    recent = text.split("Recent observations:")[1]
    assert recent.count("\n") == 5  # five lines


def test_no_observation_near_a_year_earlier_says_so_instead_of_inventing_a_change():
    rows = [(date(2026, 10, 1), Decimal("4.1")), (date(2026, 9, 1), Decimal("4.2"))]
    text = build_text(UNRATE, rows, years=1)
    assert "No observation near 1 year before the latest was found, so no change is stated." in text
    assert "Change over" not in text


def test_quarterly_data_matches_the_nearest_quarter_within_tolerance():
    meta = {**UNRATE, "frequency": "Quarterly", "units": "Billions of Dollars"}
    rows = [(date(2026, 7, 1), Decimal("33000")), (date(2025, 7, 1), Decimal("30000"))]
    assert "+3000 Billions of Dollars (+10%)" in build_text(meta, rows, years=1)


# ---- finding a series ----------------------------------------------------------------


def test_keywords_are_searched_and_the_top_result_is_used_with_the_other_candidates_named(
    monkeypatch,
):
    calls = serve(
        monkeypatch,
        {
            "series/search": {
                "seriess": [{**UNRATE, "id": "CPIAUCSL", "title": "CPI"}, {"id": "A"}, {"id": "B"}]
            },
            "series/observations": UNRATE_OBS,
        },
    )
    (src,) = FredTool().run("consumer price index")
    assert src.native_id == "CPIAUCSL"
    assert (
        "Selected by keyword search for 'consumer price index'. Other candidates: A, B." in src.text
    )
    assert [c[0] for c in calls] == ["series/search", "series/observations"]


def test_an_unknown_series_id_falls_back_to_search(monkeypatch):
    gone = FakeHTTPResponse(
        {"error_message": "Bad Request.  The series does not exist."}, status=400
    )
    serve(
        monkeypatch,
        {
            "series": [gone],
            "series/search": {"seriess": [{**UNRATE, "id": "GDP", "title": "GDP"}]},
            "series/observations": UNRATE_OBS,
        },
    )
    assert FredTool().run("gdp")[0].native_id == "GDP"


def test_a_search_with_no_results_is_a_clear_error(monkeypatch):
    serve(monkeypatch, {"series/search": {"seriess": []}})
    with pytest.raises(ToolError, match="no series found"):
        FredTool().run("zzz qqq")


# ---- no key, and the key never leaks ------------------------------------------------


def test_without_a_key_it_fails_at_once_without_any_network_call(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY")
    calls = serve(monkeypatch, {})
    with pytest.raises(
        ToolError, match=r"FRED_API_KEY is not set, so live economic data cannot be retrieved"
    ):
        FredTool().run("UNRATE")
    assert calls == []


def test_a_blank_key_counts_as_missing(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "   ")
    with pytest.raises(ToolError, match="not set"):
        FredTool().run("UNRATE")


def test_the_key_is_not_in_an_error_built_from_a_connection_failure(monkeypatch):
    def boom(url, **kw):  # a real requests error message contains the URL, key included
        raise fred.requests.ConnectionError(f"Max retries exceeded with url: {url}?api_key={KEY}")

    monkeypatch.setattr(fred.requests, "get", boom)
    with pytest.raises(ToolError) as exc:
        FredTool().run("UNRATE")
    assert KEY not in str(exc.value) and "ConnectionError" in str(exc.value)
    assert "unavailable after 3 attempts" in str(exc.value)


def test_the_key_is_not_in_an_http_error(monkeypatch):
    bad = FakeHTTPResponse(
        {"error_message": "Bad Request.  The value for variable api_key is not registered."},
        status=400,
    )
    serve(monkeypatch, {"series": [bad]})
    with pytest.raises(ToolError) as exc:
        FredTool().run("UNRATE")
    assert (
        KEY not in str(exc.value)
        and "not registered" in str(exc.value)
        and "HTTP 400" in str(exc.value)
    )


def test_rate_limits_and_server_errors_are_retried(monkeypatch):
    serve(monkeypatch, {"series": [FakeHTTPResponse(status=429), FakeHTTPResponse(status=503), {"seriess": [UNRATE]}],
                        "series/observations": UNRATE_OBS})  # fmt: skip
    assert FredTool().run("UNRATE")[0].native_id == "UNRATE"


def test_the_key_never_reaches_the_trace_or_the_notice(fake_llm, trace_dir, monkeypatch):
    from research_agent import agent
    from research_agent.tools.registry import Registry
    from research_agent.tracing import TraceStore
    from tests.fakes import answer_json, plan_json

    def boom(url, **kw):
        raise fred.requests.ConnectionError(f"url={url}?api_key={KEY}")

    monkeypatch.setattr(fred.requests, "get", boom)
    fake_llm.queue(plan_json("fred", "UNRATE"), answer_json(refusal="No live data."))
    out = agent.answer("q", Registry([FredTool()]), TraceStore(trace_dir), max_steps=1)
    blob = out + "".join(f.read_text() for f in trace_dir.glob("*.json"))
    assert KEY not in blob


def test_fred_is_marked_as_a_live_data_tool():
    assert FredTool.live_data is True
