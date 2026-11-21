"""Structured traces: every run records why the agent did what it did.

A trace is a Pydantic model saved as one JSON file per run (the source of truth), plus a
SQLite index for queries. It records, in order:

- plan: the tools offered, the model's reasoning, the tool/query/options chosen, and every
  raw model reply (including replies that failed validation and were repaired);
- tool: the tool called, its input, status, error, and the raw sources it returned, with
  the evidence IDs they were given (fallbacks are separate tool steps);
- synthesis: the raw model replies, each validation problem, and the result with every claim
  sourced or unverified;
- the final rendered answer, the outcome, and the timing.

Recording is a no-op outside a run, so the pieces stay usable (and testable) on their own.
"""

import os
import sqlite3
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field

DEFAULT_DIR = ".traces"


class LLMAttempt(BaseModel):
    """One call to the model, exactly as sent and received."""

    purpose: str  # the schema asked for: "plan", "answer"
    model: str
    messages: list[dict]
    reply: str
    problem: str | None = None  # why this reply was rejected, if it was
    duration_ms: int = 0


class SourceRecord(BaseModel):
    """A retrieved source as the tool returned it, with the evidence ID it was given."""

    evidence_id: str
    tool: str
    title: str
    url: str
    native_id: str = ""
    published: str = ""
    text: str


class PlanStep(BaseModel):
    type: Literal["plan"] = "plan"
    index: int = 0
    duration_ms: int = 0
    tools_offered: str  # the tool descriptions the model chose between
    reasoning: str
    tool: str  # a tool name, or "none"
    query: str = ""
    options: dict = {}
    fell_back_to_default: bool = False  # the model's output never validated
    llm_attempts: list[LLMAttempt] = []


class ToolStep(BaseModel):
    type: Literal["tool"] = "tool"
    index: int = 0
    duration_ms: int = 0
    tool: str
    role: Literal["planned", "fallback"] = "planned"
    query: str
    options: dict = {}
    status: Literal["ok", "error", "empty"]
    error: str = ""
    sources: list[SourceRecord] = []  # the raw output


class SynthesisStep(BaseModel):
    type: Literal["synthesis"] = "synthesis"
    index: int = 0
    duration_ms: int = 0
    evidence_ids: list[str] = []  # what the model was allowed to cite
    llm_attempts: list[LLMAttempt] = []
    repaired: bool = False  # a first reply failed validation and a retry was sent
    sourced: list[dict] = []
    unverified: list[dict] = []
    inferences: list[str] = []
    caveats: list[str] = []
    refusal_reason: str = ""


Step = Annotated[PlanStep | ToolStep | SynthesisStep, Field(discriminator="type")]


class Trace(BaseModel):
    run_id: str
    started_at: str
    question: str
    outcome: Literal["answered", "refused", "out_of_scope", "error"] = "answered"
    error: str = ""
    duration_ms: int = 0
    model: str = ""
    notices: list[str] = []
    answer: str = ""  # the final text printed to the user
    steps: list[Step] = []


def new_run_id() -> str:
    # Microseconds, so ids sort in time order even for runs started in the same second.
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex[:4]


class Run:
    """The live recorder for one run. Steps and model attempts are added as they happen."""

    def __init__(self, question: str, model: str = ""):
        self.trace = Trace(
            run_id=new_run_id(),
            started_at=datetime.now(UTC).isoformat(timespec="seconds"),
            question=question,
            model=model,
        )
        self._t0 = time.monotonic()
        self._attempts: list[LLMAttempt] = []

    def add(self, step: PlanStep | ToolStep | SynthesisStep) -> None:
        step.index = len(self.trace.steps) + 1
        self.trace.steps.append(step)

    def record_attempt(self, attempt: LLMAttempt) -> None:
        self._attempts.append(attempt)

    def take_attempts(self) -> list[LLMAttempt]:
        """Model calls made since the last step, handed to the step that made them."""
        attempts, self._attempts = self._attempts, []
        return attempts

    def finish(self, outcome: str, answer: str = "", notices: list[str] | None = None) -> None:
        self.trace.outcome = outcome  # type: ignore[assignment]
        self.trace.answer = answer
        self.trace.notices = notices or []
        self.trace.duration_ms = int((time.monotonic() - self._t0) * 1000)


_current: ContextVar[Run | None] = ContextVar("current_run", default=None)


def current() -> Run | None:
    return _current.get()


def add_step(step: PlanStep | ToolStep | SynthesisStep) -> None:
    run = current()
    if run:
        run.add(step)


def record_attempt(attempt: LLMAttempt) -> None:
    run = current()
    if run:
        run.record_attempt(attempt)


def take_attempts() -> list[LLMAttempt]:
    run = current()
    return run.take_attempts() if run else []


# ---- storage -------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT, question TEXT, outcome TEXT, error TEXT,
    duration_ms INTEGER, model TEXT, planned_tool TEXT, tools_used TEXT, fallback INTEGER,
    n_steps INTEGER, n_sources INTEGER, n_sourced INTEGER, n_unverified INTEGER,
    n_repairs INTEGER, path TEXT);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT, idx INTEGER, type TEXT, tool TEXT, query TEXT, status TEXT,
    duration_ms INTEGER, n_sources INTEGER, detail TEXT, PRIMARY KEY (run_id, idx));
CREATE TABLE IF NOT EXISTS llm_calls (
    run_id TEXT, step_idx INTEGER, purpose TEXT, attempt INTEGER, problem TEXT,
    duration_ms INTEGER);
"""


class TraceStore:
    """JSON files (the record) plus a SQLite index (for queries) in one directory."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)

    @classmethod
    def from_env(cls) -> "TraceStore":
        return cls(os.environ.get("TRACE_DIR") or DEFAULT_DIR)

    @property
    def index_path(self) -> Path:
        return self.dir / "index.db"

    def path_for(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def save(self, trace: Trace) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(trace.run_id)
        path.write_text(trace.model_dump_json(indent=2))
        self.index(trace)
        return path

    def load(self, run_id: str) -> Trace:
        if run_id == "latest":
            files = sorted(self.dir.glob("*.json"))
            if not files:
                raise FileNotFoundError(f"no traces in {self.dir}")
            return Trace.model_validate_json(files[-1].read_text())
        return Trace.model_validate_json(self.path_for(run_id).read_text())

    def index(self, trace: Trace) -> None:
        tool_steps = [s for s in trace.steps if isinstance(s, ToolStep)]
        plan = next((s for s in trace.steps if isinstance(s, PlanStep)), None)
        synth = next((s for s in trace.steps if isinstance(s, SynthesisStep)), None)
        row = (
            trace.run_id, trace.started_at, trace.question, trace.outcome, trace.error,
            trace.duration_ms, trace.model, plan.tool if plan else None,
            ",".join(dict.fromkeys(s.tool for s in tool_steps)),
            int(any(s.role == "fallback" for s in tool_steps)),
            len(trace.steps), sum(len(s.sources) for s in tool_steps),
            len(synth.sourced) if synth else 0, len(synth.unverified) if synth else 0,
            sum(max(0, len(s.llm_attempts) - 1) for s in trace.steps
                if isinstance(s, (PlanStep, SynthesisStep))),
            str(self.path_for(trace.run_id)),
        )  # fmt: skip
        with self._connect() as db:
            db.execute("DELETE FROM runs WHERE run_id=?", (trace.run_id,))
            db.execute("DELETE FROM steps WHERE run_id=?", (trace.run_id,))
            db.execute("DELETE FROM llm_calls WHERE run_id=?", (trace.run_id,))
            db.execute("INSERT INTO runs VALUES (" + ",".join("?" * 16) + ")", row)
            for s in trace.steps:
                db.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?)", _step_row(trace, s))
                for n, a in enumerate(getattr(s, "llm_attempts", []), 1):
                    db.execute(
                        "INSERT INTO llm_calls VALUES (?,?,?,?,?,?)",
                        (trace.run_id, s.index, a.purpose, n, a.problem, a.duration_ms),
                    )

    def reindex(self) -> int:
        """Rebuild the index from the JSON files (the JSON is the source of truth)."""
        if self.index_path.exists():
            self.index_path.unlink()
        count = 0
        for f in sorted(self.dir.glob("*.json")):
            self.index(Trace.model_validate_json(f.read_text()))
            count += 1
        return count

    def _connect(self) -> sqlite3.Connection:
        self.dir.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.index_path)
        db.executescript(SCHEMA)
        return db

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Run one read-only SELECT against the index."""
        if not sql.lstrip().lower().startswith(("select", "with")):
            raise ValueError("only SELECT queries are allowed")
        uri = f"file:{self.index_path}?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            cur = db.execute(sql)
            return [d[0] for d in cur.description], cur.fetchall()


def _step_row(trace: Trace, s: PlanStep | ToolStep | SynthesisStep) -> tuple:
    if isinstance(s, PlanStep):
        return (trace.run_id, s.index, s.type, s.tool, s.query, "default" if s.fell_back_to_default else "ok", s.duration_ms, 0, s.reasoning)  # fmt: skip
    if isinstance(s, ToolStep):
        return (trace.run_id, s.index, s.type, s.tool, s.query, s.status, s.duration_ms, len(s.sources), s.error)  # fmt: skip
    return (trace.run_id, s.index, s.type, None, None, "repaired" if s.repaired else "ok", s.duration_ms, 0, s.refusal_reason)  # fmt: skip


@contextmanager
def start_run(question: str, store: TraceStore | None = None, model: str = "") -> Iterator[Run]:
    """Record one run. The trace is saved when the block ends, including when it raises."""
    run = Run(question, model)
    token = _current.set(run)
    try:
        yield run
    except BaseException as e:
        run.trace.error = f"{type(e).__name__}: {e}"
        run.finish("error", notices=run.trace.notices)
        raise
    finally:
        _current.reset(token)
        if run.trace.outcome != "error" and not run.trace.duration_ms:
            run.finish(run.trace.outcome, run.trace.answer, run.trace.notices)
        if store is not None:
            try:
                path = store.save(run.trace)
                print(f"[trace] {path}", file=sys.stderr)
            except OSError as e:  # a full disk must not lose the answer
                print(f"[trace] could not save: {e}", file=sys.stderr)
