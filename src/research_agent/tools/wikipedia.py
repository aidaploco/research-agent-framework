"""Wikipedia tool: search the Action API, fetch plain-text article extracts."""

import requests

API = "https://en.wikipedia.org/w/api.php"
HEADERS = {"User-Agent": "research-agent-framework/0.1 (educational project)"}


def search(query, max_results=2, max_chars=4000):
    """Return a list of sources: {title, url, text} for the top matching articles."""
    r = requests.get(
        API,
        headers=HEADERS,
        timeout=20,
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
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
            "exlimit": max_results,
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
                {
                    "title": title,
                    "url": "https://en.wikipedia.org/wiki/" + title.replace(" ", "_"),
                    "text": text[:max_chars],
                }
            )
    return sources
