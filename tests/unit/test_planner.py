import json

import pytest

from research_agent import llm
from research_agent.planner import Plan, check_plan, make_plan
from research_agent.tools.registry import default_registry

REG = default_registry()


def reply(**kw):
    return json.dumps({"reasoning": "r", "tool": "wikipedia", "query": "q", "options": {}, **kw})


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


def test_make_plan_sends_schema_and_returns_plan(monkeypatch):
    seen = {}

    def fake_chat(messages, **kw):
        seen.update(kw)
        return reply(query="discount window")

    monkeypatch.setattr(llm, "chat", fake_chat)
    plan = make_plan("q", REG)
    assert plan.query == "discount window"
    assert seen["response_format"]["json_schema"]["strict"] is False


def test_make_plan_repairs_after_bad_reply(monkeypatch):
    replies = iter(["not json", reply(query="discount window")])
    seen = []
    monkeypatch.setattr(llm, "chat", lambda m, **k: seen.append(m) or next(replies))
    assert make_plan("q", REG).query == "discount window"
    assert "not valid" in seen[1][-1]["content"]


def test_make_plan_repairs_unknown_tool(monkeypatch):
    replies = iter([reply(tool="google"), reply(tool="arxiv")])
    seen = []
    monkeypatch.setattr(llm, "chat", lambda m, **k: seen.append(m) or next(replies))
    assert make_plan("q", REG).tool == "arxiv"
    assert "unknown tool" in seen[1][-1]["content"]


def test_make_plan_falls_back_after_repeated_failure(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "garbage")
    plan = make_plan("What is Basel III?", REG)
    assert (plan.tool, plan.query) == ("wikipedia", "What is Basel III?")
    assert "defaulting" in plan.reasoning
