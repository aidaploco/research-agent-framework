import pytest

from research_agent import planner
from research_agent.planner import PlanError, make_plan, parse_plan
from research_agent.tools.registry import default_registry

REG = default_registry()


def test_parse_valid_plan_with_prose_around_json():
    text = 'Sure! {"reasoning": "papers", "tool": "arxiv", "query": "credit risk", "options": {"recent": true}}'
    plan = parse_plan(text, REG)
    assert (plan.tool, plan.query, plan.options) == ("arxiv", "credit risk", {"recent": True})


def test_none_tool_needs_no_query():
    assert parse_plan('{"reasoning": "off topic", "tool": "none"}', REG).tool == "none"


@pytest.mark.parametrize(
    "text",
    [
        "no json here",
        '{"tool": "google", "query": "x"}',
        '{"tool": "wikipedia", "query": ""}',
        '{"tool": "wikipedia", "query": "x", "options": []}',
        '{"tool": "wikipedia", "query": ',
    ],
)
def test_parse_rejects_bad_plans(text):
    with pytest.raises(PlanError):
        parse_plan(text, REG)


def test_make_plan_retries_with_error_then_succeeds(monkeypatch):
    replies = iter(["oops", '{"reasoning": "r", "tool": "wikipedia", "query": "discount window"}'])
    seen = []

    def fake_chat(messages, **kw):
        seen.append(messages)
        return next(replies)

    monkeypatch.setattr(planner.llm, "chat", fake_chat)
    plan = make_plan("q", REG)
    assert plan.query == "discount window"
    assert "not valid" in seen[1][-1]["content"]


def test_make_plan_falls_back_after_repeated_failure(monkeypatch):
    monkeypatch.setattr(planner.llm, "chat", lambda *a, **k: "garbage")
    plan = make_plan("What is Basel III?", REG)
    assert (plan.tool, plan.query) == ("wikipedia", "What is Basel III?")
    assert "defaulting" in plan.reasoning
