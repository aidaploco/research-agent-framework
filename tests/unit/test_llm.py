import pytest

from research_agent import llm


def test_chat_requires_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(llm.LLMError, match="GROQ_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])
