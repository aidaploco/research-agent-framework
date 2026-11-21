import json

import pytest
from pydantic import BaseModel

from research_agent import llm


class Thing(BaseModel):
    name: str


def test_chat_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="GROQ_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_structured_returns_validated_instance_and_sends_schema(monkeypatch):
    seen = {}

    def fake_chat(messages, **kw):
        seen.update(kw)
        return json.dumps({"name": "x"})

    monkeypatch.setattr(llm, "chat", fake_chat)
    obj, problem = llm.structured([], Thing)
    assert obj.name == "x" and problem is None
    schema = seen["response_format"]["json_schema"]
    assert schema["name"] == "thing" and schema["strict"] is True
    assert schema["schema"]["properties"]["name"]["type"] == "string"


def test_structured_retries_check_failure_then_succeeds(monkeypatch):
    replies = iter([json.dumps({"name": "bad"}), json.dumps({"name": "good"})])
    seen = []
    monkeypatch.setattr(llm, "chat", lambda m, **k: seen.append(m) or next(replies))
    obj, problem = llm.structured(
        [], Thing, check=lambda t: None if t.name == "good" else "name must be good"
    )
    assert obj.name == "good" and problem is None
    assert "name must be good" in seen[1][-1]["content"]


def test_structured_returns_last_problem_when_check_never_passes(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: json.dumps({"name": "bad"}))
    obj, problem = llm.structured([], Thing, check=lambda t: "still wrong")
    assert obj.name == "bad" and problem == "still wrong"


def test_structured_raises_when_reply_never_parses(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: "nope")
    with pytest.raises(llm.StructuredOutputError):
        llm.structured([], Thing)
