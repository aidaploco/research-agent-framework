"""Wikipedia tool: search the Action API, fetch plain-text article extracts."""

import requests

from .base import Source, Tool, ToolError

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "research-agent-framework/0.1 (educational project)"}


class WikipediaTool(Tool):
    name = "wikipedia"
    description = (
        "Encyclopedia articles. Best for definitions, how institutions and mechanisms work, "
        "history and general background. Query: a short topic name (2-5 words)."
    )

    def __init__(self, max_results: int = 2, max_chars: int = 4000):
        self.max_results = max_results
        self.max_chars = max_chars

    def run(self, query: str, **options) -> list[Source]:
        try:
            return self._search(query)
        except requests.RequestException as e:
            raise ToolError(f"wikipedia request failed: {e}") from e

    def _search(self, query: str) -> list[Source]:
        r = requests.get(
            API,
            headers=HEADERS,
            timeout=20,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": self.max_results,
                "format": "json",
            },
        )
        r.raise_for_status()
        hits = r.json()["query"]["search"]
        if not hits:
            return []
        r = requests.get(
            API,
            headers=HEADERS,
            timeout=20,
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "pageids": "|".join(str(h["pageid"]) for h in hits),
                "exlimit": self.max_results,
                "format": "json",
            },
        )
        r.raise_for_status()
        pages = r.json()["query"]["pages"]
        sources = []
        for h in hits:  # keep Wikipedia's ranking order
            p = pages.get(str(h["pageid"]), {})
            text = (p.get("extract") or "").strip()
            if text:
                title = p["title"]
                sources.append(
                    Source(
                        tool=self.name,
                        title=title,
                        url="https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                        text=text[: self.max_chars],
                    )
                )
        return sources
