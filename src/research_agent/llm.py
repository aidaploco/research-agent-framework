"""Minimal Groq client (OpenAI-compatible chat completions endpoint)."""

import json
import os
import time
from collections.abc import Callable
from typing import TypeVar

import requests
from pydantic import BaseModel, ValidationError

from . import tracing

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMError(RuntimeError):
    pass


class StructuredOutputError(LLMError):
    """The model did not return output matching the requested schema."""


T = TypeVar("T", bound=BaseModel)


def model_name() -> str:
    return os.environ.get("LLM_MODEL", DEFAULT_MODEL)


def chat(messages, model=None, max_tokens=1500, retries=3, response_format=None):
    """Send a chat request and return the assistant text. Retries on 429/5xx."""
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
    for attempt in range(retries):
        r = requests.post(
            GROQ_URL, json=payload, timeout=60, headers={"Authorization": f"Bearer {key}"}
        )
        if r.status_code == 200:
            return (r.json()["choices"][0]["message"].get("content") or "").strip()
        if r.status_code in (429, 500, 502, 503) and attempt < retries - 1:
            time.sleep(float(r.headers.get("retry-after", 2 ** (attempt + 1))))
            continue
        raise LLMError(f"Groq API error {r.status_code}: {r.text[:300]}")
    raise LLMError("Groq API: retries exhausted")


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
