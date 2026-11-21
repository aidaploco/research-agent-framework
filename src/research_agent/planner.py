"""Planner: decides the next step of the research loop (search, finish or refuse).

The planner is shown the question, the tools it may use, and a compact log of what the loop
has already done (tool, query, outcome and the titles found, never the source text). Code
enforces the rules the model cannot be trusted with: it may refuse only before any evidence
exists, it may finish only after some does, and it may not pick a tool that failed this run.
"""

import time
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ValidationError

from . import llm, tracing
from .tools.registry import Registry

PLANNER_SYSTEM = """You plan research for a bank's research team, one step at a time.
Tools:
{tools}
{unavailable}
What has been done so far:
{log}

You may make at most {steps_left} more search(es). Choose the next action:
- search: call ONE tool. Give tool, query and options.
- finish: the evidence gathered so far is enough to answer. Only valid once at least one source has been found.
- refuse: the question is unrelated to banking, finance, economics or their research literature. Only valid before any source has been found.

Reply with a JSON object: action, reasoning (one sentence: why), tool, query, options (an object; {{}} if none).
Write queries as short keywords, not full sentences. For wikipedia, name the article you expect
(for example "Basel III" or "Discount window"), not a description of the topic.
If an earlier search found nothing or the wrong thing, change it: different words, a different article
title, or a different tool. Repeating a query returns the same thing.
Choose finish as soon as the evidence covers what the question asks; do not search just to confirm.
If the question needs more than one kind of source (for example a definition and recent papers), use a different tool for each.
Never repeat a search that is already listed."""


class Plan(BaseModel):
    action: Literal["search", "finish", "refuse"] = "search"
    reasoning: str = ""
    tool: str = ""  # a registered tool name; empty for finish and refuse
    query: str = ""
    options: dict[str, bool | str | int] = {}


@dataclass
class PlanState:
    """What the planner needs to know about the run so far."""

    log: list[str] = field(default_factory=list)  # one compact line per earlier step
    evidence_count: int = 0
    steps_left: int = 1  # searches still allowed, counting this one
    failed_tools: set[str] = field(default_factory=set)  # tools that failed earlier this run

    @property
    def log_text(self) -> str:
        return "\n".join(self.log) if self.log else "(nothing yet)"


def check_plan(plan: Plan, registry: Registry, state: PlanState) -> str | None:
    """Rules the model must follow. Returns a problem the model can act on, or None.

    On success a search plan's options are replaced by the tool's validated, coerced options.
    """
    if plan.action == "refuse":
        if state.evidence_count:
            return "you already have sources, so you cannot refuse; choose finish or search"
        return None
    if plan.action == "finish":
        if not state.evidence_count:
            return "no sources have been found yet, so you cannot finish; choose search or refuse"
        return None
    plan.tool = plan.tool.strip().lower()
    available = [n for n in registry.names() if n not in state.failed_tools]
    if plan.tool in state.failed_tools:
        return f"tool {plan.tool!r} already failed this run; choose one of {available}"
    if plan.tool not in registry.names():
        return f"unknown tool {plan.tool!r}; choose one of {available}"
    plan.query = plan.query.strip()
    if not plan.query:
        return "query is empty"
    try:
        opts = registry.get(plan.tool).options_model.model_validate(plan.options)
    except ValidationError as e:
        return f"invalid options for {plan.tool}: {e.errors()[0]['msg']} ({plan.options})"
    plan.options = opts.model_dump()
    return None


def make_plan(
    question: str, registry: Registry, state: PlanState | None = None, attempts: int = 2
) -> Plan:
    state = state or PlanState()
    available = Registry([registry.get(n) for n in registry.names() if n not in state.failed_tools])
    unavailable = (
        f"Unavailable this run (failed earlier): {', '.join(sorted(state.failed_tools))}.\n"
        if state.failed_tools
        else ""
    )
    messages = [
        {
            "role": "system",
            "content": PLANNER_SYSTEM.format(
                tools=available.describe() or "(none available)",
                unavailable=unavailable,
                log=state.log_text,
                steps_left=state.steps_left,
            ),
        },
        {"role": "user", "content": question},
    ]
    started = time.monotonic()
    fell_back = False
    try:
        # Not strict: the options object is free-form, which strict schemas do not allow.
        plan, problem = llm.structured(
            messages,
            Plan,
            strict=False,
            check=lambda p: check_plan(p, registry, state),
            attempts=attempts,
            max_tokens=300,
        )
    except llm.StructuredOutputError as e:
        plan, problem = None, str(e)
    if plan is None or problem is not None:
        plan, fell_back = _default_plan(question, registry, state, problem), True
    tracing.add_step(
        tracing.PlanStep(
            duration_ms=int((time.monotonic() - started) * 1000),
            tools_offered=available.describe(),
            action=plan.action,
            steps_left=state.steps_left,
            log_shown=state.log_text,
            reasoning=plan.reasoning,
            tool=plan.tool,
            query=plan.query,
            options=plan.options,
            fell_back_to_default=fell_back,
            llm_attempts=tracing.take_attempts(),
        )
    )
    return plan


def _default_plan(question: str, registry: Registry, state: PlanState, problem: str | None) -> Plan:
    """What to do when the model's plan never validated: never fail the run."""
    why = f"planner output invalid ({problem})"
    if state.evidence_count:
        return Plan(action="finish", reasoning=f"{why}; answering from the evidence already found")
    usable = [n for n in registry.names() if n not in state.failed_tools]
    if not usable:
        return Plan(action="finish", reasoning=f"{why}; no tool is available")
    return Plan(
        action="search",
        reasoning=f"{why}; defaulting to {usable[0]}",
        tool=usable[0],
        query=question,
    )
