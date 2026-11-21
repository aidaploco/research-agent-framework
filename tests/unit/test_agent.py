"""The research loop: refinement, state, stopping rules, and single-pass mode."""

import pytest

from research_agent import agent
from research_agent.evidence import EvidenceStore
from research_agent.tools.base import ToolError
from research_agent.tools.registry import Registry
from research_agent.tracing import ToolStep, TraceStore
from tests.fakes import FakeTool, answer_json, finish_json, plan_json, refuse_json, source


def wiki(*results, **kw):
    return FakeTool(*results, name="wikipedia", id_prefix="W", **kw)


def arxiv(*results, **kw):
    return FakeTool(*results, name="arxiv", id_prefix="A", **kw)


def page(n, text="body text"):
    return source(title=f"Page {n}", url=f"https://w/{n}", text=text, tool="wikipedia")


def paper(n, text="abstract text"):
    return source(
        title=f"Paper {n}", url=f"https://a/{n}", text=text, tool="arxiv", native_id=f"arXiv:{n}"
    )


def run(fake_llm, trace_dir, tools, *replies, max_steps=3):
    fake_llm.queue(*replies)
    out = agent.answer("q", Registry(tools), TraceStore(trace_dir), max_steps=max_steps)
    return out, TraceStore(trace_dir).load("latest")


def tool_steps(trace):
    return [s for s in trace.steps if isinstance(s, ToolStep)]


def planner_prompt(fake_llm, n):
    return fake_llm.calls[n]["messages"][0]["content"]


# ---- multi-step: use more than one tool, and refine ----------------------------------


def test_uses_two_tools_across_steps_and_cites_both(fake_llm, trace_dir):
    w, a = wiki([page(1)]), arxiv([paper(1)])
    out, trace = run(
        fake_llm, trace_dir, [w, a],
        plan_json("wikipedia", "yield curve"),
        plan_json("arxiv", "yield curve recession"),
        finish_json(),
        answer_json(("A definition.", ["W1"]), ("A finding.", ["A1"])),
    )  # fmt: skip
    assert len(w.calls) == 1 and len(a.calls) == 1
    assert "- A definition. [W1]" in out and "- A finding. [A1]" in out
    assert [s.tool for s in tool_steps(trace)] == ["wikipedia", "arxiv"]
    assert trace.stop_reason == "finish" and trace.mode == "multi-step" and trace.max_steps == 3


def test_the_planner_sees_a_compact_log_not_source_text(fake_llm, trace_dir):
    secret = "SOURCE BODY THAT MUST NOT REACH THE PLANNER"
    run(
        fake_llm, trace_dir, [wiki([page(1, secret)]), arxiv([paper(1)])],
        plan_json("wikipedia", "yield curve"), finish_json(),
        answer_json(("A definition.", ["W1"])),
    )  # fmt: skip
    second = planner_prompt(fake_llm, 1)
    assert "step 1: search wikipedia query='yield curve' -> [W1] Page 1" in second
    assert secret not in second
    assert secret in fake_llm.calls[2]["messages"][1]["content"]  # but synthesis sees it


def test_an_empty_result_is_fed_back_and_the_planner_refines(fake_llm, trace_dir):
    w = wiki([], then=[[page(1)]])
    out, trace = run(
        fake_llm, trace_dir, [w],
        plan_json("wikipedia", "yield curve inversion recession indicator"),
        plan_json("wikipedia", "Yield curve"),
        finish_json(),
        answer_json(("Found it.", ["W1"])),
    )  # fmt: skip
    assert "-> no results" in planner_prompt(fake_llm, 1)
    assert [c[0] for c in w.calls] == ["yield curve inversion recession indicator", "Yield curve"]
    assert "- Found it. [W1]" in out
    assert [s.status for s in tool_steps(trace)] == ["empty", "ok"]


# ---- the rules code enforces ---------------------------------------------------------


def test_refusing_at_step_one_makes_no_tool_or_synthesis_call(fake_llm, trace_dir):
    w = wiki([page(1)])
    out, trace = run(fake_llm, trace_dir, [w], refuse_json("Not finance."))
    assert "## Cannot answer from sources\nNot finance." in out
    assert w.calls == [] and len(fake_llm.calls) == 1
    assert trace.outcome == "out_of_scope" and trace.stop_reason == "refuse"


def test_finishing_at_step_one_with_no_evidence_is_not_accepted(fake_llm, trace_dir):
    w = wiki([page(1)])
    out, trace = run(
        fake_llm, trace_dir, [w],
        finish_json(), plan_json("wikipedia", "Basel III"),  # first reply rejected, then repaired
        finish_json(),
        answer_json(("A fact.", ["W1"])),
    )  # fmt: skip
    assert len(w.calls) == 1 and "- A fact. [W1]" in out
    assert "no sources have been found yet" in trace.steps[0].llm_attempts[0].problem


def test_refusing_after_evidence_is_not_accepted(fake_llm, trace_dir):
    out, trace = run(
        fake_llm, trace_dir, [wiki([page(1)])],
        plan_json("wikipedia", "Basel III"),
        refuse_json(), finish_json(),  # the refusal is rejected, then repaired to finish
        answer_json(("A fact.", ["W1"])),
    )  # fmt: skip
    assert trace.outcome == "answered" and "- A fact. [W1]" in out


def test_a_repeated_search_is_never_executed_and_is_recorded_as_skipped(fake_llm, trace_dir):
    w = wiki([page(1)])
    out, trace = run(
        fake_llm, trace_dir, [w],
        plan_json("wikipedia", "Basel III"),
        plan_json("wikipedia", "  basel   III "),  # the same search, differently spelled
        answer_json(("A fact.", ["W1"])),
    )  # fmt: skip
    assert len(w.calls) == 1
    first, second = tool_steps(trace)
    assert (second.status, second.error) == ("skipped", "repeat of step 1")
    assert trace.stop_reason == "duplicate" and "- A fact. [W1]" in out  # still answers


def test_the_same_query_with_different_options_is_not_a_repeat(fake_llm, trace_dir):
    class WithOptions(FakeTool):
        pass

    a = arxiv([paper(1)], then=[[paper(2)]])
    a.options_model = __import__(
        "research_agent.tools.arxiv", fromlist=["ArxivOptions"]
    ).ArxivOptions
    run(
        fake_llm, trace_dir, [a],
        plan_json("arxiv", "credit risk"),
        plan_json("arxiv", "credit risk", options={"recent": True}),
        finish_json(),
        answer_json(("A fact.", ["A1"])),
    )  # fmt: skip
    assert len(a.calls) == 2 and a.calls[1][1] == {"recent": True}


def test_a_failed_tool_is_not_retried_and_not_offered_again(fake_llm, trace_dir):
    a = arxiv(ToolError("arxiv unavailable after 3 attempts (rate limited)"))
    w = wiki([page(1)])
    out, trace = run(
        fake_llm, trace_dir, [a, w],
        plan_json("arxiv", "credit risk"),
        plan_json("wikipedia", "Credit risk"),
        finish_json(),
        answer_json(("Credit risk is loss risk.", ["W1"])),
    )  # fmt: skip
    second = planner_prompt(fake_llm, 1)
    assert "Unavailable this run (failed earlier): arxiv." in second and "- arxiv:" not in second
    assert "FAILED (arxiv: arxiv unavailable after 3 attempts (rate limited))" in second
    assert len(a.calls) == 1
    assert (
        "The arxiv tool was unavailable (arxiv unavailable after 3 attempts (rate limited))." in out
    )
    assert "- Credit risk is loss risk. [W1]" in out


def test_choosing_a_failed_tool_again_is_repaired_by_code(fake_llm, trace_dir):
    a = arxiv(ToolError("down"))
    w = wiki([page(1)])
    run(
        fake_llm, trace_dir, [a, w],
        plan_json("arxiv", "x"),
        plan_json("arxiv", "y"), plan_json("wikipedia", "Y"),  # the first is rejected
        finish_json(),
        answer_json(("Fact.", ["W1"])),
    )  # fmt: skip
    assert len(a.calls) == 1 and "already failed" in fake_llm.calls[2]["messages"][-1]["content"]


def test_the_loop_stops_after_two_steps_with_nothing_new(fake_llm, trace_dir):
    w = wiki([page(1)], then=[[page(1)], []])  # new, then nothing new, then nothing at all
    _, trace = run(
        fake_llm, trace_dir, [w],
        plan_json("wikipedia", "a"), plan_json("wikipedia", "b"), plan_json("wikipedia", "c"),
        answer_json(("Fact.", ["W1"])),
        max_steps=6,
    )  # fmt: skip
    assert trace.stop_reason == "no_new_evidence" and len(w.calls) == 3
    assert "nothing new" in fake_llm.calls[2]["messages"][0]["content"]


def test_the_step_cap_ends_the_loop(fake_llm, trace_dir):
    w = wiki([page(1)], then=[[page(2)], [page(3)]])
    _, trace = run(
        fake_llm, trace_dir, [w],
        plan_json("wikipedia", "a"), plan_json("wikipedia", "b"),
        answer_json(("Fact.", ["W1"])),
        max_steps=2,
    )  # fmt: skip
    assert trace.stop_reason == "step_cap" and len(w.calls) == 2
    assert len([s for s in trace.steps if s.type == "plan"]) == 2  # no third planner call


def test_a_spent_evidence_budget_ends_the_loop(fake_llm, trace_dir, monkeypatch):
    monkeypatch.setattr(agent, "EvidenceStore", lambda: EvidenceStore(char_budget=2000))
    w = wiki([page(1, "x" * 5000)], then=[[page(2)]])
    _, trace = run(
        fake_llm, trace_dir, [w],
        plan_json("wikipedia", "a"),
        answer_json(("Fact.", ["W1"])),
    )  # fmt: skip
    assert trace.stop_reason == "budget" and len(w.calls) == 1
    step = tool_steps(trace)[0]
    assert (
        step.trimmed_for_budget == 1 and len(step.sources[0].text) < 2000
    )  # the trace shows what was kept


def test_max_steps_must_be_positive():
    with pytest.raises(ValueError):
        agent.answer("q", Registry([wiki()]), max_steps=0)


# ---- single-pass mode: the same loop, one search -------------------------------------


def test_single_pass_is_the_same_loop_with_one_search(fake_llm, trace_dir):
    w, a = wiki([page(1)]), arxiv([paper(1)])
    out, trace = run(
        fake_llm, trace_dir, [w, a],
        plan_json("wikipedia", "yield curve"),
        answer_json(("A definition.", ["W1"])),
        max_steps=1,
    )  # fmt: skip
    assert len(w.calls) == 1 and a.calls == []
    assert trace.mode == "single-pass" and trace.max_steps == 1 and trace.stop_reason == "step_cap"
    assert len(fake_llm.calls) == 2  # one planner call, one synthesis call
    assert "at most 1 more search" in planner_prompt(fake_llm, 0)


# ---- failures: fall back only when no later step could react -------------------------


def test_single_pass_falls_back_when_the_tool_is_down_and_says_so(fake_llm, trace_dir):
    a = arxiv(ToolError("arxiv unavailable after 3 attempts (rate limited)"))
    w = wiki([page(1)])
    out, trace = run(
        fake_llm, trace_dir, [a, w],
        plan_json("arxiv", "credit risk", options={}),
        answer_json(("Credit risk is loss risk.", ["W1"])),
        max_steps=1,
    )  # fmt: skip
    notice = out.split("\n\n")[0]
    assert (
        "The planned tool (arxiv) was unavailable" in notice
        and "come from wikipedia instead" in notice
    )
    assert [(s.tool, s.role) for s in tool_steps(trace)] == [
        ("arxiv", "planned"),
        ("wikipedia", "fallback"),
    ]


def test_in_multi_step_a_failure_is_left_to_the_planner_not_hidden_by_a_fallback(
    fake_llm, trace_dir
):
    a = arxiv(ToolError("down"))
    w = wiki([page(1)])
    run(
        fake_llm, trace_dir, [a, w],
        plan_json("arxiv", "credit risk"),
        plan_json("wikipedia", "Credit risk"),
        finish_json(),
        answer_json(("Fact.", ["W1"])),
    )  # fmt: skip
    assert [c[0] for c in w.calls] == [
        "Credit risk"
    ]  # the planner's query, not an automatic fallback


def test_the_last_step_may_fall_back(fake_llm, trace_dir):
    a = arxiv(ToolError("down"))
    w = wiki([page(1)])
    out, trace = run(
        fake_llm, trace_dir, [a, w],
        plan_json("arxiv", "credit risk"),
        answer_json(("Fact.", ["W1"])),
        max_steps=1,
    )  # fmt: skip
    assert "come from wikipedia instead" in out


def test_every_tool_down_is_reported_and_answered_honestly(fake_llm, trace_dir):
    out, trace = run(
        fake_llm, trace_dir, [arxiv(ToolError("arxiv down")), wiki(ToolError("wikipedia down"))],
        plan_json("arxiv", "x"),
        answer_json(refusal="Nothing was retrieved."),
        max_steps=1,
    )  # fmt: skip
    assert "No sources could be retrieved (arxiv: arxiv down; wikipedia: wikipedia down)." in out
    assert "## Sources\n(none)" in out and trace.outcome == "refused"


# ---- tools that return current figures (FRED) ----------------------------------------


def fred_tool(*results, **kw):
    t = FakeTool(*results, name="fred", id_prefix="F", **kw)
    t.live_data = True
    return t


def test_a_failed_live_data_tool_says_no_current_figure_is_available(fake_llm, trace_dir):
    out, trace = run(
        fake_llm, trace_dir,
        [fred_tool(ToolError("FRED_API_KEY is not set, so live economic data cannot be retrieved")), wiki([page(1)])],
        plan_json("fred", "UNRATE"),
        plan_json("wikipedia", "Unemployment rate"),
        finish_json(),
        answer_json(("The rate is measured by a survey.", ["W1"])),
    )  # fmt: skip
    assert out.startswith("## Notices")
    assert "The fred tool was unavailable (FRED_API_KEY is not set" in out
    assert "No current figure from fred is available, so none is stated here" in out
    assert "general background that may be out of date" in out
    assert "- The rate is measured by a survey. [W1]" in out  # the background is still given


def test_the_model_is_told_too_so_it_can_say_so(fake_llm, trace_dir):
    run(
        fake_llm, trace_dir, [fred_tool(ToolError("no key")), wiki([page(1)])],
        plan_json("fred", "UNRATE"), answer_json(refusal="No live data."),
        max_steps=1,
    )  # fmt: skip
    assert "No current figure from fred is available" in fake_llm.last_user_message


def test_the_notice_is_added_when_a_live_tool_is_the_one_falling_back(fake_llm, trace_dir):
    out, _ = run(
        fake_llm, trace_dir, [fred_tool(ToolError("no key")), wiki([page(1)])],
        plan_json("fred", "UNRATE"),
        answer_json(("Background.", ["W1"])),
        max_steps=1,
    )  # fmt: skip
    assert (
        "come from wikipedia instead" in out and "No current figure from fred is available" in out
    )


def test_an_ordinary_tool_failure_gets_no_live_data_clause(fake_llm, trace_dir):
    out, _ = run(
        fake_llm, trace_dir, [arxiv(ToolError("down")), wiki([page(1)])],
        plan_json("arxiv", "x"), finish_json(), answer_json(refusal="x"),
        max_steps=1,
    )  # fmt: skip
    assert "No current figure" not in out


def test_the_model_is_told_what_it_may_and_may_not_say_when_live_data_is_unavailable(
    fake_llm, trace_dir
):
    out, _ = run(
        fake_llm, trace_dir, [fred_tool(ToolError("no key")), wiki([page(1)])],
        plan_json("fred", "UNRATE"), plan_json("wikipedia", "Unemployment rate"), finish_json(),
        answer_json(("The rate is measured by a monthly survey.", ["W1"])),
    )  # fmt: skip
    told = fake_llm.last_user_message
    assert "Do NOT state the current value" in told and "how it is measured" in told
    assert "any specific value for any date" in told
    assert (
        "Do NOT state the current value" not in out
    )  # an instruction for the model, not the reader


def test_no_live_instruction_when_the_live_tool_worked(fake_llm, trace_dir):
    run(
        fake_llm, trace_dir, [fred_tool([source(url="https://f/1", tool="fred")])],
        plan_json("fred", "UNRATE"), finish_json(), answer_json(("A fact.", ["F1"])),
    )  # fmt: skip
    assert "Do NOT state" not in fake_llm.last_user_message
