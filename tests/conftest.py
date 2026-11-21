"""Shared fixtures. Every test runs with the network blocked."""

import socket

import pytest

from research_agent import llm
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that tries to open a network connection.

    Unit tests must use the fakes in tests/fakes.py (a fake LLM, fake tools, recorded HTTP
    responses). A live call to Groq, Wikipedia or arXiv would make them slow, flaky and
    dependent on a key, so it is an error here.
    """

    def blocked(*args, **kwargs):
        raise AssertionError("network access attempted in a unit test; use a fake")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


@pytest.fixture
def fake_llm(monkeypatch):
    """A scripted LLM installed in place of the Groq client."""
    fake = FakeLLM()
    monkeypatch.setattr(llm, "chat", fake.chat)
    return fake
