"""M3: plan one tool call, run it, then synthesize validated, cited claims."""

import sys

from .evidence import EvidenceStore
from .planner import make_plan
from .synthesis import Synthesis, render, synthesize
from .tools.base import ToolError
from .tools.registry import Registry, default_registry


def answer(question: str, registry: Registry | None = None) -> str:
    registry = registry or default_registry()
    store = EvidenceStore()
    plan = make_plan(question, registry)
    print(f"[plan] {plan.tool}: {plan.query!r} - {plan.reasoning}", file=sys.stderr)
    if plan.tool == "none":
        # Out of scope: no tool can help, so refuse without spending an LLM call.
        result = Synthesis(refusal_reason=plan.reasoning or "No available tool can answer this.")
        return render(result, store)
    note = ""
    tool = registry.get(plan.tool)
    try:
        store.add(tool.id_prefix, tool.run(plan.query, **plan.options))
    except ToolError as e:
        note = f"\n(The {plan.tool} tool failed: {e})"
        print(f"[tool error] {e}", file=sys.stderr)
    return render(synthesize(question, store, note), store)
