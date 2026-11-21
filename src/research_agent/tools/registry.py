"""Tool registry: the one place tools are listed, described and looked up."""

from .arxiv import ArxivTool
from .base import Tool
from .fred import FredTool
from .wikipedia import WikipediaTool


class Registry:
    def __init__(self, tools: list[Tool] | None = None):
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)

    def describe(self) -> str:
        """Text the planner reads to choose a tool."""
        lines = []
        for tool in self._tools.values():
            fields = tool.options_model.model_fields
            opts = "".join(
                f"\n    option {k} ({f.annotation.__name__}): {f.description}"
                for k, f in fields.items()
            )
            lines.append(f"- {tool.name}: {tool.description}{opts}")
        return "\n".join(lines)


def default_registry() -> Registry:
    return Registry([WikipediaTool(), ArxivTool(), FredTool()])
