"""FRED tool: current and historical US economic statistics from the St. Louis Fed.

Needs a free API key (FRED_API_KEY). Without one the tool fails immediately, with no network
call, and says so; the agent then reports that live data could not be retrieved and never fills
the gap from the model's memory.

The tool returns one source per call: the series' title, units, frequency, the latest
observations, the value a year earlier, and the change over the period. The change is computed
here, not by the model, so the grounding check can verify it against the source text.

The API key travels in the request URL, and a requests exception message contains the URL. So
no error message here is ever built from an exception's text: reasons are exception type names
or FRED's own `error_message`, and nothing logs a request URL.
"""

import os
import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import requests
from pydantic import BaseModel, ConfigDict, Field

from ..retry import RetriesExhausted, Retry, with_retries
from .base import Source, Tool, ToolError

API = "https://api.stlouisfed.org/fred"
HEADERS = {"User-Agent": "research-agent-framework/0.1 (educational project)"}
TIMEOUT = 10.0
BACKOFF = (1.0, 2.0)
KEY_HELP = "https://fred.stlouisfed.org/docs/api/api_key.html"
YEAR_AGO_TOLERANCE = timedelta(days=70)  # quarterly data has no observation on the exact day
SERIES_ID = re.compile(r"[A-Za-z0-9_]{2,25}")


class FredOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    years: int = Field(
        1, ge=1, le=10, description="years back to compare the latest value with (default 1)"
    )


class _NoSuchSeries(Exception):
    pass


def _dec(text: str) -> Decimal | None:
    """An observation value. FRED writes '.' for a missing value."""
    try:
        return Decimal(text)
    except Exception:
        return None


def _fmt(d: Decimal) -> str:
    return format(d.normalize(), "f")


def _years_before(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 February
        return d.replace(year=d.year - years, day=28)


def build_text(meta: dict, observations: list[tuple[date, Decimal]], years: int) -> str:
    """The source text: a header, the latest value, the value a year earlier, the change.

    `observations` are valid (date, value) pairs, newest first.
    """
    units = meta.get("units", "")
    freq = meta.get("frequency", "")
    title, sid = meta.get("title", ""), meta.get("id", "")
    adj = meta.get("seasonal_adjustment", "")
    updated = (meta.get("last_updated") or "")[:10]
    latest_date, latest = observations[0]
    lines = [
        f"Series {sid}: {title}. Frequency: {freq}. Units: {units}. {adj}. Last updated {updated}.",
        f"Latest observation, {title} on {latest_date.isoformat()}: {_fmt(latest)} ({units}).",
    ]
    target = _years_before(latest_date, years)
    nearest = min(observations, key=lambda o: abs(o[0] - target))
    span = f"{years} year" + ("s" if years != 1 else "")
    if abs(nearest[0] - target) <= YEAR_AGO_TOLERANCE and nearest[0] != latest_date:
        then_date, then = nearest
        delta = (latest - then).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if units.lower().startswith("percent"):
            change = f"{'+' if delta > 0 else ''}{_fmt(delta)} percentage points"
        elif then != 0:
            pct = ((latest / then - 1) * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            change = f"{'+' if delta > 0 else ''}{_fmt(delta)} {units} ({'+' if pct > 0 else ''}{_fmt(pct)}%)"
        else:
            change = f"{'+' if delta > 0 else ''}{_fmt(delta)} {units}"
        lines += [
            f"About {span} earlier, {title} on {then_date.isoformat()}: {_fmt(then)} ({units}).",
            f"Change over the past {span} in {title}: {change}, from {_fmt(then)} on {then_date.isoformat()} "
            f"to {_fmt(latest)} on {latest_date.isoformat()}.",
        ]
    else:
        lines.append(
            f"No observation near {span} before the latest was found, so no change is stated."
        )
    shown = observations[:5] if freq.lower().startswith(("daily", "weekly")) else observations[:14]
    lines.append("Recent observations:")
    lines += [f"{title} on {d.isoformat()}: {_fmt(v)}" for d, v in shown]
    return "\n".join(lines)


class FredTool(Tool):
    name = "fred"
    id_prefix = "F"
    live_data = True
    description = (
        "FRED (Federal Reserve Economic Data): CURRENT and historical US economic statistics, with "
        "the latest value, the value a year earlier and the change. Use it for questions about the "
        "current or recent level of a statistic. Query: a FRED series ID, or keywords to search "
        "for one. Common IDs: UNRATE unemployment rate; FEDFUNDS federal funds rate; DPCREDIT "
        "discount window primary credit rate; DGS10 and DGS2 10- and 2-year Treasury yields; "
        "T10Y2Y and T10Y3M yield curve spreads (negative means inverted); CPIAUCSL consumer price "
        "index; GDP nominal and GDPC1 real GDP; PAYEMS nonfarm payrolls; MORTGAGE30US 30-year "
        "mortgage rate; USREC recession indicator. Needs a free API key."
    )
    options_model = FredOptions

    def run(self, query: str, **options) -> list[Source]:
        key = os.environ.get("FRED_API_KEY", "").strip()
        if not key:
            raise ToolError(
                f"FRED_API_KEY is not set, so live economic data cannot be retrieved "
                f"(a free key: {KEY_HELP})"
            )
        years = FredOptions.model_validate(options).years
        query = query.strip()
        via_search: list[str] = []
        sid = query.upper()
        if SERIES_ID.fullmatch(query):
            try:
                meta = self._series(sid, key)
            except _NoSuchSeries:
                sid, meta, via_search = self._search(query, key)
        else:
            sid, meta, via_search = self._search(query, key)
        observations = self._observations(sid, meta, years, key)
        if not observations:
            raise ToolError(f"fred: series {sid} has no observations")
        text = build_text(meta, observations, years)
        if via_search:
            text += f"\nSelected by keyword search for {query!r}. Other candidates: {', '.join(via_search)}."
        return [
            Source(
                tool=self.name,
                title=f"FRED: {meta.get('title', sid)} ({sid})",
                url=f"https://fred.stlouisfed.org/series/{sid}",
                text=text,
                native_id=sid,
                meta={
                    "published": observations[0][0].isoformat(),  # the latest observation's date
                    "frequency": meta.get("frequency", ""),
                    "units": meta.get("units", ""),
                },
            )
        ]

    # ---- API calls -------------------------------------------------------------------

    def _get(self, path: str, params: dict, key: str) -> dict:
        def attempt(n: int) -> dict:
            try:
                r = requests.get(
                    f"{API}/{path}",
                    headers=HEADERS,
                    timeout=TIMEOUT,
                    params={**params, "api_key": key, "file_type": "json"},
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                raise Retry(type(e).__name__) from e  # never str(e): it contains the URL and key
            if r.status_code == 429 or r.status_code >= 500:
                raise Retry(f"HTTP {r.status_code}")
            if r.status_code >= 400:
                try:
                    message = r.json().get("error_message", "")
                except ValueError:
                    message = ""
                if "does not exist" in message:
                    raise _NoSuchSeries(message)
                raise ToolError(
                    f"fred rejected the request (HTTP {r.status_code}): {message or 'no details'}"
                )
            return r.json()

        try:
            return with_retries("fred", attempt, attempts=3, backoff=BACKOFF)
        except RetriesExhausted as e:
            raise ToolError(f"fred unavailable after {e.attempts} attempts ({e.reason})") from e

    def _series(self, sid: str, key: str) -> dict:
        return self._get("series", {"series_id": sid}, key)["seriess"][0]

    def _search(self, text: str, key: str) -> tuple[str, dict, list[str]]:
        found = self._get(
            "series/search",
            {"search_text": text, "limit": 5, "order_by": "popularity", "sort_order": "desc"},
            key,
        ).get("seriess", [])
        if not found:
            raise ToolError(f"fred: no series found for {text!r}")
        return found[0]["id"], found[0], [s["id"] for s in found[1:]]

    def _observations(
        self, sid: str, meta: dict, years: int, key: str
    ) -> list[tuple[date, Decimal]]:
        end = meta.get("observation_end") or date.today().isoformat()
        start = _years_before(date.fromisoformat(end), years) - timedelta(
            days=YEAR_AGO_TOLERANCE.days + 10
        )
        rows = self._get(
            "series/observations",
            {
                "series_id": sid,
                "observation_start": start.isoformat(),
                "sort_order": "desc",
                "limit": 1000,
            },
            key,
        ).get("observations", [])
        out = []
        for row in rows:
            value = _dec(row.get("value", "."))
            if value is not None:  # skip FRED's "." (missing)
                out.append((date.fromisoformat(row["date"]), value))
        return out
