"""Minimal Groq client (OpenAI-compatible chat completions endpoint)."""

import os
import time

import requests

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"


class LLMError(RuntimeError):
    pass


def chat(messages, model=None, max_tokens=1500, retries=3):
    """Send a chat request and return the assistant text. Retries on 429/5xx."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError(
            "GROQ_API_KEY is not set. Copy .env.example to .env and add a free Groq key."
        )
    payload = {
        "model": model or os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "reasoning_effort": "low",
        "temperature": 0.2,
    }
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
