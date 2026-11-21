import pytest

from research_agent.tools.base import Source, Tool
from research_agent.tools.registry import Registry, default_registry


class Dummy(Tool):
    name = "dummy"
    description = "does nothing"
    options = {"flag": "a flag"}

    def run(self, query, **options):
        return [Source(tool=self.name, title="t", url="u", text=query)]


def test_register_get_and_names():
    reg = Registry([Dummy()])
    assert reg.names() == ["dummy"]
    assert reg.get("dummy").run("q")[0].text == "q"


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError):
        Registry([Dummy(), Dummy()])


def test_describe_lists_tools_and_options():
    text = Registry([Dummy()]).describe()
    assert "dummy: does nothing" in text
    assert "option flag: a flag" in text


def test_default_registry_has_wikipedia_and_arxiv():
    assert default_registry().names() == ["wikipedia", "arxiv"]
