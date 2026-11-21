"""arXiv tool: search paper abstracts and metadata via the arXiv Atom API.

arXiv rate-limits aggressively (it answers "Rate exceeded." with a 200 status), so
requests are spaced and retried with backoff.
"""

import re
import time
import xml.etree.ElementTree as ET

import requests
from pydantic import BaseModel, ConfigDict, Field

from .base import Source, Tool, ToolError

API = "https://export.arxiv.org/api/query"
HEADERS = {"User-Agent": "research-agent-framework/0.1 (educational project)"}
NS = {"a": "http://www.w3.org/2005/Atom"}
MIN_INTERVAL = 3.0  # arXiv asks for at most one request per 3 seconds
TIMEOUT = 10.0  # seconds per request
BACKOFF = (2.0, 4.0)  # seconds to wait before attempt 2 and attempt 3

_last_request = 0.0


def _space_requests() -> None:
    """Wait so requests are at least MIN_INTERVAL apart."""
    wait = MIN_INTERVAL - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)


def _mark_request() -> None:
    global _last_request
    _last_request = time.monotonic()


def build_query(text: str) -> str:
    """Turn free keywords into an arXiv query: every word must appear (AND)."""
    words = re.findall(r"[A-Za-z0-9\-]+", text)
    if not words:
        raise ToolError("arxiv: empty query")
    return " AND ".join(f"all:{w}" for w in words)


def parse_feed(xml_text: str, max_chars: int = 1200) -> list[Source]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ToolError(f"arxiv: unparseable response ({xml_text[:60]!r})") from e
    sources = []
    for e in root.findall("a:entry", NS):
        abs_url = (e.findtext("a:id", "", NS) or "").strip()
        arxiv_id = abs_url.rsplit("/abs/", 1)[-1]
        title = " ".join((e.findtext("a:title", "", NS) or "").split())
        summary = " ".join((e.findtext("a:summary", "", NS) or "").split())
        authors = [
            " ".join((a.findtext("a:name", "", NS) or "").split())
            for a in e.findall("a:author", NS)
        ]
        sources.append(
            Source(
                tool="arxiv",
                title=title,
                url=abs_url.replace("http://", "https://"),
                text=summary[:max_chars],
                native_id=f"arXiv:{arxiv_id}",
                meta={
                    "authors": authors,
                    "published": (e.findtext("a:published", "", NS) or "")[:10],
                },
            )
        )
    return sources


class ArxivOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recent: bool = Field(False, description="true to sort newest first instead of by relevance")


class ArxivTool(Tool):
    name = "arxiv"
    id_prefix = "A"
    description = (
        "Academic papers (abstracts, authors, dates). Best for research literature and "
        "'recent papers' questions. Query: 3-6 topical keywords, no filler words."
    )
    options_model = ArxivOptions

    def __init__(self, max_results: int = 5, attempts: int = 3, timeout: float = TIMEOUT):
        self.max_results = max_results
        self.attempts = attempts
        self.timeout = timeout

    def run(self, query: str, **options) -> list[Source]:
        params = {
            "search_query": build_query(query),
            "max_results": self.max_results,
            "sortBy": "submittedDate" if options.get("recent") else "relevance",
            "sortOrder": "descending",
        }
        return parse_feed(self._get(params))

    def _get(self, params: dict) -> str:
        """Fetch with a short timeout and a short backoff. Worst case is about 40 seconds."""
        reason = "no attempt made"
        for attempt in range(self.attempts):
            _space_requests()
            try:
                r = requests.get(API, headers=HEADERS, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                reason = type(e).__name__  # e.g. ReadTimeout
            else:
                if r.status_code == 200 and "<feed" in r.text:
                    _mark_request()
                    return r.text
                limited = r.status_code == 429 or "Rate exceeded" in r.text
                reason = "rate limited" if limited else f"HTTP {r.status_code}"
            _mark_request()
            if attempt < self.attempts - 1:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
        raise ToolError(f"arxiv unavailable after {self.attempts} attempts ({reason})")
