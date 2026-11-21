"""Wikipedia tool: search the Action API, fetch plain-text article extracts."""

import time

import requests

from .base import Source, Tool, ToolError

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "research-agent-framework/0.1 (educational project)"}
TIMEOUT = 10.0  # seconds per request
BACKOFF = (1.0, 2.0)  # seconds to wait before attempt 2 and attempt 3


class WikipediaTool(Tool):
    name = "wikipedia"
    id_prefix = "W"
    description = (
        "Encyclopedia articles. Best for definitions, how institutions and mechanisms work, "
        "history and general background. Query: a short topic name (2-5 words)."
    )

    def __init__(self, max_results: int = 2, max_chars: int = 8000, other_chars: int = 3000):
        # The top article gets the larger budget: key facts often sit past the first few
        # thousand characters (Basel III's capital ratios start near character 4,300).
        # The token limit on the free LLM tier keeps the total bounded.
        self.max_results = max_results
        self.max_chars = max_chars
        self.other_chars = other_chars

    def run(self, query: str, **options) -> list[Source]:
        try:
            return self._search(query)
        except requests.RequestException as e:
            raise ToolError(f"wikipedia request failed: {e}") from e

    def _get(self, params: dict, attempts: int = 3) -> dict:
        """One API call. Rate limits (429), timeouts and connection errors are retried."""
        reason = "no attempt made"
        for attempt in range(attempts):
            try:
                r = requests.get(
                    API, headers=HEADERS, timeout=TIMEOUT, params={**params, "format": "json"}
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                reason = type(e).__name__
            else:
                if r.status_code != 429:
                    r.raise_for_status()
                    return r.json()
                reason = "rate limited"
            if attempt < attempts - 1:
                time.sleep(BACKOFF[min(attempt, len(BACKOFF) - 1)])
        raise ToolError(f"wikipedia unavailable after {attempts} attempts ({reason})")

    def _source(self, title: str, text: str, limit: int) -> Source:
        return Source(
            tool=self.name,
            title=title,
            url="https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
            text=text[:limit],
        )

    def _title_lookup(self, query: str) -> Source | None:
        """The article whose title is `query` (redirects followed), if it exists.

        Full-text search ranks an exact title like "Basel III" below pages that merely
        mention it, so the exact article is tried first. Disambiguation pages are skipped.
        """
        data = self._get(
            {
                "action": "query",
                "titles": query,
                "redirects": 1,
                "prop": "extracts",
                "explaintext": 1,
            }
        )
        for page in data["query"]["pages"].values():
            text = (page.get("extract") or "").strip()
            if "missing" in page or not text or "may refer to" in text[:300]:
                continue
            return self._source(page["title"], text, self.max_chars)
        return None

    def _search(self, query: str) -> list[Source]:
        sources: list[Source] = []
        exact = self._title_lookup(query)
        if exact:
            sources.append(exact)
        hits = self._get(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": self.max_results,
            }
        )["query"]["search"]
        if hits:
            pages = self._get(
                {
                    "action": "query",
                    "prop": "extracts",
                    "explaintext": 1,
                    "pageids": "|".join(str(h["pageid"]) for h in hits),
                    "exlimit": self.max_results,
                }
            )["query"]["pages"]
            for h in hits:  # keep Wikipedia's ranking order
                p = pages.get(str(h["pageid"]), {})
                text = (p.get("extract") or "").strip()
                if text and all(p["title"] != s.title for s in sources):
                    sources.append(self._source(p["title"], text, self.other_chars))
        return sources[: self.max_results]
