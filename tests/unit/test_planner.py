import pytest

from research_agent.planner import Plan, PlanState, check_plan, make_plan
from research_agent.tools.registry import default_registry
from tests.fakes import finish_json, plan_json, refuse_json

REG = default_registry()
NO_EVIDENCE = PlanState()
WITH_EVIDENCE = PlanState(evidence_count=2, steps_left=2)


def search(**kw):
    return Plan(action="search", reasoning="r", **kw)


# ---- the rules code enforces ---------------------------------------------------------


def test_valid_search_passes_and_options_are_coerced():
    plan = search(tool=" ArXiv ", query=" credit risk ", options={"recent": "true"})
    assert check_plan(plan, REG, NO_EVIDENCE) is None
    assert (plan.tool, plan.query, plan.options) == ("arxiv", "credit risk", {"recent": True})


def test_refuse_is_allowed_only_before_any_evidence():
    assert check_plan(Plan(action="refuse"), REG, NO_EVIDENCE) is None
    problem = check_plan(Plan(action="refuse"), REG, WITH_EVIDENCE)
    assert "already have sources" in problem and "finish or search" in problem


def test_finish_is_allowed_only_after_some_evidence():
    assert check_plan(Plan(action="finish"), REG, WITH_EVIDENCE) is None
    problem = check_plan(Plan(action="finish"), REG, NO_EVIDENCE)
    assert "no sources" in problem and "search or refuse" in problem


@pytest.mark.parametrize(
    "plan, expected",
    [
        (search(tool="google", query="x"), "unknown tool"),
        (search(tool="wikipedia", query="  "), "query is empty"),
        (search(tool="arxiv", query="x", options={"recent": "maybe"}), "invalid options"),
        (search(tool="arxiv", query="x", options={"bogus": 1}), "invalid options"),
    ],
)
def test_search_problems_are_reported(plan, expected):
    assert expected in check_plan(plan, REG, NO_EVIDENCE)


def test_a_tool_that_failed_this_run_cannot_be_chosen_again():
    state = PlanState(failed_tools={"arxiv"})
    problem = check_plan(search(tool="arxiv", query="x"), REG, state)
    assert "already failed" in problem and "['wikipedia']" in problem
    assert check_plan(search(tool="wikipedia", query="x"), REG, state) is None


# ---- what the model is shown ---------------------------------------------------------


def test_the_prompt_shows_the_log_steps_left_and_actions(fake_llm):
    fake_llm.queue(plan_json(query="discount window"))
    state = PlanState(
        log=["step 1: search wikipedia query='Basel III' -> [W1] Basel III"],
        evidence_count=1,
        steps_left=2,
    )
    make_plan("q", REG, state)
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "step 1: search wikipedia query='Basel III' -> [W1] Basel III" in system
    assert "at most 2 more search" in system
    assert "- finish:" in system and "- refuse:" in system


def test_the_first_prompt_says_nothing_has_been_done(fake_llm):
    fake_llm.queue(plan_json())
    make_plan("q", REG)
    assert "(nothing yet)" in fake_llm.calls[0]["messages"][0]["content"]


def test_failed_tools_are_not_offered(fake_llm):
    fake_llm.queue(plan_json())
    make_plan("q", REG, PlanState(failed_tools={"arxiv"}))
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "Unavailable this run (failed earlier): arxiv." in system
    assert "- wikipedia:" in system and "- arxiv:" not in system


def test_plan_schema_is_not_strict_because_options_are_free_form(fake_llm):
    fake_llm.queue(plan_json(query="discount window"))
    assert make_plan("q", REG).query == "discount window"
    assert fake_llm.calls[0]["response_format"]["json_schema"]["strict"] is False


# ---- repair and fallback -------------------------------------------------------------


def test_finishing_with_no_evidence_is_rejected_and_repaired(fake_llm):
    fake_llm.queue(finish_json(), plan_json(query="discount window"))
    plan = make_plan("q", REG, NO_EVIDENCE)
    assert (plan.action, plan.query) == ("search", "discount window")
    assert "no sources have been found yet" in fake_llm.last_user_message


def test_refusing_after_evidence_is_rejected_and_repaired(fake_llm):
    fake_llm.queue(refuse_json(), finish_json())
    plan = make_plan("q", REG, WITH_EVIDENCE)
    assert plan.action == "finish"
    assert "you already have sources" in fake_llm.last_user_message


def test_repairs_bad_json_and_unknown_tools(fake_llm):
    fake_llm.queue("not json", plan_json(tool="google"), plan_json(tool="arxiv"))
    assert make_plan("q", REG, attempts=3).tool == "arxiv"
    assert "unknown tool" in fake_llm.last_user_message


def test_invalid_output_with_no_evidence_defaults_to_searching_the_first_tool(fake_llm):
    fake_llm.always("garbage")
    plan = make_plan("What is Basel III?", REG)
    assert (plan.action, plan.tool, plan.query) == ("search", "wikipedia", "What is Basel III?")
    assert "defaulting" in plan.reasoning


def test_invalid_output_with_evidence_defaults_to_finishing(fake_llm):
    fake_llm.always("garbage")
    plan = make_plan("q", REG, WITH_EVIDENCE)
    assert plan.action == "finish" and "evidence already found" in plan.reasoning


def test_invalid_output_with_every_tool_failed_finishes(fake_llm):
    fake_llm.always("garbage")
    plan = make_plan("q", REG, PlanState(failed_tools={"wikipedia", "arxiv"}))
    assert plan.action == "finish" and "no tool is available" in plan.reasoning


def test_the_prompt_tells_the_model_to_change_a_failed_query_and_to_finish_when_covered(fake_llm):
    fake_llm.queue(plan_json())
    make_plan("q", REG)
    system = fake_llm.calls[0]["messages"][0]["content"]
    assert "Repeating a query returns the same thing" in system
    assert "Choose finish as soon as the evidence covers" in system
