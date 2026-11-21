import pytest
from pydantic import BaseModel, Field

from research_agent.tools.base import Source, Tool
from research_agent.tools.registry import Registry, default_registry


class DummyOptions(BaseModel):
    flag: bool = Field(False, description="a flag")


class Dummy(Tool):
    name = "dummy"
    description = "does nothing"
    id_prefix = "D"
    options_model = DummyOptions

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
    assert "option flag (bool): a flag" in text


def test_default_registry_has_wikipedia_arxiv_and_fred():
    assert default_registry().names() == ["wikipedia", "arxiv", "fred"]


def test_each_tool_declares_a_distinct_id_prefix():
    reg = default_registry()
    prefixes = [reg.get(n).id_prefix for n in reg.names()]
    assert prefixes == ["W", "A", "F"]


def test_citation_does_not_repeat_an_id_the_title_already_has():
    s = Source(
        tool="fred", title="FRED: Unemployment Rate (UNRATE)", url="u", text="t", native_id="UNRATE"
    )
    assert s.citation() == "FRED: Unemployment Rate (UNRATE) - u"
    s = Source(tool="arxiv", title="A paper", url="u", text="t", native_id="arXiv:1")
    assert s.citation() == "A paper (arXiv:1) - u"
