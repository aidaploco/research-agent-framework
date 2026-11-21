"""Fakes used by the unit tests: a scripted LLM, a fake tool, and HTTP responses.

Nothing here touches the network. The autouse `no_network` fixture in conftest.py makes any
accidental live call an error.
"""

import json

from research_agent.tools.base import Source, Tool


class FakeLLM:
    """Stands in for the Groq client. Replies are scripted, and every call is recorded."""

    def __init__(self):
        self._replies: list = []
        self.calls: list[dict] = []  # {"messages": [...], **chat kwargs}

    def queue(self, *replies):
        """Queue replies: a str, a dict (sent as JSON), or an Exception to raise."""
        self._replies.extend(replies)
        return self

    def always(self, reply):
        self._always = reply
        return self

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        if self._replies:
            reply = self._replies.pop(0)
        elif hasattr(self, "_always"):
            reply = self._always
        else:
            raise AssertionError("FakeLLM received an unexpected call (no reply queued)")
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    @property
    def last_user_message(self) -> str:
        return self.calls[-1]["messages"][-1]["content"]


class FakeTool(Tool):
    """A tool that returns canned sources, or raises.

    `result` is a list of sources, an Exception to raise, or a callable(query, **options)
    returning either. `then` gives later results for later calls (the last one repeats).
    """

    id_prefix = "F"
    description = "fake"

    def __init__(self, result=None, name="fake", id_prefix="F", then=()):
        self.name = name
        self.id_prefix = id_prefix
        self._results = [[] if result is None else result, *then]
        self.calls: list[tuple[str, dict]] = []

    def run(self, query, **options):
        self.calls.append((query, options))
        result = self._results[min(len(self.calls), len(self._results)) - 1]
        if callable(result):
            result = result(query, **options)
        if isinstance(result, Exception):
            raise result
        return result


def source(title="Title", url="https://example.org/a", text="body", **kw) -> Source:
    return Source(tool=kw.pop("tool", "fake"), title=title, url=url, text=text, **kw)


def answer_json(*claims, inferences=(), caveats=(), refusal=""):
    """The JSON the model returns for synthesis: claims are (text, [evidence ids])."""
    return {
        "claims": [{"text": t, "evidence_ids": ids} for t, ids in claims],
        "inferences": list(inferences),
        "caveats": list(caveats),
        "refusal_reason": refusal,
    }


def plan_json(tool="wikipedia", query="q", reasoning="r", options=None, action="search"):
    """A planner reply. Default: search `tool` for `query`."""
    return {
        "action": action,
        "reasoning": reasoning,
        "tool": tool,
        "query": query,
        "options": options or {},
    }


def finish_json(reasoning="enough evidence"):
    return plan_json(action="finish", tool="", query="", reasoning=reasoning)


def refuse_json(reasoning="Not a finance question."):
    return plan_json(action="refuse", tool="", query="", reasoning=reasoning)


class FakeHTTPResponse:
    """A requests.Response look-alike."""

    def __init__(self, data=None, text=None, status=200, headers=None):
        self._data = data
        self.text = text if text is not None else json.dumps(data)
        self.status_code = status
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(str(self.status_code))
