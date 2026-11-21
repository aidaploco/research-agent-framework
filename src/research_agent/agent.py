"""M2: plan one tool call, run it, then synthesize a cited answer."""

import sys

from . import llm
from .planner import make_plan
from .tools.base import Source, ToolError
from .tools.registry import Registry, default_registry

SYSTEM = """You are a careful research assistant for a bank's research team.
Answer the question using ONLY the numbered sources provided.
Format:
## From sources
Claims taken from the sources, each followed by a citation like [1] or [2].
## Model inference / caveats
Anything you add that is NOT in the sources (reasoning, inference, gaps, outdated risk). Say so explicitly.
If the sources do not answer the question, or the question is outside banking/finance/economics research, say that plainly instead of guessing. Never invent citations or figures."""


def format_context(sources: list[Source]) -> str:
    if not sources:
        return "(no sources were found)"
    blocks = []
    for i, s in enumerate(sources, 1):
        head = f"[{i}] {s.title} ({s.url})"
        if s.meta.get("published"):
            head += f" published {s.meta['published']}"
        blocks.append(f"{head}\n{s.text}")
    return "\n\n".join(blocks)


def answer(question: str, registry: Registry | None = None) -> str:
    registry = registry or default_registry()
    plan = make_plan(question, registry)
    print(f"[plan] {plan.tool}: {plan.query!r} - {plan.reasoning}", file=sys.stderr)
    sources: list[Source] = []
    note = ""
    if plan.tool != "none":
        try:
            sources = registry.get(plan.tool).run(plan.query, **plan.options)
        except ToolError as e:
            note = f"\n(The {plan.tool} tool failed: {e})"
            print(f"[tool error] {e}", file=sys.stderr)
    text = llm.chat(
        [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": f"Question: {question}\n\nSources:\n{format_context(sources)}{note}",
            },
        ]
    )
    refs = "\n".join(f"[{i}] {s.citation()}" for i, s in enumerate(sources, 1))
    return f"{text}\n\n## Sources\n{refs or '(none)'}"
