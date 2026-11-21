"""Evidence store: gives every retrieved source a stable ID (W1, A2, ...) and a size budget.

IDs are the tool's prefix plus a per-prefix counter, and they stay stable for the whole
run, so a later step's W1 means the same source as an earlier step's. A source with a URL
already stored keeps its original ID instead of being added twice.

The store also enforces one character budget for the whole run. The free LLM tier allows
8,000 tokens a minute for prompt plus output, so what the model is shown must be bounded.
Trimming happens here, as sources enter, so the prompt, the grounding check and the trace
all see exactly the same text.
"""

from dataclasses import dataclass, field

from .tools.base import Source

EVIDENCE_CHAR_BUDGET = 16_000  # about 4,000 to 5,000 tokens
MIN_USEFUL_CHARS = 300  # a source trimmed below this is not worth an ID


@dataclass
class Added:
    ids: list[str] = field(default_factory=list)  # one per source that is stored, in order
    new_ids: list[str] = field(default_factory=list)  # the ones that were not already stored
    dropped: int = 0  # sources left out because the budget was spent
    trimmed: int = 0  # sources cut short to fit the budget


class EvidenceStore:
    def __init__(self, char_budget: int = EVIDENCE_CHAR_BUDGET) -> None:
        self.char_budget = char_budget
        self._by_id: dict[str, Source] = {}
        self._by_url: dict[str, str] = {}
        self._counts: dict[str, int] = {}
        self._used = 0

    @staticmethod
    def _cost(source: Source) -> int:
        return len(source.text) + len(source.title) + len(source.url) + 30  # + prompt framing

    @property
    def chars_left(self) -> int:
        return max(0, self.char_budget - self._used)

    def add(self, prefix: str, sources: list[Source]) -> Added:
        """Store sources within the budget and return what happened to each."""
        out = Added()
        for source in sources:
            existing = self._by_url.get(source.url)
            if existing:
                out.ids.append(existing)
                continue
            room = self.chars_left - (self._cost(source) - len(source.text))
            if room < MIN_USEFUL_CHARS:
                out.dropped += 1
                continue
            if len(source.text) > room:
                source = source.model_copy(update={"text": source.text[:room]})
                out.trimmed += 1
            self._counts[prefix] = self._counts.get(prefix, 0) + 1
            eid = f"{prefix}{self._counts[prefix]}"
            self._by_id[eid] = source
            self._by_url[source.url] = eid
            self._used += self._cost(source)
            out.ids.append(eid)
            out.new_ids.append(eid)
        return out

    def get(self, eid: str) -> Source | None:
        return self._by_id.get(eid)

    def items(self) -> list[tuple[str, Source]]:
        return list(self._by_id.items())

    def __len__(self) -> int:
        return len(self._by_id)
