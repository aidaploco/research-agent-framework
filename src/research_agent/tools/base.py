"""The tool interface every research tool implements.

A tool is a self-contained module: it declares a name, a one-line description the
planner reads, and the options it accepts, and `run()` returns a list of `Source`.
Adding a tool means writing one subclass and registering it.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict, Field


class ToolError(RuntimeError):
    """A tool could not complete (network failure, rate limit, bad response)."""


class Source(BaseModel):
    """One retrieved item, with what a reader needs to verify it."""

    model_config = ConfigDict(frozen=True)

    tool: str  # which tool produced it
    title: str
    url: str
    text: str
    native_id: str = ""  # the source's own identifier where one exists (e.g. an arXiv ID)
    meta: dict = Field(default_factory=dict)  # e.g. authors, published date

    def citation(self) -> str:
        has_id = self.native_id and f"({self.native_id})" not in self.title
        label = f"{self.title} ({self.native_id})" if has_id else self.title
        return f"{label} - {self.url}"


class NoOptions(BaseModel):
    """Options model for tools that take none."""

    model_config = ConfigDict(extra="forbid")


class Tool(ABC):
    name: str
    id_prefix: str  # evidence IDs are this prefix plus a number: W1, A2, ...
    description: str  # shown to the planner: what this tool is good for
    options_model: type[BaseModel] = NoOptions  # validates and documents run() options
    # True for tools that return current figures. If one is unavailable, the answer must say no
    # current figure is available, and must not fill the gap from the model's memory.
    live_data: bool = False

    @abstractmethod
    def run(self, query: str, **options) -> list[Source]:
        """Search for `query` and return sources. Raise ToolError on failure."""
