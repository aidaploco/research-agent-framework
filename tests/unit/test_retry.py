import pytest

from research_agent import retry, tracing
from research_agent.retry import RetriesExhausted, Retry, with_retries
from research_agent.tracing import ToolStep, TraceStore


@pytest.fixture
def sleeps(monkeypatch):
    waits = []
    monkeypatch.setattr(retry.time, "sleep", waits.append)
    return waits


def flaky(failures, reason="rate limited", **kw):
    calls = []

    def attempt(n):
        calls.append(n)
        if len(calls) <= failures:
            raise Retry(reason, **kw)
        return "ok"

    return attempt, calls


def test_returns_on_first_success_without_waiting(sleeps):
    attempt, calls = flaky(0)
    assert with_retries("t", attempt, attempts=3, backoff=(1.0, 2.0)) == "ok"
    assert calls == [0] and sleeps == []


def test_retries_with_the_backoff_schedule_and_the_last_value_repeats(sleeps):
    attempt, calls = flaky(3)
    assert with_retries("t", attempt, attempts=5, backoff=(1.0, 2.0)) == "ok"
    assert calls == [0, 1, 2, 3] and sleeps == [1.0, 2.0, 2.0]


def test_gives_up_after_the_last_attempt_with_the_last_reason_and_no_final_wait(sleeps):
    attempt, calls = flaky(99, reason="ReadTimeout")
    with pytest.raises(RetriesExhausted) as exc:
        with_retries("t", attempt, attempts=3, backoff=(1.0, 2.0))
    assert (exc.value.reason, exc.value.attempts) == ("ReadTimeout", 3)
    assert len(calls) == 3 and sleeps == [1.0, 2.0]  # no sleep after the final failure


def test_a_server_supplied_wait_overrides_the_backoff(sleeps):
    attempt, _ = flaky(1, wait=7.0)
    with_retries("t", attempt, attempts=3, backoff=(1.0,))
    assert sleeps == [7.0]


def test_other_exceptions_are_not_retried(sleeps):
    def attempt(n):
        raise ValueError("a bug, not a transient failure")

    with pytest.raises(ValueError):
        with_retries("t", attempt, attempts=3, backoff=(1.0,))
    assert sleeps == []


def test_on_retry_reports_each_reason_and_wait(sleeps):
    seen = []
    attempt, _ = flaky(2)
    with_retries(
        "t", attempt, attempts=3, backoff=(1.0, 2.0), on_retry=lambda r, w: seen.append((r, w))
    )
    assert seen == [("rate limited", 1.0), ("rate limited", 2.0)]


# ---- retries are visible in the trace -----------------------------------------------


def test_each_retry_is_noted_in_the_active_trace_under_its_label(sleeps, trace_dir):
    with tracing.start_run("q", TraceStore(trace_dir)) as run:
        attempt, _ = flaky(2)
        with_retries("arxiv", attempt, attempts=3, backoff=(2.0, 4.0))
        with_retries("llm", flaky(1)[0], attempts=2, backoff=(1.0,))
        assert run.take_retries("arxiv") == [
            "attempt 1 of 3 failed (rate limited); waiting 2s",
            "attempt 2 of 3 failed (rate limited); waiting 4s",
        ]
        assert run.take_retries("arxiv") == []  # taken once
        assert len(run.take_retries("llm")) == 1
        run.finish("answered")


def test_noting_a_retry_outside_a_run_is_harmless(sleeps):
    attempt, _ = flaky(1)
    assert with_retries("t", attempt, attempts=2, backoff=(1.0,)) == "ok"
    assert tracing.take_retries("t") == []


def test_a_tool_steps_trace_lists_its_retries(fake_llm, trace_dir, monkeypatch):
    from research_agent import agent
    from research_agent.tools import arxiv
    from research_agent.tools.registry import Registry
    from tests.fakes import FakeHTTPResponse, answer_json, plan_json

    monkeypatch.setattr(arxiv, "_space_requests", lambda: None)
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    feed = (
        '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/1.1</id>'
        "<title>T</title><published>2025-01-01T00:00:00Z</published><summary>S</summary></entry></feed>"
    )
    replies = iter([FakeHTTPResponse(text="Rate exceeded."), FakeHTTPResponse(text=feed)])
    monkeypatch.setattr(arxiv.requests, "get", lambda *a, **k: next(replies))
    fake_llm.queue(plan_json("arxiv", "credit risk"), answer_json(refusal="x"))
    store = TraceStore(trace_dir)
    agent.answer("q", Registry([arxiv.ArxivTool()]), store, max_steps=1)
    step = next(s for s in store.load("latest").steps if isinstance(s, ToolStep))
    assert step.status == "ok"
    assert step.retries == ["attempt 1 of 3 failed (rate limited); waiting 2s"]


def test_model_call_retries_are_listed_on_the_attempt(trace_dir, monkeypatch):
    from research_agent import llm
    from tests.fakes import FakeHTTPResponse
    from tests.unit.test_llm_limits import ok

    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setattr(llm, "bucket", llm.TokenBucket())
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    replies = iter([FakeHTTPResponse(text="slow", status=429, headers={"retry-after": "3"}),
                    ok(content='{"name": "x"}')])  # fmt: skip
    monkeypatch.setattr(llm.requests, "post", lambda *a, **k: next(replies))
    with tracing.start_run("q", TraceStore(trace_dir)) as run:
        from pydantic import BaseModel

        class Thing(BaseModel):
            name: str

        llm.structured([{"role": "user", "content": "hi"}], Thing)
        call = tracing.take_attempts()[0]
        assert call.retries == 1 and call.waited_ms == 3000
        assert call.retry_log == ["attempt 1 of 3 failed (HTTP 429); waiting 3s"]
        run.finish("answered")
