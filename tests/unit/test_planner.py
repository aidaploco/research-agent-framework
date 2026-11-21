import pytest

from research_agent.planner import Plan, check_plan, make_plan
from research_agent.tools.registry import default_registry
from tests.fakes import plan_json

REG = default_registry()


def test_valid_plan_passes_and_options_are_coerced():
    plan = Plan(reasoning="r", tool=" ArXiv ", query=" credit risk ", options={"recent": "true"})
    assert check_plan(plan, REG) is None
    assert (plan.tool, plan.query, plan.options) == ("arxiv", "credit risk", {"recent": True})


def test_none_tool_needs_no_query():
    assert check_plan(Plan(reasoning="off topic", tool="none"), REG) is None


@pytest.mark.parametrize(
    "plan, expected",
    [
        (Plan(reasoning="r", tool="google", query="x"), "unknown tool"),
        (Plan(reasoning="r", tool="wikipedia", query="  "), "query is empty"),
        (
            Plan(reasoning="r", tool="arxiv", query="x", options={"recent": "maybe"}),
            "invalid options",
        ),
        (Plan(reasoning="r", tool="arxiv", query="x", options={"bogus": 1}), "invalid options"),
    ],
)
def test_check_plan_reports_problems(plan, expected):
    assert expected in check_plan(plan, REG)


def test_make_plan_sends_schema_and_returns_plan(fake_llm):
    fake_llm.queue(plan_json(query="discount window"))
    plan = make_plan("q", REG)
    assert plan.query == "discount window"
    assert fake_llm.calls[0]["response_format"]["json_schema"]["strict"] is False
    assert "wikipedia" in fake_llm.calls[0]["messages"][0]["content"]  # prompt lists the tools


def test_make_plan_repairs_after_bad_reply(fake_llm):
    fake_llm.queue("not json", plan_json(query="discount window"))
    assert make_plan("q", REG).query == "discount window"
    assert "not valid" in fake_llm.last_user_message


def test_make_plan_repairs_unknown_tool(fake_llm):
    fake_llm.queue(plan_json(tool="google"), plan_json(tool="arxiv"))
    assert make_plan("q", REG).tool == "arxiv"
    assert "unknown tool" in fake_llm.last_user_message


def test_make_plan_falls_back_after_repeated_failure(fake_llm):
    fake_llm.always("garbage")
    plan = make_plan("What is Basel III?", REG)
    assert (plan.tool, plan.query) == ("wikipedia", "What is Basel III?")
    assert "defaulting" in plan.reasoning
