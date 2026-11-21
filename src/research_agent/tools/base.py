"""The tool interface every research tool implements.

A tool is a self-contained module: it declares a name, a one-line description the
planner reads, and the options it accepts, and `run()` returns a list of `Source`.
Adding a tool means writing one subclass and registering it.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class ToolError(RuntimeError):
    """A tool could not complete (network failure, rate limit, bad response)."""


@dataclass(frozen=True)
class Source:
    """One retrieved item, with what a reader needs to verify it."""

    tool: str  # which tool produced it
    title: str
    url: str
    text: str
    id: str = ""  # stable identifier where one exists (e.g. an arXiv ID)
    meta: dict = field(default_factory=dict)  # e.g. authors, published date

    def citation(self) -> str:
        label = f"{self.title} ({self.id})" if self.id else self.title
        return f"{label} - {self.url}"


class Tool(ABC):
    name: str
    description: str  # shown to the planner: what this tool is good for
    options: dict[str, str] = {}  # optional run() keyword arguments -> description

    @abstractmethod
    def run(self, query: str, **options) -> list[Source]:
        """Search for `query` and return sources. Raise ToolError on failure."""
