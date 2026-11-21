import json

from research_agent import agent, llm
from research_agent.planner import Plan
from research_agent.tools.base import Source, Tool, ToolError
from research_agent.tools.registry import Registry


class FakeTool(Tool):
    name = "fake"
    id_prefix = "F"
    description = "fake"

    def __init__(self, result):
        self.result = result

    def run(self, query, **options):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run_agent(monkeypatch, plan, tool_result, reply):
    prompts = []
    monkeypatch.setattr(agent, "make_plan", lambda q, r: plan)
    monkeypatch.setattr(llm, "chat", lambda m, **k: prompts.append(m) or reply)
    return agent.answer("q", Registry([FakeTool(tool_result)])), prompts


def claims_reply(*claims):
    return json.dumps(
        {
            "claims": [{"text": t, "evidence_ids": ids} for t, ids in claims],
            "inferences": ["So it matters."],
            "caveats": [],
            "refusal_reason": "",
        }
    )


def test_answer_renders_validated_claims_with_ids_and_urls(monkeypatch):
    src = Source(
        tool="fake",
        title="Paper",
        url="https://x/abs/1",
        text="abstract",
        native_id="arXiv:1",
        meta={"published": "2025-01-01"},
    )
    out, prompts = run_agent(
        monkeypatch,
        Plan(reasoning="r", tool="fake", query="q"),
        [src],
        claims_reply(("A fact.", ["F1"])),
    )
    assert "- A fact. [F1]" in out
    assert "[F1] Paper (arXiv:1) - https://x/abs/1" in out
    assert "- Inference: So it matters." in out
    assert "[F1] Paper (https://x/abs/1) published 2025-01-01" in prompts[0][1]["content"]


def test_tool_failure_is_reported_to_the_model_not_raised(monkeypatch):
    out, prompts = run_agent(
        monkeypatch,
        Plan(reasoning="r", tool="fake", query="q"),
        ToolError("down"),
        json.dumps(
            {"claims": [], "inferences": [], "caveats": [], "refusal_reason": "Tool was down."}
        ),
    )
    assert "Tool was down." in out and "## Sources\n(none)" in out
    assert "The fake tool failed: down" in prompts[0][1]["content"]


def test_out_of_scope_plan_refuses_without_any_llm_call(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no LLM call expected")

    monkeypatch.setattr(
        agent, "make_plan", lambda q, r: Plan(reasoning="Not finance.", tool="none")
    )
    monkeypatch.setattr(llm, "chat", boom)
    out = agent.answer("best restaurant?", Registry([FakeTool(ToolError("must not run"))]))
    assert "## Cannot answer from sources\nNot finance." in out
