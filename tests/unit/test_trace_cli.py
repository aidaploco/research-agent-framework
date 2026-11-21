from research_agent import agent
from research_agent.tools.base import ToolError
from research_agent.tools.registry import Registry
from research_agent.trace_cli import main, render_trace
from research_agent.tracing import TraceStore
from tests.fakes import FakeTool, answer_json, finish_json, plan_json, refuse_json, source


def make_run(fake_llm, trace_dir, tool_result, replies):
    """A full run: the planner reply comes from the fake LLM, like a real run."""
    fake_llm.queue(
        plan_json(tool="arxiv", query="credit risk", reasoning="a definition question"), *replies
    )
    tools = [
        FakeTool(tool_result, name="arxiv", id_prefix="A"),
        FakeTool([source(url="https://w/1")], name="wikipedia", id_prefix="W"),
    ]
    agent.answer("What research exists?", Registry(tools), TraceStore(trace_dir), max_steps=1)


def test_show_explains_each_decision(fake_llm, trace_dir, capsys):
    src = source(
        title="A paper",
        url="https://x/abs/1",
        text="Default prediction.",
        native_id="arXiv:1",
        meta={"published": "2025-01-05"},
    )
    make_run(fake_llm, trace_dir, [src], [answer_json(("Default prediction.", ["A1"]))])
    assert main(["--dir", str(trace_dir), "show", "latest"]) == 0
    out = capsys.readouterr().out
    assert "Question: What research exists?" in out and "Outcome:  answered" in out
    assert "PLAN" in out and "chose: search arxiv  query='credit risk'" in out
    assert "why: a definition question" in out
    assert "[A1] A paper arXiv:1 2025-01-05" in out and "https://x/abs/1" in out
    assert (
        "SYNTHESIS" in out and "may cite: A1" in out and "sourced [A1]: Default prediction." in out
    )
    assert "Final answer:" in out


def test_show_names_failures_fallbacks_and_unverified_claims(fake_llm, trace_dir, capsys):
    make_run(fake_llm, trace_dir, ToolError("arxiv unavailable (rate limited)"),
        [answer_json(("Made up.", ["W9"]))] * 2)  # fmt: skip
    main(["--dir", str(trace_dir), "show", "latest"])
    out = capsys.readouterr().out
    assert "ERROR: arxiv unavailable (rate limited)" in out and "[fallback]" in out
    assert "UNVERIFIED: Made up." in out and "a reply was rejected and repaired" in out
    assert "REJECTED:" in out and "The planned tool (arxiv) was unavailable" in out


def test_show_json_prints_the_raw_trace(fake_llm, trace_dir, capsys):
    make_run(fake_llm, trace_dir, [source()], [answer_json(("A fact.", ["A1"]))])
    main(["--dir", str(trace_dir), "show", "latest", "--json"])
    assert '"type": "plan"' in capsys.readouterr().out


def test_list_and_sql(fake_llm, trace_dir, capsys):
    make_run(fake_llm, trace_dir, [source()], [answer_json(("A fact.", ["A1"]))])
    assert main(["--dir", str(trace_dir), "list"]) == 0
    assert "answered" in capsys.readouterr().out
    assert (
        main(["--dir", str(trace_dir), "sql", "SELECT type, status FROM steps ORDER BY idx"]) == 0
    )
    assert "synthesis" in capsys.readouterr().out


def test_sql_rejects_writes_and_reports_errors(fake_llm, trace_dir, capsys):
    make_run(fake_llm, trace_dir, [source()], [answer_json(("A fact.", ["A1"]))])
    assert main(["--dir", str(trace_dir), "sql", "DROP TABLE runs"]) == 1
    assert "only SELECT" in capsys.readouterr().err
    assert main(["--dir", str(trace_dir), "show", "no-such-run"]) == 1


def test_reindex_command(fake_llm, trace_dir, capsys):
    make_run(fake_llm, trace_dir, [source()], [answer_json(("A fact.", ["A1"]))])
    assert main(["--dir", str(trace_dir), "reindex"]) == 0
    assert "reindexed 1 traces" in capsys.readouterr().out


def test_render_trace_handles_an_out_of_scope_run(fake_llm, trace_dir):
    fake_llm.queue(refuse_json("Not finance."))
    agent.answer("best restaurant?", Registry([FakeTool([])]), TraceStore(trace_dir), max_steps=1)
    text = render_trace(TraceStore(trace_dir).load("latest"))
    assert "chose: refuse" in text and "Outcome:  out_of_scope" in text and "Notices: none" in text


def test_show_a_multi_step_run_explains_what_the_planner_saw_and_why_it_stopped(
    fake_llm, trace_dir, capsys
):
    fake_llm.queue(
        plan_json("wikipedia", "Yield curve"),
        plan_json("arxiv", "yield curve recession"),
        finish_json("both a definition and papers found"),
        answer_json(("A definition.", ["W1"])),
    )
    tools = [
        FakeTool([source(title="Yield curve", url="https://w/1")], name="wikipedia", id_prefix="W"),
        FakeTool(
            [source(title="A paper", url="https://a/1", native_id="arXiv:1")],
            name="arxiv",
            id_prefix="A",
        ),
    ]
    agent.answer("q", Registry(tools), TraceStore(trace_dir), max_steps=3)
    main(["--dir", str(trace_dir), "show", "latest"])
    out = capsys.readouterr().out
    assert "Mode:     multi-step (up to 3 searches); stopped because: finish" in out
    assert "saw (earlier steps):" in out and "step 1: search wikipedia query='Yield curve'" in out
    assert "chose: finish" in out and "why: both a definition and papers found" in out
    assert "2 search(es) left" in out and "1 new" not in out.split("Step 1")[0]


def test_show_marks_skipped_steps_and_budget_trimming(fake_llm, trace_dir, capsys):
    fake_llm.queue(
        plan_json("wikipedia", "same"), plan_json("wikipedia", "same"), answer_json(refusal="x")
    )
    tool = FakeTool([source(url="https://w/1")], name="wikipedia", id_prefix="W")
    agent.answer("q", Registry([tool]), TraceStore(trace_dir), max_steps=3)
    main(["--dir", str(trace_dir), "show", "latest"])
    out = capsys.readouterr().out
    assert "SKIPPED, not run: repeat of step 1" in out and "stopped because: duplicate" in out
