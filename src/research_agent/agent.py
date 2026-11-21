"""The research loop: plan a step, run it, learn from the result, repeat, then synthesize.

Orchestration pattern: a ReAct-style loop with state kept in code. Each iteration the planner
(an LLM) picks one action (search one tool, finish, or refuse) after seeing a compact log of
what the loop already did. Everything the model could get wrong is enforced here instead:

- a step cap, so the loop always ends (`max_steps`; 1 is single-pass mode, the same loop);
- a seen-set, so a repeated search is never executed (it is recorded as skipped);
- a per-run set of failed tools, so a dead tool is not retried on every step;
- one evidence budget (see evidence.py), so the prompt always fits the free tier.

The loop stops on: finish, the step cap, a repeated search, two steps in a row that found
nothing new, or a spent evidence budget. The reason is recorded in the trace.
"""

import sys
import time
from dataclasses import dataclass, field

from . import llm, tracing
from .evidence import MIN_USEFUL_CHARS, Added, EvidenceStore
from .planner import Plan, PlanState, make_plan
from .synthesis import Synthesis, render, synthesize
from .tools.base import Source, Tool, ToolError
from .tools.registry import Registry, default_registry
from .tracing import SourceRecord, ToolStep, TraceStore

DEFAULT_MAX_STEPS = 3
NO_PROGRESS_LIMIT = 2  # stop after this many steps in a row that found nothing new


def _call(
    tool: Tool, plan: Plan, role: str, store: EvidenceStore
) -> tuple[list[Source], str, Added | None]:
    """Run one tool and record it as a trace step. Returns (sources, error, what was stored)."""
    options = plan.options if role == "planned" else {}  # options belong to the planned tool
    started = time.monotonic()
    error = ""
    try:
        sources = tool.run(plan.query, **options)
    except ToolError as e:
        sources, error = [], str(e)
        print(f"[tool error] {e}", file=sys.stderr)
    added = store.add(tool.id_prefix, sources) if sources else None
    records = []
    for eid in added.ids if added else []:
        stored = store.get(eid)  # the text as stored: trimmed to the evidence budget
        assert stored is not None
        records.append(
            SourceRecord(
                evidence_id=eid,
                tool=stored.tool,
                title=stored.title,
                url=stored.url,
                native_id=stored.native_id,
                published=str(stored.meta.get("published", "")),
                text=stored.text,
            )
        )
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
            new_sources=len(added.new_ids) if added else 0,
            dropped_for_budget=added.dropped if added else 0,
            trimmed_for_budget=added.trimmed if added else 0,
        )
    )
    return sources, error, added


@dataclass
class StepOutcome:
    """What one search step did."""

    tool: str  # the tool that produced the sources (a fallback if the planned one was down)
    status: str  # ok, empty or error
    new_ids: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # tool -> why it failed
    notice: str = ""  # written by code and shown to the reader


def run_step(
    plan: Plan, registry: Registry, store: EvidenceStore, allow_fallback: bool
) -> StepOutcome:
    """Run the planned tool. If it is unavailable and no later step could react to that
    (`allow_fallback`), try the other registered tools with the same query.

    Nothing is hidden: a tool that failed, and any substitute that answered in its place, is
    always reported in the notice.
    """
    failures: dict[str, str] = {}
    order = [plan.tool] + (
        [n for n in registry.names() if n != plan.tool] if allow_fallback else []
    )
    for name in order:
        role = "planned" if name == plan.tool else "fallback"
        sources, error, added = _call(registry.get(name), plan, role, store)
        if error:
            failures[name] = error
            continue
        new_ids = added.new_ids if added else []
        if not failures:
            return StepOutcome(name, "ok" if sources else "empty", new_ids)
        if not sources:
            continue  # the substitute found nothing either; try the next one
        down = "; ".join(f"{n}: {why}" for n, why in failures.items())
        text = (
            f"The planned tool ({plan.tool}) was unavailable ({down}). The sources below come "
            f"from {name} instead, which may not cover what {plan.tool} would have."
        )
        return StepOutcome(name, "ok", new_ids, failures, text)
    down = "; ".join(f"{n}: {why}" for n, why in failures.items())
    if len(failures) == 1:
        text = f"The {plan.tool} tool was unavailable ({down.split(': ', 1)[1]})."
    else:
        text = f"No sources could be retrieved ({down})."
    return StepOutcome(plan.tool, "error", [], failures, text)


def _log_line(n: int, plan: Plan, outcome: StepOutcome, store: EvidenceStore) -> str:
    """One compact line for the planner: what was tried and found, never the source text."""
    opts = f" options={plan.options}" if plan.options else ""
    head = f"step {n}: search {plan.tool} query={plan.query!r}{opts}"
    if outcome.status == "error":
        why = "; ".join(f"{t}: {r}" for t, r in outcome.failures.items())
        return f"{head} -> FAILED ({why}); it will not be offered again"
    via = f" (via {outcome.tool}, after {plan.tool} failed)" if outcome.tool != plan.tool else ""
    if outcome.status == "empty":
        return f"{head}{via} -> no results"
    if not outcome.new_ids:
        return f"{head}{via} -> nothing new (everything found was already gathered)"
    titles = "; ".join(
        f"[{e}] {(store.get(e).title if store.get(e) else '')[:60]}" for e in outcome.new_ids
    )
    return f"{head}{via} -> {titles}"


@dataclass
class Research:
    stop_reason: str
    notices: list[str] = field(default_factory=list)
    refusal: str = ""  # set when the planner refused (the question is out of scope)


def research(question: str, registry: Registry, store: EvidenceStore, max_steps: int) -> Research:
    """The loop. Fills `store` with evidence and returns why it stopped."""
    out = Research(stop_reason="step_cap")
    log: list[str] = []
    seen: dict[tuple, int] = {}  # (tool, query, options) -> the step that ran it
    failed: set[str] = set()
    no_progress = 0
    for step in range(1, max_steps + 1):
        state = PlanState(
            log=list(log),
            evidence_count=len(store),
            steps_left=max_steps - step + 1,
            failed_tools=set(failed),
        )
        plan = make_plan(question, registry, state)
        print(
            f"[plan {step}] {plan.action} {plan.tool} {plan.query!r} - {plan.reasoning}",
            file=sys.stderr,
        )
        if plan.action == "refuse":
            out.refusal = plan.reasoning or "No available tool can answer this."
            out.stop_reason = "refuse"
            return out
        if plan.action == "finish":
            out.stop_reason = "finish"
            return out
        key = (plan.tool, " ".join(plan.query.lower().split()), tuple(sorted(plan.options.items())))
        repeat = seen.get(key)
        if repeat or plan.tool in failed:
            why = f"repeat of step {repeat}" if repeat else f"{plan.tool} already failed this run"
            tracing.add_step(
                ToolStep(
                    tool=plan.tool,
                    query=plan.query,
                    options=plan.options,
                    status="skipped",
                    error=why,
                )
            )
            print(f"[skipped] {plan.tool} {plan.query!r}: {why}", file=sys.stderr)
            out.stop_reason = "duplicate" if repeat else "tool_unavailable"
            return out
        seen[key] = step
        outcome = run_step(plan, registry, store, allow_fallback=step == max_steps)
        failed |= set(outcome.failures)
        if outcome.notice:
            out.notices.append(outcome.notice)
        log.append(_log_line(step, plan, outcome, store))
        no_progress = 0 if outcome.new_ids else no_progress + 1
        if no_progress >= NO_PROGRESS_LIMIT:
            out.stop_reason = "no_new_evidence"
            return out
        if store.chars_left < MIN_USEFUL_CHARS * 2:
            out.stop_reason = "budget"
            return out
    return out


def answer(
    question: str,
    registry: Registry | None = None,
    traces: TraceStore | None = None,
    max_steps: int = DEFAULT_MAX_STEPS,
) -> str:
    if max_steps < 1:
        raise ValueError("max_steps must be at least 1")
    registry = registry or default_registry()
    traces = traces or TraceStore.from_env()
    store = EvidenceStore()
    with tracing.start_run(question, traces, model=llm.model_name()) as run:
        run.trace.max_steps = max_steps
        run.trace.mode = "single-pass" if max_steps == 1 else "multi-step"
        found = research(question, registry, store, max_steps)
        run.trace.stop_reason = found.stop_reason
        if found.refusal:
            # Out of scope: no evidence was gathered, so refuse without spending an LLM call.
            text = render(Synthesis(refusal_reason=found.refusal), store)
            run.finish("out_of_scope", text)
            return text
        note = " ".join(found.notices)
        result = synthesize(question, store, f"\n({note})" if note else "")
        result.notices = found.notices
        text = render(result, store)
        run.finish("answered" if result.sourced else "refused", text, found.notices)
        return text
