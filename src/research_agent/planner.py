"""Planner: asks the LLM which tool to call (one per step) and with what query."""

import json
import re
from dataclasses import dataclass, field

from . import llm
from .tools.registry import Registry

PLANNER_SYSTEM = """You plan research for a bank's research team. Choose ONE tool to call next.
Tools:
{tools}

Reply with ONLY a JSON object:
{{"reasoning": "<one sentence: why this tool and query>", "tool": "<tool name, or \\"none\\" if no tool can help>", "query": "<search query>", "options": {{}}}}
Use "none" for questions unrelated to banking, finance, economics or their research literature.
Write queries as short keywords, not full sentences."""


@dataclass
class Plan:
    reasoning: str
    tool: str  # a registered tool name, or "none"
    query: str = ""
    options: dict = field(default_factory=dict)


class PlanError(ValueError):
    pass


def parse_plan(text: str, registry: Registry) -> Plan:
    """Validate the planner's reply. Raises PlanError with a message the LLM can act on."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise PlanError("reply contained no JSON object")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        raise PlanError(f"invalid JSON: {e}") from e
    tool = str(data.get("tool", "")).strip().lower()
    if tool != "none" and tool not in registry.names():
        raise PlanError(f"unknown tool {tool!r}; choose one of {registry.names()} or 'none'")
    query = str(data.get("query", "")).strip()
    if tool != "none" and not query:
        raise PlanError("query is empty")
    options = data.get("options")
    if options is None:
        options = {}
    if not isinstance(options, dict):
        raise PlanError("options must be an object")
    return Plan(str(data.get("reasoning", "")).strip(), tool, query, options)


def make_plan(question: str, registry: Registry, attempts: int = 2) -> Plan:
    messages = [
        {"role": "system", "content": PLANNER_SYSTEM.format(tools=registry.describe())},
        {"role": "user", "content": question},
    ]
    error = ""
    for _ in range(attempts):
        reply = llm.chat(messages, max_tokens=300)
        try:
            return parse_plan(reply, registry)
        except PlanError as e:
            error = str(e)
            messages += [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That was not valid: {error}. Reply with only the JSON.",
                },
            ]
    # Fall back to a safe default rather than failing the whole run.
    first = registry.names()[0]
    return Plan(f"planner output invalid ({error}); defaulting to {first}", first, question)
