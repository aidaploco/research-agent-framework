"""Wikipedia tool: search the Action API, fetch plain-text article extracts."""

import requests

from ..retry import RetriesExhausted, Retry, with_retries
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

    def __init__(self, max_results: int = 3, max_chars: int = 8000, other_chars: int = 3000):
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

        def attempt(n: int) -> dict:
            try:
                r = requests.get(
                    API, headers=HEADERS, timeout=TIMEOUT, params={**params, "format": "json"}
                )
            except (requests.Timeout, requests.ConnectionError) as e:
                raise Retry(type(e).__name__) from e
            if r.status_code == 429:
                raise Retry("rate limited")
            r.raise_for_status()
            return r.json()

        try:
            return with_retries("wikipedia", attempt, attempts=attempts, backoff=BACKOFF)
        except RetriesExhausted as e:
            raise ToolError(
                f"wikipedia unavailable after {e.attempts} attempts ({e.reason})"
            ) from e

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

    def _extract(self, pageid: int) -> tuple[str, str] | None:
        """The (title, full plain-text extract) of one page, or None if it has no text.

        One request per page, on purpose: for whole-article text the API lowers `exlimit` to 1,
        so a request for several pages returns text for just one of them, and not the first
        one asked for.
        """
        pages = self._get(
            {"action": "query", "prop": "extracts", "explaintext": 1, "pageids": pageid}
        )["query"]["pages"]
        page = pages.get(str(pageid), {})
        text = (page.get("extract") or "").strip()
        return (page["title"], text) if text else None

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
                "srlimit": self.max_results + 1,  # one spare: the exact title may repeat a hit
            }
        )["query"]["search"]
        for hit in hits:  # Wikipedia's ranking order
            if len(sources) >= self.max_results:
                break
            found = self._extract(hit["pageid"])
            if found and all(found[0] != s.title for s in sources):
                sources.append(self._source(found[0], found[1], self.other_chars))
        return sources
