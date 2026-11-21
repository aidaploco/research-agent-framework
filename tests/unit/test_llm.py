import pytest
from pydantic import BaseModel

from research_agent import llm


class Thing(BaseModel):
    name: str


def test_chat_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="GROQ_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_structured_returns_validated_instance_and_sends_schema(fake_llm):
    fake_llm.queue({"name": "x"})
    obj, problem = llm.structured([], Thing)
    assert obj.name == "x" and problem is None
    schema = fake_llm.calls[0]["response_format"]["json_schema"]
    assert schema["name"] == "thing" and schema["strict"] is True
    assert schema["schema"]["properties"]["name"]["type"] == "string"


def test_structured_retries_check_failure_then_succeeds(fake_llm):
    fake_llm.queue({"name": "bad"}, {"name": "good"})
    obj, problem = llm.structured(
        [], Thing, check=lambda t: None if t.name == "good" else "name must be good"
    )
    assert obj.name == "good" and problem is None
    assert "name must be good" in fake_llm.last_user_message


def test_structured_returns_last_problem_when_check_never_passes(fake_llm):
    fake_llm.always({"name": "bad"})
    obj, problem = llm.structured([], Thing, check=lambda t: "still wrong")
    assert obj.name == "bad" and problem == "still wrong"
    assert len(fake_llm.calls) == 2  # one repair retry, not more


def test_structured_raises_when_reply_never_parses(fake_llm):
    fake_llm.always("nope")
    with pytest.raises(llm.StructuredOutputError):
        llm.structured([], Thing)
