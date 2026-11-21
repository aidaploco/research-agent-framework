"""Planner: asks the LLM which tool to call (one per step) and with what query."""

from pydantic import BaseModel, ValidationError

from . import llm
from .tools.registry import Registry

PLANNER_SYSTEM = """You plan research for a bank's research team. Choose ONE tool to call next.
Tools:
{tools}

Reply with a JSON object: reasoning (one sentence: why this tool and query), tool (a tool name,
or "none" if no tool can help), query (the search query), options (an object; {{}} if none).
Use "none" for questions unrelated to banking, finance, economics or their research literature.
Write queries as short keywords, not full sentences."""


class Plan(BaseModel):
    reasoning: str
    tool: str  # a registered tool name, or "none"
    query: str = ""
    options: dict[str, bool | str | int] = {}


def check_plan(plan: Plan, registry: Registry) -> str | None:
    """Semantic validation against the registry. Returns a problem the LLM can act on, or None.

    On success the options are replaced by the tool's validated, type-coerced options.
    """
    plan.tool = plan.tool.strip().lower()
    if plan.tool == "none":
        return None
    if plan.tool not in registry.names():
        return f"unknown tool {plan.tool!r}; choose one of {registry.names()} or 'none'"
    plan.query = plan.query.strip()
    if not plan.query:
        return "query is empty"
    try:
        opts = registry.get(plan.tool).options_model.model_validate(plan.options)
    except ValidationError as e:
        return f"invalid options for {plan.tool}: {e.errors()[0]['msg']} ({plan.options})"
    plan.options = opts.model_dump()
    return None


def make_plan(question: str, registry: Registry, attempts: int = 2) -> Plan:
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM.format(tools=registry.describe())},
        {"role": "user", "content": question},
    ]
    try:
        # Not strict: the options object is free-form, which strict schemas do not allow.
        plan, problem = llm.structured(
            messages,
            Plan,
            strict=False,
            check=lambda p: check_plan(p, registry),
            attempts=attempts,
            max_tokens=300,
        )
        if problem is None:
            return plan
    except llm.StructuredOutputError as e:
        problem = str(e)
    # Fall back to a safe default rather than failing the whole run.
    first = registry.names()[0]
    return Plan(
        reasoning=f"planner output invalid ({problem}); defaulting to {first}",
        tool=first,
        query=question,
    )
