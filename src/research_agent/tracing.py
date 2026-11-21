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
from contextlib import closing, contextmanager
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
    prompt_tokens: int = 0
    completion_tokens: int = 0
    waited_ms: int = 0  # time spent waiting for the token allowance or a rate limit
    retries: int = 0  # HTTP-level retries inside this call


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
    action: Literal["search", "finish", "refuse"] = "search"
    steps_left: int = 1  # how many searches the loop could still make, counting this one
    log_shown: str = ""  # the compact log of earlier steps the planner was shown
    reasoning: str
    tool: str  # a tool name; empty for finish and refuse
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
    # skipped: the loop refused to run it (a repeat, or a tool that already failed this run)
    status: Literal["ok", "error", "empty", "skipped"]
    error: str = ""  # the failure, or why it was skipped
    sources: list[SourceRecord] = []  # the raw output, as stored (after the evidence budget)
    new_sources: int = 0  # how many were not already in the evidence store
    dropped_for_budget: int = 0  # returned but left out because the evidence budget was spent
    trimmed_for_budget: int = 0  # kept, but cut short to fit


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
    mode: str = "multi-step"  # or "single-pass" (max_steps 1): the same loop, one search
    max_steps: int = 1
    stop_reason: str = ""  # why the loop stopped: finish, step_cap, duplicate, no_new_evidence, ...
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

SCHEMA_VERSION = 2  # bump when SCHEMA changes: an older index is rebuilt from the JSON files
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT, question TEXT, outcome TEXT, error TEXT,
    duration_ms INTEGER, model TEXT, mode TEXT, max_steps INTEGER, stop_reason TEXT,
    tools_used TEXT, n_plan_steps INTEGER, n_tool_calls INTEGER, n_skipped INTEGER,
    fallback INTEGER, n_sources INTEGER, n_sourced INTEGER, n_unverified INTEGER,
    n_repairs INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER, wait_ms INTEGER,
    path TEXT);
CREATE TABLE IF NOT EXISTS steps (
    run_id TEXT, idx INTEGER, type TEXT, tool TEXT, query TEXT, status TEXT,
    duration_ms INTEGER, n_sources INTEGER, detail TEXT, PRIMARY KEY (run_id, idx));
CREATE TABLE IF NOT EXISTS llm_calls (
    run_id TEXT, step_idx INTEGER, purpose TEXT, attempt INTEGER, problem TEXT,
    duration_ms INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER,
    waited_ms INTEGER, retries INTEGER);
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
        """Write the JSON record, then update the index. The index failing never loses the record."""
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.path_for(trace.run_id)
        path.write_text(trace.model_dump_json(indent=2))
        try:
            self.index(trace)
        except sqlite3.Error as e:
            print(f"[trace] index not updated ({e}); run `trace_cli reindex`", file=sys.stderr)
        return path

    def load(self, run_id: str) -> Trace:
        if run_id == "latest":
            files = sorted(self.dir.glob("*.json"))
            if not files:
                raise FileNotFoundError(f"no traces in {self.dir}")
            return Trace.model_validate_json(files[-1].read_text())
        return Trace.model_validate_json(self.path_for(run_id).read_text())

    def index(self, trace: Trace) -> None:
        with self._connect() as db:
            self._insert(db, trace)
            db.execute(
                "UPDATE runs SET path=? WHERE run_id=?",
                (str(self.path_for(trace.run_id)), trace.run_id),
            )

    def reindex(self) -> int:
        """Rebuild the index from the JSON files (the JSON is the source of truth)."""
        if self.index_path.exists():
            self.index_path.unlink()
        with self._connect():  # recreates the schema and repopulates from the JSON files
            pass
        return len(list(self.dir.glob("*.json")))

    def _version(self) -> int | None:
        try:
            with closing(sqlite3.connect(self.index_path)) as db:
                return db.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.DatabaseError:  # not a database at all
            return None

    def _connect(self) -> sqlite3.Connection:
        """Open the index, rebuilding it from the JSON files if it is missing or out of date."""
        self.dir.mkdir(parents=True, exist_ok=True)
        rebuild = not self.index_path.exists() or self._version() != SCHEMA_VERSION
        if rebuild:
            self.index_path.unlink(missing_ok=True)
        db = sqlite3.connect(self.index_path)
        if rebuild:
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            for f in sorted(self.dir.glob("*.json")):
                try:
                    self._insert(db, Trace.model_validate_json(f.read_text()))
                except ValueError:  # a file that is not a trace of this version: skip it
                    continue
            db.commit()
        return db

    @staticmethod
    def _insert(db: sqlite3.Connection, trace: Trace) -> None:
        plans = [s for s in trace.steps if isinstance(s, PlanStep)]
        tools = [s for s in trace.steps if isinstance(s, ToolStep)]
        calls = [s for s in trace.steps if isinstance(s, PlanStep | SynthesisStep)]
        attempts = [a for s in calls for a in s.llm_attempts]
        synth = next((s for s in trace.steps if isinstance(s, SynthesisStep)), None)
        ran = [s for s in tools if s.status != "skipped"]
        row = {
            "run_id": trace.run_id, "started_at": trace.started_at, "question": trace.question,
            "outcome": trace.outcome, "error": trace.error, "duration_ms": trace.duration_ms,
            "model": trace.model, "mode": trace.mode, "max_steps": trace.max_steps,
            "stop_reason": trace.stop_reason,
            "tools_used": ",".join(dict.fromkeys(s.tool for s in ran)),
            "n_plan_steps": len(plans), "n_tool_calls": len(ran),
            "n_skipped": len(tools) - len(ran),
            "fallback": int(any(s.role == "fallback" for s in tools)),
            "n_sources": sum(s.new_sources for s in tools),
            "n_sourced": len(synth.sourced) if synth else 0,
            "n_unverified": len(synth.unverified) if synth else 0,
            "n_repairs": sum(1 for a in attempts if a.problem),
            "prompt_tokens": sum(a.prompt_tokens for a in attempts),
            "completion_tokens": sum(a.completion_tokens for a in attempts),
            "wait_ms": sum(a.waited_ms for a in attempts),
            "path": "",
        }  # fmt: skip
        for table in ("runs", "steps", "llm_calls"):
            db.execute(f"DELETE FROM {table} WHERE run_id=?", (trace.run_id,))
        cols = ",".join(row)
        db.execute(
            f"INSERT INTO runs ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values())
        )  # noqa: S608
        for s in trace.steps:
            db.execute("INSERT INTO steps VALUES (?,?,?,?,?,?,?,?,?)", _step_row(trace, s))
            for n, a in enumerate(getattr(s, "llm_attempts", []), 1):
                db.execute(
                    "INSERT INTO llm_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (trace.run_id, s.index, a.purpose, n, a.problem, a.duration_ms,
                     a.prompt_tokens, a.completion_tokens, a.waited_ms, a.retries),
                )  # fmt: skip

    def query(self, sql: str) -> tuple[list[str], list[tuple]]:
        """Run one read-only SELECT against the index."""
        if not sql.lstrip().lower().startswith(("select", "with")):
            raise ValueError("only SELECT queries are allowed")
        self._connect().close()  # make sure the index exists and is current
        uri = f"file:{self.index_path}?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as db:
            cur = db.execute(sql)
            return [d[0] for d in cur.description], cur.fetchall()


def _step_row(trace: Trace, s: PlanStep | ToolStep | SynthesisStep) -> tuple:
    if isinstance(s, PlanStep):
        status = "default" if s.fell_back_to_default else "ok"
        return (trace.run_id, s.index, s.type, s.tool or None, s.query, f"{s.action}/{status}", s.duration_ms, 0, s.reasoning)  # fmt: skip
    if isinstance(s, ToolStep):
        return (trace.run_id, s.index, s.type, s.tool, s.query, s.status, s.duration_ms, s.new_sources, s.error)  # fmt: skip
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
            except (
                OSError,
                sqlite3.Error,
            ) as e:  # a full disk or a bad index must not lose the answer
                print(f"[trace] could not save: {e}", file=sys.stderr)
