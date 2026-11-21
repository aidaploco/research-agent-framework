from research_agent import agent
from research_agent.tools.base import ToolError
from research_agent.tools.registry import Registry
from research_agent.trace_cli import main, render_trace
from research_agent.tracing import TraceStore
from tests.fakes import FakeTool, answer_json, plan_json, source


def make_run(fake_llm, trace_dir, tool_result, replies):
    """A full run: the planner reply comes from the fake LLM, like a real run."""
    fake_llm.queue(
        plan_json(tool="arxiv", query="credit risk", reasoning="a definition question"), *replies
    )
    tools = [
        FakeTool(tool_result, name="arxiv", id_prefix="A"),
        FakeTool([source(url="https://w/1")], name="wikipedia", id_prefix="W"),
    ]
    agent.answer("What research exists?", Registry(tools), TraceStore(trace_dir))


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
    assert "PLAN" in out and "chose: arxiv  query='credit risk'" in out
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
    fake_llm.queue(plan_json(tool="none", reasoning="Not finance."))
    agent.answer("best restaurant?", Registry([FakeTool([])]), TraceStore(trace_dir))
    text = render_trace(TraceStore(trace_dir).load("latest"))
    assert "chose: none" in text and "Outcome:  out_of_scope" in text and "Notices: none" in text
