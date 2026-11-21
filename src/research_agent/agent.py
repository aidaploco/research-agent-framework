"""Plan one tool call, run it (falling back if it is down), synthesize cited claims, and trace it."""

import sys
import time

from . import llm, tracing
from .evidence import EvidenceStore
from .planner import Plan, make_plan
from .synthesis import Synthesis, render, synthesize
from .tools.base import Source, Tool, ToolError
from .tools.registry import Registry, default_registry
from .tracing import SourceRecord, ToolStep, TraceStore


def _call(tool: Tool, plan: Plan, role: str, store: EvidenceStore) -> list[Source] | ToolError:
    """Run one tool and record it as a trace step. Returns the sources, or the error."""
    options = plan.options if role == "planned" else {}  # options belong to the planned tool
    started = time.monotonic()
    error = ""
    try:
        sources = tool.run(plan.query, **options)
    except ToolError as e:
        sources, error = [], str(e)
        print(f"[tool error] {e}", file=sys.stderr)
    records: list[SourceRecord] = []
    if sources:
        ids = store.add(tool.id_prefix, sources)
        records = [
            SourceRecord(
                evidence_id=eid,
                tool=s.tool,
                title=s.title,
                url=s.url,
                native_id=s.native_id,
                published=str(s.meta.get("published", "")),
                text=s.text,
            )
            for eid, s in zip(ids, sources, strict=True)
        ]
    tracing.add_step(
        ToolStep(
            duration_ms=int((time.monotonic() - started) * 1000),
            tool=tool.name,
            role=role,  # type: ignore[arg-type]
            query=plan.query,
            options=options,
            status="error" if error else ("ok" if sources else "empty"),
            error=error,
            sources=records,
        )
    )
    return ToolError(error) if error else sources


def run_tool(plan: Plan, registry: Registry, store: EvidenceStore) -> tuple[list[str], str]:
    """Run the planned tool; if it is unavailable, try the other registered tools.

    Returns (notices, note). Notices are written by code and shown to the reader; the note
    tells the model what happened so it can caveat its answer. Nothing is hidden: a tool that
    failed, and any substitute that answered in its place, is always reported.
    """
    failures: dict[str, str] = {}
    order = [plan.tool] + [n for n in registry.names() if n != plan.tool]
    for name in order:
        role = "planned" if name == plan.tool else "fallback"
        result = _call(registry.get(name), plan, role, store)
        if isinstance(result, ToolError):
            failures[name] = str(result)
            continue
        if not failures:
            return [], ""
        if not result:
            continue  # the substitute found nothing either; try the next one
        down = "; ".join(f"{n}: {why}" for n, why in failures.items())
        text = (
            f"The planned tool ({plan.tool}) was unavailable ({down}). The sources below come "
            f"from {name} instead, which may not cover what {plan.tool} would have."
        )
        return [text], text
    down = "; ".join(f"{n}: {why}" for n, why in failures.items())
    text = f"No sources could be retrieved ({down})."
    return [text], text


def answer(
    question: str, registry: Registry | None = None, traces: TraceStore | None = None
) -> str:
    registry = registry or default_registry()
    traces = traces or TraceStore.from_env()
    store = EvidenceStore()
    with tracing.start_run(question, traces, model=llm.model_name()) as run:
        plan = make_plan(question, registry)
        print(f"[plan] {plan.tool}: {plan.query!r} - {plan.reasoning}", file=sys.stderr)
        if plan.tool == "none":
            # Out of scope: no tool can help, so refuse without spending an LLM call.
            result = Synthesis(
                refusal_reason=plan.reasoning or "No available tool can answer this."
            )
            text = render(result, store)
            run.finish("out_of_scope", text)
            return text
        notices, note = run_tool(plan, registry, store)
        result = synthesize(question, store, f"\n({note})" if note else "")
        result.notices = notices
        text = render(result, store)
        run.finish("answered" if result.sourced else "refused", text, notices)
        return text
