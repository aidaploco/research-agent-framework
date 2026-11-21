"""Grounding: check that the figures in a claim are really in the cited sources.

Two checks, both deterministic:

1. Normalized match. Numbers are compared by value, not by spelling, so "1,200" equals
   "1200", "$46 billion" equals "46,000,000,000", "4.5%" equals "4.5 percent", and
   "thirty" equals "30". Units must agree when both sides state one (a percent is not a
   basis point). ISO dates are compared as dates.
2. Proximity. A matching number only counts if, near it in the source, appears a keyword
   taken from the claim's own wording around that number. This catches a real number
   attached to the wrong fact: "leverage ratio of 3%" is not grounded by an unrelated "3%".
   Plain years only need to be present.

Limits: this is keyword proximity, not understanding. It cannot tell that a claim reverses
the meaning of the passage, it ignores single-digit plain integers, and it does not convert
between units (basis points to percent) or parse month-name dates.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

DASHES = str.maketrans(dict.fromkeys("‐‑‒–—−", "-"))

NUMBER_WORDS = {
    **{
        w: n
        for n, w in enumerate(
            "one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
            "fifteen sixteen seventeen eighteen nineteen twenty".split(),
            1,
        )
    },
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}  # fmt: skip
SCALES = {
    "thousand": 3,
    "k": 3,
    "million": 6,
    "mn": 6,
    "billion": 9,
    "bn": 9,
    "trillion": 12,
    "tn": 12,
}
UNIT_ALIASES = {
    "%": "%", "percent": "%", "per cent": "%", "percentage": "%",
    "basis point": "bp", "basis points": "bp", "bp": "bp", "bps": "bp",
    "percentage point": "pp", "percentage points": "pp",
}  # fmt: skip

_WORDS = "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
_SCALES = "|".join(SCALES)
FIGURE = re.compile(
    rf"""
    (?P<date>\d{{4}}-\d{{2}}-\d{{2}})
  | (?<![A-Za-z\d.])(?:[$€£]\s?)?(?P<num>\d[\d,\u00a0\u202f]*(?:\.\d+)?)
        (?:\s*(?P<scale>{_SCALES})\b)?
        (?:\s*(?P<unit>%|percentage\spoints?\b|percent\b|per\scent\b|basis\spoints?\b|bps\b|bp\b))?
  | \b(?P<word>{_WORDS})\b(?:\s*(?P<wunit>%|percent\b|per\scent\b|basis\spoints?\b))?
    """,
    re.VERBOSE | re.IGNORECASE,
)

STOPWORDS = set(
    """about above after again against also although among around because been before below
    between both could during each from have into more most much must only other over same
    should since some such than that their them then there these they this those through under
    until very were what when where which while will with within would approximately roughly
    nearly almost least minimum maximum average typically usually generally several many
    percent billion million thousand trillion basis points point cent percentage
    """.split()
)
KEYWORD = re.compile(r"[a-z][a-z0-9\-]{3,}")  # 4+ characters, starts with a letter
CLAIM_WINDOW = 60  # characters of the claim kept on each side of a number
SOURCE_WINDOW = 100  # most characters of the source searched on each side of a match


@dataclass(frozen=True)
class Figure:
    value: Decimal | str  # a Decimal, or an ISO date string
    unit: str | None
    start: int
    end: int
    text: str

    def checkable(self) -> bool:
        """Worth verifying: dates, anything with a unit or scale, decimals, 2+ digit integers."""
        if isinstance(self.value, str) or self.unit:
            return True
        return self.value != self.value.to_integral_value() or abs(self.value) >= 10

    def is_year(self) -> bool:
        """A plain four-digit year. It belongs to a whole event, not to the few words beside it."""
        return (
            not isinstance(self.value, str)
            and self.unit is None
            and self.value == self.value.to_integral_value()
            and 1900 <= self.value <= 2100
        )

    def matches(self, other: "Figure") -> bool:
        if isinstance(self.value, str) or isinstance(other.value, str):
            return self.value == other.value
        if self.value != other.value:
            return False
        return self.unit is None or other.unit is None or self.unit == other.unit


def _unit(raw: str | None) -> str | None:
    if not raw:
        return None
    return UNIT_ALIASES.get(re.sub(r"\s+", " ", raw.lower()))


def figures(text: str) -> list[Figure]:
    """Every number in `text`, normalized (commas, currency, scale words, number words)."""
    text = text.translate(DASHES)
    out = []
    for m in FIGURE.finditer(text):
        if m.group("date"):
            out.append(Figure(m.group("date"), None, m.start(), m.end(), m.group(0)))
        elif m.group("num"):
            value = Decimal(re.sub(r"[,\u00a0\u202f]", "", m.group("num")))
            if m.group("scale"):
                value *= Decimal(10) ** SCALES[m.group("scale").lower()]
            out.append(
                Figure(value.normalize(), _unit(m.group("unit")), m.start(), m.end(), m.group(0))
            )
        else:
            value = Decimal(NUMBER_WORDS[m.group("word").lower()])
            out.append(Figure(value, _unit(m.group("wunit")), m.start(), m.end(), m.group(0)))
    return out


def stem(word: str) -> str:
    """Light stemming: strip a plural or verb ending ("ratios" -> "ratio", "divided" -> "divid").

    Deliberately not a prefix cut: "dividend" and "divided" must stay different words.
    """
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def keyword_map(text: str) -> dict[str, str]:
    """Content words by stem (so "ratios" matches "ratio"): {stem: first word seen}."""
    out: dict[str, str] = {}
    for w in KEYWORD.findall(text.lower()):
        if w not in STOPWORDS:
            out.setdefault(stem(w), w)
    return out


def keywords(text: str) -> set[str]:
    return set(keyword_map(text))


# A colon introduces a list ("... at all times of: 4.5%"), so it does not end a sentence.
SENTENCE_END = re.compile(r"[.!?;]\s|\n")  # source text: stop at a sentence or line
CLAUSE_END = re.compile(r"[.!?;,]\s|\n")  # claim text: also stop at a comma
SENTENCE_ONLY = re.compile(r"[.!?;]\s")  # ignores line breaks
SPARSE_WINDOW = 200  # reach for a number that sits alone on its line


def window_around(text: str, start: int, end: int, cap: int, boundary: re.Pattern) -> str:
    """The span around text[start:end] up to the nearest `boundary`, at most `cap` characters
    each side. Bounding keeps a number from being "near" a word that belongs to the
    neighbouring sentence (or, in a claim, the neighbouring clause)."""
    left = max([m.end() for m in boundary.finditer(text, 0, start)] or [0])
    right = next((m.start() for m in boundary.finditer(text, end)), len(text))
    return text[max(left, start - cap) : min(right, end + cap)]


def source_context(haystack: str, fig: Figure) -> str:
    """The words in the source that describe `fig`.

    Normally the sentence or line it is in. When that has almost no words (a number alone on
    its own line, as in lists and tables), its description is the lead-in before it, so the
    window widens to the surrounding text, still stopping at sentence ends.
    """
    w = window_around(haystack, fig.start, fig.end, SOURCE_WINDOW, SENTENCE_END)
    if len(keywords(w)) < 2:
        w = window_around(haystack, fig.start, fig.end, SPARSE_WINDOW, SENTENCE_ONLY)
    return w


def figure_problem(claim_text: str, haystack: str) -> str | None:
    """Why the claim's figures are not grounded in `haystack`, or None if they are."""
    claim_text = claim_text.translate(DASHES)
    source_figures = figures(haystack)
    haystack = haystack.translate(DASHES)
    claim_all = keyword_map(claim_text)
    problems = []
    for fig in figures(claim_text):
        if not fig.checkable():
            continue
        local = window_around(claim_text, fig.start, fig.end, CLAIM_WINDOW, CLAUSE_END)
        wanted = keyword_map(local) or claim_all
        found = [g for g in source_figures if fig.matches(g)]
        if not found:
            problems.append(f"figure {fig.text.strip()!r} does not appear in the sources")
            continue
        if not wanted or fig.is_year():
            continue  # nothing to anchor on, or a year: presence in the sources is enough
        near = any(set(wanted) & keywords(source_context(haystack, g)) for g in found)
        if not near:
            problems.append(
                f"figure {fig.text.strip()!r} appears in the sources but not near "
                f"any of its context words {sorted(wanted.values())[:6]}"
            )
    return "; ".join(problems) or None
