"""Evidence store: gives every retrieved source a stable ID (W1, A2, ...).

IDs are the tool's prefix plus a per-prefix counter, and they stay stable for the whole
run, so a later step's W1 means the same source as an earlier step's. A source with a URL
already stored keeps its original ID instead of being added twice.
"""

from .tools.base import Source


class EvidenceStore:
    def __init__(self) -> None:
        self._by_id: dict[str, Source] = {}
        self._by_url: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def add(self, prefix: str, sources: list[Source]) -> list[str]:
        """Store sources and return their evidence IDs in order."""
        ids = []
        for source in sources:
            existing = self._by_url.get(source.url)
            if existing:
                ids.append(existing)
                continue
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            eid = f"{prefix}{self._counts[prefix]}"
            self._by_id[eid] = source
            self._by_url[source.url] = eid
            ids.append(eid)
        return ids

    def get(self, eid: str) -> Source | None:
        return self._by_id.get(eid)

    def items(self) -> list[tuple[str, Source]]:
        return list(self._by_id.items())

    def __len__(self) -> int:
        return len(self._by_id)
