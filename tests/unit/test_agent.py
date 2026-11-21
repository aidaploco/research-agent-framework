import pytest

from research_agent import agent
from research_agent.planner import Plan
from research_agent.tools.base import ToolError
from research_agent.tools.registry import Registry
from tests.fakes import FakeTool, answer_json, source


@pytest.fixture
def planned(monkeypatch):
    """Make the planner return a fixed plan, so these tests exercise the agent only."""

    def set_plan(**kw):
        plan = Plan(reasoning=kw.pop("reasoning", "r"), tool=kw.pop("tool"), query="q", **kw)
        monkeypatch.setattr(agent, "make_plan", lambda q, r: plan)

    return set_plan


def test_answer_renders_validated_claims_with_ids_and_urls(planned, fake_llm):
    planned(tool="fake")
    src = source(
        title="Paper",
        url="https://x/abs/1",
        text="abstract",
        native_id="arXiv:1",
        meta={"published": "2025-01-01"},
    )
    fake_llm.queue(answer_json(("A fact.", ["F1"]), inferences=["So it matters."]))
    out = agent.answer("q", Registry([FakeTool([src])]))
    assert "- A fact. [F1]" in out
    assert "[F1] Paper (arXiv:1) - https://x/abs/1" in out
    assert "- Inference: So it matters." in out
    assert "[F1] Paper (https://x/abs/1) published 2025-01-01" in fake_llm.last_user_message
    assert "## Notices" not in out


def test_out_of_scope_plan_refuses_without_any_llm_call(planned, fake_llm):
    planned(tool="none", reasoning="Not finance.")  # fake_llm has no replies queued
    out = agent.answer("best restaurant?", Registry([FakeTool(ToolError("must not run"))]))
    assert "## Cannot answer from sources\nNot finance." in out
    assert fake_llm.calls == []


def test_tool_down_with_no_substitute_says_so_in_a_notice(planned, fake_llm):
    planned(tool="fake")
    fake_llm.queue(answer_json(refusal="Nothing was retrieved."))
    out = agent.answer("q", Registry([FakeTool(ToolError("fake unavailable (ReadTimeout)"))]))
    assert out.startswith("## Notices")
    assert "No sources could be retrieved (fake: fake unavailable (ReadTimeout))." in out
    assert "No sources could be retrieved" in fake_llm.last_user_message  # the model is told too
    assert "## Sources\n(none)" in out


def two_tools(primary_error, fallback_result):
    primary = FakeTool(primary_error, name="arxiv", id_prefix="A")
    fallback = FakeTool(fallback_result, name="wikipedia", id_prefix="W")
    return primary, fallback, Registry([primary, fallback])


def test_falls_back_to_another_tool_and_discloses_the_substitution(planned, fake_llm):
    planned(tool="arxiv", options={"recent": True})
    primary, fallback, registry = two_tools(
        ToolError("arxiv unavailable after 3 attempts (rate limited)"),
        [source(title="Credit risk", url="https://en.wikipedia.org/wiki/Credit_risk", text="x")],
    )
    fake_llm.queue(answer_json(("Credit risk is risk of loss.", ["W1"])))
    out = agent.answer("q", registry)
    notice = out.split("\n\n")[0]
    assert "The planned tool (arxiv) was unavailable" in notice
    assert "arxiv unavailable after 3 attempts (rate limited)" in notice
    assert "come from wikipedia instead" in notice
    assert "- Credit risk is risk of loss. [W1]" in out
    assert "[W1] Credit risk - https://en.wikipedia.org/wiki/Credit_risk" in out
    assert "come from wikipedia instead" in fake_llm.last_user_message
    assert fallback.calls == [("q", {})]  # the planned tool's options are not passed on


def test_both_tools_down_reports_both_failures(planned, fake_llm):
    planned(tool="arxiv")
    _, _, registry = two_tools(ToolError("arxiv down"), ToolError("wikipedia down"))
    fake_llm.queue(answer_json(refusal="No evidence."))
    out = agent.answer("q", registry)
    assert "(arxiv: arxiv down; wikipedia: wikipedia down)" in out


def test_empty_fallback_result_is_not_presented_as_a_substitute(planned, fake_llm):
    planned(tool="arxiv")
    _, _, registry = two_tools(ToolError("arxiv down"), [])
    fake_llm.queue(answer_json(refusal="No evidence."))
    out = agent.answer("q", registry)
    assert "No sources could be retrieved (arxiv: arxiv down)." in out
    assert "come from" not in out


def test_no_fallback_when_the_planned_tool_works_but_finds_nothing(planned, fake_llm):
    planned(tool="arxiv")
    primary, fallback, registry = two_tools([], [source()])
    fake_llm.queue(answer_json(refusal="Nothing found."))
    out = agent.answer("q", registry)
    assert fallback.calls == [] and "## Notices" not in out
