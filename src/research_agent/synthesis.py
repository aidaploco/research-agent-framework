"""Synthesis: the model returns structured claims; code validates and renders them.

The model never writes citation markers. It returns claims, each with the evidence IDs
that support it, and code (a) checks every ID exists, (b) checks that numbers in a claim
appear in the cited sources, (c) lets the model repair once, and (d) demotes any claim
that still fails to an "unverified" section. Demoted claims are never dropped: dropping
would hide information, and demoting keeps retrieved facts visibly separate from
anything the model asserts on its own.
"""

import re

from pydantic import BaseModel, ConfigDict

from . import llm
from .evidence import EvidenceStore
from .grounding import figure_problem

SYSTEM = """You are a careful research assistant for a bank's research team.
Answer the question using ONLY the sources provided. Each source has an evidence ID like [W1] or [A2].
Return JSON with:
- claims: each claim is one statement taken from the sources (text) and the evidence IDs that
  directly support it (evidence_ids, e.g. ["W1", "A2"]). Use only IDs that appear in the sources.
  Make each claim ONE atomic fact: if several facts have different figures or dates, write
  several claims. Do not write citation markers like [W1] inside text. Do not put anything here that the
  sources do not say; use exact figures from the sources.
- inferences: your own reasoning or conclusions that go beyond the sources, one per item.
- caveats: limits of the evidence (gaps, age of sources, what is missing).
- refusal_reason: "" normally. If the sources do not answer the question, set it to a short
  explanation and return no claims. Never invent facts, figures or sources."""

MARKER = re.compile(r"【[^】]*】|\[[A-Z]\d+\]")


class Claim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    evidence_ids: list[str]


class Answer(BaseModel):
    """What the model returns. No defaults: strict schemas require every field."""

    model_config = ConfigDict(extra="forbid")

    claims: list[Claim]
    inferences: list[str]
    caveats: list[str]
    refusal_reason: str


class Unverified(BaseModel):
    text: str
    reason: str


class Synthesis(BaseModel):
    """A validated answer, ready to render."""

    notices: list[str] = []  # written by code, not the model: tool failures, substitutions
    sourced: list[Claim] = []
    unverified: list[Unverified] = []
    inferences: list[str] = []
    caveats: list[str] = []
    refusal_reason: str = ""


def normalize_ids(ids: list[str]) -> list[str]:
    """Split "W1, A2" style strings, trim, upper-case and de-duplicate, keeping order."""
    out: list[str] = []
    for raw in ids:
        for part in re.split(r"[,;\s]+", raw):
            part = part.strip().upper()
            if part and part not in out:
                out.append(part)
    return out


def clean_text(text: str) -> str:
    """Remove any citation markers the model wrote itself: code adds the real ones."""
    return re.sub(r"\s+", " ", MARKER.sub("", text)).strip()


def _haystack(store: EvidenceStore, ids: list[str]) -> str:
    """The text a claim may be checked against: title, body, the source's own ID, and date."""
    parts = []
    for eid in ids:
        s = store.get(eid)
        if s:
            parts += [s.title, s.text, s.native_id]
            if s.meta.get("published"):
                parts.append(f"published {s.meta['published']}")
    return "\n".join(parts)


def claim_problem(claim: Claim, store: EvidenceStore) -> str | None:
    """Why a claim cannot be shown as sourced, or None if it can."""
    ids = normalize_ids(claim.evidence_ids)
    if not ids:
        return "cites no evidence"
    unknown = [i for i in ids if store.get(i) is None]
    if unknown:
        return f"cites unknown evidence IDs {unknown}"
    return figure_problem(claim.text, _haystack(store, ids))


def check_answer(answer: Answer, store: EvidenceStore) -> str | None:
    """Aggregate claim problems into one message the model can act on."""
    lines = []
    for i, claim in enumerate(answer.claims, 1):
        problem = claim_problem(claim, store)
        if problem:
            lines.append(f'claim {i} ("{claim.text[:60]}") {problem}')
    if not lines:
        return None
    valid = [eid for eid, _ in store.items()]
    return "; ".join(lines) + f". Valid evidence IDs: {valid}"


def classify(answer: Answer, store: EvidenceStore) -> Synthesis:
    """Split claims into sourced and unverified. Nothing is dropped."""
    sourced, unverified = [], []
    for claim in answer.claims:
        text = clean_text(claim.text)
        problem = claim_problem(claim, store)
        if problem:
            unverified.append(Unverified(text=text, reason=problem))
        else:
            sourced.append(Claim(text=text, evidence_ids=normalize_ids(claim.evidence_ids)))
    return Synthesis(
        sourced=sourced,
        unverified=unverified,
        inferences=[clean_text(t) for t in answer.inferences],
        caveats=[clean_text(t) for t in answer.caveats],
        refusal_reason=clean_text(answer.refusal_reason),
    )


def format_context(store: EvidenceStore) -> str:
    if not len(store):
        return "(no sources were found)"
    blocks = []
    for eid, s in store.items():
        head = f"[{eid}] {s.title} ({s.url})"
        if s.meta.get("published"):
            head += f" published {s.meta['published']}"
        blocks.append(f"{head}\n{s.text}")
    return "\n\n".join(blocks)


def synthesize(question: str, store: EvidenceStore, note: str = "") -> Synthesis:
    """Ask the model for structured claims, with one repair retry, then validate."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": f"Question: {question}\n\nSources:\n{format_context(store)}{note}",
        },
    ]
    try:
        answer, _ = llm.structured(
            messages, Answer, check=lambda a: check_answer(a, store), attempts=2, max_tokens=2000
        )
    except llm.StructuredOutputError as e:
        return Synthesis(
            refusal_reason="The model did not return a valid structured answer.",
            caveats=[f"Technical detail: {e}"],
        )
    return classify(answer, store)


def render(result: Synthesis, store: EvidenceStore) -> str:
    """Plain-text answer. Citation markers are written here, by code."""
    out: list[str] = []
    if result.notices:
        out.append("## Notices")
        out += [f"- {n}" for n in result.notices]
        if result.sourced:
            out.append("")
    if result.sourced:
        out.append("## From sources")
        for c in result.sourced:
            marks = "".join(f"[{i}]" for i in c.evidence_ids)
            out.append(f"- {c.text} {marks}")
    if result.refusal_reason:
        out.append("\n## Cannot answer from sources" if not result.sourced else "\n## Gaps")
        out.append(result.refusal_reason)
    if result.unverified:
        out.append(
            "\n## Unverified (model statements without valid sourcing; do not rely on these)"
        )
        out += [f"- {u.text} (not shown as sourced: {u.reason})" for u in result.unverified]
    if result.inferences or result.caveats:
        out.append("\n## Model inference / caveats")
        out += [f"- Inference: {t}" for t in result.inferences]
        out += [f"- Caveat: {t}" for t in result.caveats]
    out.append("\n## Sources")
    if len(store):
        out += [f"[{eid}] {s.citation()}" for eid, s in store.items()]
    else:
        out.append("(none)")
    return "\n".join(out)
