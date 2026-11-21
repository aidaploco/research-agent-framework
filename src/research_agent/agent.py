"""M3: plan one tool call, run it (falling back if it is down), then synthesize cited claims."""

import sys

from .evidence import EvidenceStore
from .planner import Plan, make_plan
from .synthesis import Synthesis, render, synthesize
from .tools.base import ToolError
from .tools.registry import Registry, default_registry


def run_tool(plan: Plan, registry: Registry, store: EvidenceStore) -> tuple[list[str], str]:
    """Run the planned tool; if it is unavailable, try the other registered tools.

    Returns (notices, note). Notices are written by code and shown to the reader; the note
    tells the model what happened so it can caveat its answer. Nothing is hidden: a tool that
    failed, and any substitute that answered in its place, is always reported.
    """
    failures: dict[str, str] = {}
    order = [plan.tool] + [n for n in registry.names() if n != plan.tool]
    for name in order:
        tool = registry.get(name)
        options = plan.options if name == plan.tool else {}  # options belong to the planned tool
        try:
            sources = tool.run(plan.query, **options)
        except ToolError as e:
            failures[name] = str(e)
            print(f"[tool error] {e}", file=sys.stderr)
            continue
        if not failures:
            store.add(tool.id_prefix, sources)
            return [], ""
        if not sources:
            continue  # the substitute found nothing either; try the next one
        store.add(tool.id_prefix, sources)
        down = "; ".join(f"{n}: {why}" for n, why in failures.items())
        text = (
            f"The planned tool ({plan.tool}) was unavailable ({down}). The sources below come "
            f"from {name} instead, which may not cover what {plan.tool} would have."
        )
        return [text], text
    down = "; ".join(f"{n}: {why}" for n, why in failures.items())
    text = f"No sources could be retrieved ({down})."
    return [text], text


def answer(question: str, registry: Registry | None = None) -> str:
    registry = registry or default_registry()
    store = EvidenceStore()
    plan = make_plan(question, registry)
    print(f"[plan] {plan.tool}: {plan.query!r} - {plan.reasoning}", file=sys.stderr)
    if plan.tool == "none":
        # Out of scope: no tool can help, so refuse without spending an LLM call.
        result = Synthesis(refusal_reason=plan.reasoning or "No available tool can answer this.")
        return render(result, store)
    notices, note = run_tool(plan, registry, store)
    result = synthesize(question, store, f"\n({note})" if note else "")
    result.notices = notices
    return render(result, store)
