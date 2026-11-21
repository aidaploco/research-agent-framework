"""The model client's rate-limit handling: pacing, Retry-After caps, oversized requests."""

import pytest

from research_agent import llm
from tests.fakes import FakeHTTPResponse


class Clock:
    """A fake clock: sleep() advances monotonic()."""

    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(llm.time, "monotonic", c.monotonic)
    monkeypatch.setattr(llm.time, "sleep", c.sleep)
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(llm, "bucket", llm.TokenBucket())
    llm.take_call_info()
    return c


def ok(content="hi", prompt=100, completion=10, remaining=7000, limit=8000):
    return FakeHTTPResponse(
        {"choices": [{"message": {"content": content}}],
         "usage": {"prompt_tokens": prompt, "completion_tokens": completion}},
        headers={"x-ratelimit-limit-tokens": str(limit), "x-ratelimit-remaining-tokens": str(remaining)},
    )  # fmt: skip


def post_returning(monkeypatch, *responses):
    replies = iter(responses)
    sent = []

    def post(url, **kw):
        sent.append(kw["json"])
        return next(replies)

    monkeypatch.setattr(llm.requests, "post", post)
    return sent


MSG = [{"role": "user", "content": "hello"}]


def test_usage_is_reported_for_the_trace(monkeypatch):
    post_returning(monkeypatch, ok(prompt=123, completion=45))
    assert llm.chat(MSG) == "hi"
    info = llm.take_call_info()
    assert (info.prompt_tokens, info.completion_tokens) == (123, 45)
    assert llm.take_call_info().prompt_tokens == 0  # taken once


def test_the_estimate_counts_output_tokens_because_groq_does():
    # Measured: a 413 reported prompt tokens + max_completion_tokens as the request size.
    assert llm.estimate_tokens([{"content": "x" * 3200}], max_out=500) == 1000 + 40 + 500


def test_a_request_that_can_never_fit_fails_before_sending(monkeypatch):
    sent = post_returning(monkeypatch)
    huge = [{"role": "user", "content": "x" * 40_000}]
    with pytest.raises(llm.LLMError, match=r"can never fit"):
        llm.chat(huge, max_tokens=100)
    assert sent == []  # no wasted call


def test_waits_for_the_bucket_to_refill_instead_of_failing(monkeypatch, clock):
    sent = post_returning(monkeypatch, ok(remaining=500), ok())
    llm.chat(MSG)  # leaves only 500 tokens in the bucket
    big = [{"role": "user", "content": "x" * 9600}]  # about 3000 + 1500 output tokens needed
    llm.chat(big, max_tokens=1500)
    wait = clock.slept[0]
    assert 25 < wait < 40  # (4540 - ~500) / (8000/60) tokens a second, plus a margin
    assert llm.take_call_info().waited_ms == int(wait * 1000)
    assert len(sent) == 2


def test_no_wait_when_the_bucket_has_room(monkeypatch, clock):
    post_returning(monkeypatch, ok(remaining=7900), ok())
    llm.chat(MSG)
    llm.chat(MSG)
    assert clock.slept == []


def test_the_bucket_refills_with_time(monkeypatch, clock):
    post_returning(monkeypatch, ok(remaining=0), ok())
    llm.chat(MSG)
    clock.now += 60  # a full minute later the bucket is full again
    llm.chat(MSG)
    assert clock.slept == []


def test_retry_after_is_honoured_when_short(monkeypatch, clock):
    sent = post_returning(
        monkeypatch,
        FakeHTTPResponse(text="slow down", status=429, headers={"retry-after": "7"}),
        ok(),
    )
    assert llm.chat(MSG) == "hi"
    assert clock.slept == [7.0] and len(sent) == 2
    info = llm.take_call_info()
    assert info.retries == 1 and info.waited_ms == 7000


def test_a_retry_after_beyond_the_cap_fails_clearly_instead_of_sleeping(monkeypatch, clock):
    post_returning(
        monkeypatch,
        FakeHTTPResponse(text="daily quota", status=429, headers={"retry-after": "7200"}),
    )
    with pytest.raises(llm.LLMError, match=r"asks to wait 7200s.*30s"):
        llm.chat(MSG)
    assert clock.slept == []  # never slept for two hours


def test_a_413_is_reported_not_retried(monkeypatch):
    sent = post_returning(monkeypatch, FakeHTTPResponse(text="Request too large", status=413))
    with pytest.raises(llm.LLMError, match="413"):
        llm.chat(MSG)
    assert len(sent) == 1


def test_missing_rate_limit_headers_do_not_break_pacing(monkeypatch, clock):
    post_returning(
        monkeypatch, FakeHTTPResponse({"choices": [{"message": {"content": "x"}}]}), ok()
    )
    llm.chat(MSG)
    llm.chat(MSG)
    assert clock.slept == []
