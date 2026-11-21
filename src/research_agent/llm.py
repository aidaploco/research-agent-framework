"""Minimal Groq client (OpenAI-compatible chat completions endpoint)."""

import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from . import tracing
from .retry import RetriesExhausted, Retry, with_retries

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"

MAX_RETRY_WAIT = 30.0  # the longest we will sleep because the API told us to wait
MAX_PACING_WAIT = 65.0  # the longest we will sleep to let the token bucket refill
CHARS_PER_TOKEN = 3.2  # conservative: real English prose with figures is nearer 4
FALLBACK_TPM = 8000  # Groq's free tier for the default model, until a response says otherwise


class LLMError(RuntimeError):
    pass


class StructuredOutputError(LLMError):
    """The model did not return output matching the requested schema."""


T = TypeVar("T", bound=BaseModel)


@dataclass
class CallInfo:
    """What the last call cost: reported into the trace."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    waited_ms: int = 0  # pacing plus rate-limit waits
    retries: int = 0


_last = CallInfo()


def take_call_info() -> CallInfo:
    """The cost of the last chat() call, once."""
    global _last
    info, _last = _last, CallInfo()
    return info


class TokenBucket:
    """Tracks Groq's tokens-per-minute allowance from the response headers.

    The bucket refills continuously at limit/60 tokens a second, and `remaining` is what was
    left when the last response arrived, so what is available now can be computed and a call
    that would not fit can wait instead of failing. A request counts its prompt plus the
    output it may produce (max_completion_tokens), measured: a 413 reports exactly that sum.
    """

    def __init__(self) -> None:
        self.limit: int | None = None
        self.remaining: float | None = None
        self.at = 0.0

    def update(self, headers) -> None:
        try:
            self.limit = int(headers["x-ratelimit-limit-tokens"])
            self.remaining = float(headers["x-ratelimit-remaining-tokens"])
            self.at = time.monotonic()
        except (KeyError, ValueError, TypeError):
            pass

    def available(self) -> float | None:
        if self.remaining is None or not self.limit:
            return None
        refilled = (self.limit / 60) * (time.monotonic() - self.at)
        return min(float(self.limit), self.remaining + refilled)

    def wait_for(self, needed: int) -> float:
        """Seconds to wait before `needed` tokens fit (0 when they already do)."""
        avail = self.available()
        if avail is None or avail >= needed or not self.limit:
            return 0.0
        return (needed - avail) / (self.limit / 60) + 0.5  # a small margin


bucket = TokenBucket()


def estimate_tokens(messages: list[dict], max_out: int) -> int:
    chars = sum(len(str(m.get("content", ""))) for m in messages)
    return int(chars / CHARS_PER_TOKEN) + 40 + max_out


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def _retry_after(headers, attempt: int) -> float:
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return float(2 ** (attempt + 1))


def chat(messages, model=None, max_tokens=1500, retries=3, response_format=None):
    """Send a chat request and return the assistant text.

    Before sending, waits (up to MAX_PACING_WAIT) if the tokens-per-minute allowance cannot
    cover the request, and refuses a request that could never fit. On 429 and 5xx it waits for
    the API's Retry-After, but never longer than MAX_RETRY_WAIT: past that it raises with a
    clear message, so a daily quota cannot make the CLI sleep for hours.
    """
    global _last
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add a free Groq key."
        )
    payload = {
        "model": model or model_name(),
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "reasoning_effort": "low",
        "temperature": 0.2,
    }
    if response_format:
        payload["response_format"] = response_format
    info = CallInfo()
    _last = info
    needed = estimate_tokens(messages, max_tokens)
    limit = bucket.limit or FALLBACK_TPM
    if needed > limit:
        raise LLMError(
            f"This request needs about {needed} tokens but the free tier allows {limit} per "
            "minute, so it can never fit. Reduce the amount of evidence sent to the model."
        )
    wait = bucket.wait_for(needed)
    if wait > 0:
        wait = min(wait, MAX_PACING_WAIT)
        print(f"[pacing] waiting {wait:.1f}s for the token allowance", file=sys.stderr)
        time.sleep(wait)
        info.waited_ms += int(wait * 1000)

    def attempt(n: int) -> str:
        r = requests.post(
            GROQ_URL, json=payload, timeout=60, headers={"Authorization": f"Bearer {key}"}
        )
        bucket.update(r.headers)
        if r.status_code == 200:
            body = r.json()
            usage = body.get("usage") or {}
            info.prompt_tokens = int(usage.get("prompt_tokens", 0))
            info.completion_tokens = int(usage.get("completion_tokens", 0))
            return (body["choices"][0]["message"].get("content") or "").strip()
        if r.status_code in (429, 500, 502, 503) and n < retries - 1:
            wait = _retry_after(r.headers, n)
            if wait > MAX_RETRY_WAIT:
                raise LLMError(
                    f"Groq asks to wait {wait:.0f}s, longer than the {MAX_RETRY_WAIT:.0f}s this "
                    "client will wait. A per-minute limit clears sooner; a daily quota means "
                    f"trying again later. Original response: {r.text[:200]}"
                )
            raise Retry(f"HTTP {r.status_code}", wait=wait)
        if r.status_code == 400 and "called a tool" in r.text and n < retries - 1:
            # A glitch of some models (seen with gpt-oss-20b): it tries to call a tool although none
            # is offered, and the API rejects the reply. A fresh attempt usually works.
            raise Retry("the model attempted a tool call", wait=0.5)
        raise LLMError(f"Groq API error {r.status_code}: {r.text[:300]}")

    def waited(reason: str, wait: float) -> None:
        info.waited_ms += int(wait * 1000)
        info.retries += 1

    try:
        return with_retries("llm", attempt, attempts=retries, backoff=(2.0, 4.0), on_retry=waited)
    except RetriesExhausted as e:
        raise LLMError(f"Groq API: {e}") from e


def structured(
    messages: list[dict],
    model_cls: type[T],
    *,
    strict: bool = True,
    check: Callable[[T], str | None] | None = None,
    attempts: int = 2,
    max_tokens: int = 1500,
) -> tuple[T, str | None]:
    """Ask for output matching `model_cls` (a Pydantic model) and return a validated instance.

    The schema is sent as a JSON-schema response format, and the reply is validated with
    Pydantic regardless. `check` is an optional semantic test (for example "every evidence ID
    exists") returning a problem description or None. On a parse failure or a failed check the
    model is shown the problem and asked again, up to `attempts` times.

    Returns (instance, problem). `problem` is None when everything passed. It is the last
    check failure when the model never fixed it, so the caller can degrade gracefully
    (for example demote a claim) instead of losing the output. A reply that never parses
    raises StructuredOutputError.
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": model_cls.__name__.lower(),
            "strict": strict,
            "schema": model_cls.model_json_schema(),
        },
    }
    messages = list(messages)
    problem: str | None = None
    for attempt in range(attempts):
        sent = [dict(m) for m in messages]
        started = time.monotonic()
        reply = chat(messages, max_tokens=max_tokens, response_format=response_format)
        elapsed = int((time.monotonic() - started) * 1000)
        cost = take_call_info()
        retry_log = tracing.take_retries("llm")
        try:
            obj = model_cls.model_validate_json(reply)
        except (ValidationError, json.JSONDecodeError) as e:
            problem = f"reply did not match the schema: {str(e)[:300]}"
            obj = None
        else:
            problem = check(obj) if check else None
        tracing.record_attempt(
            tracing.LLMAttempt(
                purpose=model_cls.__name__.lower(),
                model=model_name(),
                messages=sent,
                reply=reply,
                problem=problem,
                duration_ms=elapsed,
                prompt_tokens=cost.prompt_tokens,
                completion_tokens=cost.completion_tokens,
                waited_ms=cost.waited_ms,
                retries=cost.retries,
                retry_log=retry_log,
            )
        )
        if obj is not None and problem is None:
            return obj, None
        if attempt < attempts - 1:
            messages += [
                {"role": "assistant", "content": reply},
                {
                    "role": "user",
                    "content": f"That was not valid: {problem}. Reply again with corrected JSON only.",
                },
            ]
    if obj is None:
        raise StructuredOutputError(problem or "no valid structured output")
    return obj, problem
