import json
import sqlite3

import pytest

from research_agent import agent, tracing
from research_agent.tools.arxiv import ArxivOptions
from research_agent.tools.base import ToolError
from research_agent.tools.registry import Registry
from research_agent.tracing import (
    LLMAttempt,
    PlanStep,
    SynthesisStep,
    ToolStep,
    Trace,
    TraceStore,
)
from tests.fakes import FakeTool, answer_json, finish_json, plan_json, refuse_json, source


@pytest.fixture
def store(trace_dir):
    return TraceStore(trace_dir)


def attempt(**kw):
    return LLMAttempt(
        purpose="plan", model="m", messages=[{"role": "user", "content": "q"}], reply="{}", **kw
    )


# ---- the recorder -------------------------------------------------------------------


def test_recording_is_a_noop_outside_a_run():
    assert tracing.current() is None
    tracing.add_step(ToolStep(tool="t", query="q", status="ok"))  # must not raise
    tracing.record_attempt(attempt())
    assert tracing.take_attempts() == []


def test_steps_are_numbered_and_attempts_are_handed_to_the_next_step(store):
    with tracing.start_run("q", store) as run:
        tracing.record_attempt(attempt())
        tracing.record_attempt(attempt(problem="bad"))
        assert len(tracing.take_attempts()) == 2
        assert tracing.take_attempts() == []  # taken once
        tracing.add_step(ToolStep(tool="a", query="q", status="ok"))
        tracing.add_step(ToolStep(tool="b", query="q", status="empty"))
        run.finish("answered", "text")
    assert [s.index for s in run.trace.steps] == [1, 2]


def test_trace_is_saved_when_the_run_raises_and_the_error_is_reraised(store):
    with pytest.raises(RuntimeError, match="boom"):
        with tracing.start_run("q", store):
            raise RuntimeError("boom")
    trace = store.load("latest")
    assert trace.outcome == "error" and "RuntimeError: boom" in trace.error


def test_a_failed_save_does_not_lose_the_answer(tmp_path, capsys):
    blocked = tmp_path / "file"
    blocked.write_text("x")  # a file where the directory should be
    with tracing.start_run("q", TraceStore(blocked / "sub")) as run:
        run.finish("answered", "the answer")
    assert "could not save" in capsys.readouterr().err


# ---- storage and index --------------------------------------------------------------


def sample_trace():
    run = tracing.Run("What is X?", model="m")
    run.add(
        PlanStep(
            tools_offered="- w",
            reasoning="because",
            tool="wikipedia",
            query="X",
            llm_attempts=[attempt()],
        )
    )
    run.add(ToolStep(tool="wikipedia", query="X", status="ok", sources=[
        tracing.SourceRecord(evidence_id="W1", tool="wikipedia", title="X", url="u", text="body")
    ]))  # fmt: skip
    run.add(SynthesisStep(evidence_ids=["W1"], llm_attempts=[attempt(problem="bad id"), attempt()], repaired=True,
        sourced=[{"text": "t", "evidence_ids": ["W1"]}], unverified=[{"text": "u", "reason": "r"}]))  # fmt: skip
    run.finish("answered", "answer", ["a notice"])
    return run.trace


def test_save_load_roundtrip_keeps_every_step_type(store):
    trace = sample_trace()
    store.save(trace)
    loaded = store.load(trace.run_id)
    assert loaded == trace
    assert [type(s) for s in loaded.steps] == [
        PlanStep,
        ToolStep,
        SynthesisStep,
    ]  # all three kinds survive
    assert loaded.steps[1].sources[0].evidence_id == "W1"


def test_json_file_is_plain_readable_json(store):
    trace = sample_trace()
    data = json.loads(store.save(trace).read_text())
    assert data["steps"][0]["type"] == "plan" and data["steps"][0]["reasoning"] == "because"


def test_index_is_queryable_and_summarizes_the_run(store):
    store.save(sample_trace())
    cols, rows = store.query(
        "SELECT outcome, mode, tools_used, n_plan_steps, n_tool_calls, n_skipped, n_sources, "
        "n_sourced, n_unverified, n_repairs, fallback FROM runs"
    )
    assert rows == [("answered", "multi-step", "wikipedia", 1, 1, 0, 0, 1, 1, 1, 0)]
    _, steps = store.query("SELECT type, status FROM steps ORDER BY idx")
    assert steps == [("plan", "search/ok"), ("tool", "ok"), ("synthesis", "repaired")]
    _, calls = store.query("SELECT purpose, problem FROM llm_calls WHERE problem IS NOT NULL")
    assert calls == [("plan", "bad id")]


def test_saving_the_same_run_twice_does_not_duplicate_index_rows(store):
    trace = sample_trace()
    store.save(trace)
    store.save(trace)
    assert store.query("SELECT count(*) FROM runs")[1] == [(1,)]
    assert store.query("SELECT count(*) FROM steps")[1] == [(3,)]


def test_query_is_read_only(store):
    store.save(sample_trace())
    with pytest.raises(ValueError, match="only SELECT"):
        store.query("DELETE FROM runs")
    with pytest.raises(sqlite3.Error):
        store.query("SELECT 1; DROP TABLE runs")  # a second statement is rejected by sqlite
    assert store.query("SELECT count(*) FROM runs")[1] == [(1,)]


def test_reindex_rebuilds_the_index_from_the_json_files(store):
    store.save(sample_trace())
    store.index_path.unlink()
    assert store.reindex() == 1
    assert store.query("SELECT count(*) FROM runs")[1] == [(1,)]


# ---- an agent run produces a complete trace -----------------------------------------


def go(store, tools, max_steps=1):
    return agent.answer("q", Registry(tools), store, max_steps=max_steps)


def test_full_run_records_plan_tool_and_synthesis(fake_llm, store):
    src = source(title="Discount window", url="https://w/D", text="The rate is 100 basis points.")
    fake_llm.queue(
        plan_json(tool="fake", query="discount window", reasoning="a definition question"),
        answer_json(("The rate is 100 basis points.", ["F1"])),
    )
    agent.answer("What is the discount window?", Registry([FakeTool([src])]), store, max_steps=1)
    trace = store.load("latest")
    plan, call, synth = trace.steps
    assert trace.outcome == "answered" and trace.question == "What is the discount window?"
    assert (plan.tool, plan.query, plan.reasoning) == (
        "fake",
        "discount window",
        "a definition question",
    )
    assert plan.action == "search" and plan.steps_left == 1 and plan.log_shown == "(nothing yet)"
    assert "fake" in plan.tools_offered  # what the model chose between
    assert plan.llm_attempts[0].messages[0]["role"] == "system"  # the exact prompt
    assert call.status == "ok" and call.new_sources == 1 and call.sources[0].evidence_id == "F1"
    assert call.sources[0].text == "The rate is 100 basis points."  # raw output
    assert synth.evidence_ids == ["F1"] and synth.sourced[0]["evidence_ids"] == ["F1"]
    assert trace.mode == "single-pass" and trace.max_steps == 1 and trace.stop_reason == "step_cap"
    assert "[F1]" in trace.answer


def test_a_multi_step_run_records_every_plan_and_tool_step_in_order(fake_llm, store):
    fake_llm.queue(
        plan_json("fake", "first"), plan_json("fake", "second"), finish_json("enough"),
        answer_json(("Fact.", ["F1"])),
    )  # fmt: skip
    tool = FakeTool([source(url="https://w/1")], then=[[source(url="https://w/2")]])
    go(store, [tool], max_steps=3)
    trace = store.load("latest")
    assert [s.type for s in trace.steps] == ["plan", "tool", "plan", "tool", "plan", "synthesis"]
    assert [s.index for s in trace.steps] == [1, 2, 3, 4, 5, 6]
    assert [s.action for s in trace.steps if s.type == "plan"] == ["search", "search", "finish"]
    assert trace.steps[2].log_shown.startswith("step 1: search fake query='first'")
    assert trace.steps[2].steps_left == 2 and trace.stop_reason == "finish"


def test_repaired_synthesis_is_visible_in_the_trace(fake_llm, store):
    fake_llm.queue(
        plan_json("fake", "q"),
        answer_json(("Fact.", ["F9"])),  # unknown id, rejected
        answer_json(("Fact.", ["F1"])),
    )
    go(store, [FakeTool([source()])])
    synth = store.load("latest").steps[-1]
    assert synth.repaired and len(synth.llm_attempts) == 2
    assert "unknown evidence IDs" in synth.llm_attempts[0].problem
    assert synth.llm_attempts[1].problem is None


def test_demoted_claims_and_their_reasons_are_in_the_trace(fake_llm, store):
    fake_llm.queue(plan_json("fake", "q"))
    fake_llm.always(answer_json(("Unsupported.", ["F9"])))
    go(store, [FakeTool([source()])])
    synth = store.load("latest").steps[-1]
    assert synth.unverified[0]["text"] == "Unsupported." and "F9" in synth.unverified[0]["reason"]


def test_fallback_and_tool_errors_are_separate_steps(fake_llm, store):
    fake_llm.queue(
        plan_json("arxiv", "q", options={"recent": True}), answer_json(("Body.", ["W1"]))
    )
    primary = FakeTool(ToolError("arxiv unavailable (rate limited)"), name="arxiv", id_prefix="A")
    primary.options_model = ArxivOptions  # like the real arXiv tool, it accepts `recent`
    fallback = FakeTool([source(url="https://w/1")], name="wikipedia", id_prefix="W")
    go(store, [primary, fallback])
    trace = store.load("latest")
    first, second = [s for s in trace.steps if isinstance(s, ToolStep)]
    assert (first.tool, first.role, first.status) == ("arxiv", "planned", "error")
    assert "rate limited" in first.error and first.options == {"recent": True}
    assert (second.tool, second.role, second.status, second.options) == (
        "wikipedia",
        "fallback",
        "ok",
        {},
    )
    assert "come from wikipedia instead" in trace.notices[0]
    assert store.query("SELECT fallback FROM runs")[1] == [(1,)]


def test_a_skipped_repeat_is_in_the_trace_and_the_index(fake_llm, store):
    fake_llm.queue(plan_json("fake", "same"), plan_json("fake", "same"), answer_json(refusal="x"))
    go(store, [FakeTool([source()])], max_steps=3)
    trace = store.load("latest")
    skipped = [s for s in trace.steps if isinstance(s, ToolStep) and s.status == "skipped"]
    assert skipped and skipped[0].error == "repeat of step 1" and trace.stop_reason == "duplicate"
    assert store.query("SELECT n_tool_calls, n_skipped FROM runs")[1] == [(1, 1)]


def test_empty_tool_result_is_recorded_as_empty(fake_llm, store):
    fake_llm.queue(plan_json("fake", "q"), answer_json(refusal="Nothing found."))
    go(store, [FakeTool([])])
    trace = store.load("latest")
    assert [s.status for s in trace.steps if isinstance(s, ToolStep)] == ["empty"]
    assert trace.outcome == "refused"


def test_out_of_scope_run_has_a_plan_step_and_no_tool_or_synthesis(fake_llm, store):
    fake_llm.queue(refuse_json("Not finance."))
    go(store, [FakeTool([])])
    trace = store.load("latest")
    assert trace.outcome == "out_of_scope" and trace.stop_reason == "refuse"
    assert [s.type for s in trace.steps] == ["plan"]
    assert trace.steps[0].action == "refuse" and trace.steps[0].reasoning == "Not finance."
    assert len(fake_llm.calls) == 1  # only the planner call


def test_planner_fallback_to_default_is_flagged(fake_llm, store):
    fake_llm.queue("garbage", "garbage", answer_json(refusal="x"))
    go(store, [FakeTool([source()])])
    plan = store.load("latest").steps[0]
    assert plan.fell_back_to_default and "defaulting" in plan.reasoning
    assert len(plan.llm_attempts) == 2 and plan.llm_attempts[0].problem
    assert store.query("SELECT status FROM steps WHERE type='plan'")[1] == [("search/default",)]


def test_a_crash_mid_run_still_leaves_a_trace_with_the_steps_so_far(fake_llm, store):
    fake_llm.queue(plan_json("fake", "q"))

    class Crash(FakeTool):
        def run(self, query, **options):
            raise ValueError("unexpected")

    with pytest.raises(ValueError):
        go(store, [Crash()])
    trace = store.load("latest")
    assert trace.outcome == "error" and "ValueError: unexpected" in trace.error
    assert [s.type for s in trace.steps] == ["plan"]  # the plan made before the crash survives


def test_each_run_gets_its_own_trace_file(fake_llm, store):
    fake_llm.queue(refuse_json(), refuse_json())
    agent.answer("one", Registry([FakeTool([])]), store, max_steps=1)
    agent.answer("two", Registry([FakeTool([])]), store, max_steps=1)
    files = sorted(store.dir.glob("*.json"))
    assert len(files) == 2
    assert [Trace.model_validate_json(f.read_text()).question for f in files] == ["one", "two"]
    assert store.load("latest").question == "two"  # ids sort in time order


def test_token_usage_from_the_client_reaches_the_trace(fake_llm, store, monkeypatch):
    from research_agent import llm

    def fake_chat(messages, **kw):
        llm._last = llm.CallInfo(prompt_tokens=700, completion_tokens=40, waited_ms=2500, retries=1)
        return fake_llm.chat(messages, **kw)

    monkeypatch.setattr(llm, "chat", fake_chat)
    fake_llm.queue(refuse_json())
    go(store, [FakeTool([])])
    call = store.load("latest").steps[0].llm_attempts[0]
    assert (call.prompt_tokens, call.completion_tokens, call.waited_ms, call.retries) == (
        700,
        40,
        2500,
        1,
    )
    assert store.query("SELECT prompt_tokens, wait_ms FROM runs")[1] == [(700, 2500)]
