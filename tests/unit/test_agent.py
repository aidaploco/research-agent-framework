from research_agent import agent
from research_agent.planner import Plan
from research_agent.tools.base import Source, Tool, ToolError
from research_agent.tools.registry import Registry


class FakeTool(Tool):
    name = "fake"
    description = "fake"

    def __init__(self, result):
        self.result = result

    def run(self, query, **options):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def run_agent(monkeypatch, plan, tool_result):
    prompts = []
    monkeypatch.setattr(agent, "make_plan", lambda q, r: plan)
    monkeypatch.setattr(agent.llm, "chat", lambda m, **k: prompts.append(m) or "ANSWER")
    out = agent.answer("q", Registry([FakeTool(tool_result)]))
    return out, prompts


def test_answer_cites_sources_with_ids_and_urls(monkeypatch):
    src = Source(
        "fake",
        "Paper",
        "https://x/abs/1",
        "abstract",
        id="arXiv:1",
        meta={"published": "2025-01-01"},
    )
    out, prompts = run_agent(monkeypatch, Plan("r", "fake", "q"), [src])
    assert "ANSWER" in out
    assert "[1] Paper (arXiv:1) - https://x/abs/1" in out
    assert "published 2025-01-01" in prompts[0][1]["content"]


def test_tool_failure_is_reported_to_the_model_not_raised(monkeypatch):
    out, prompts = run_agent(monkeypatch, Plan("r", "fake", "q"), ToolError("down"))
    assert "(none)" in out
    assert "The fake tool failed: down" in prompts[0][1]["content"]


def test_none_plan_skips_tools(monkeypatch):
    out, prompts = run_agent(monkeypatch, Plan("off topic", "none"), ToolError("must not run"))
    assert "(none)" in out
    assert "no sources were found" in prompts[0][1]["content"]
