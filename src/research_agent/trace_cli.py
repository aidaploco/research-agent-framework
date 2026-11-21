"""Read traces: `python -m research_agent.trace_cli list | show <id|latest> | sql "<SELECT>" | reindex`."""

import argparse
import sys
import textwrap

from .tracing import PlanStep, SynthesisStep, ToolStep, Trace, TraceStore

WIDTH = 100


def _wrap(text: str, indent: str) -> str:
    return textwrap.fill(
        " ".join(str(text).split()), WIDTH, initial_indent=indent, subsequent_indent=indent
    )


def _attempts(step: PlanStep | SynthesisStep) -> list[str]:
    lines = []
    for n, a in enumerate(step.llm_attempts, 1):
        status = f"REJECTED: {a.problem}" if a.problem else "accepted"
        cost = f", {a.prompt_tokens}+{a.completion_tokens} tokens" if a.prompt_tokens else ""
        wait = f", waited {a.waited_ms} ms" if a.waited_ms else ""
        retry = f", {a.retries} retries" if a.retries else ""
        lines.append(
            f"    model call {n} ({a.model}, {a.duration_ms} ms{cost}{wait}{retry}): {status}"
        )
        if a.problem:
            lines.append(_wrap(f"reply was: {a.reply[:300]}", "      "))
    return lines


def render_trace(t: Trace) -> str:
    """The run as a narrative: what was decided, on what evidence, and why."""
    out = [
        f"Run {t.run_id}   {t.started_at}   {t.duration_ms} ms   model {t.model}",
        f"Question: {t.question}",
        f"Outcome:  {t.outcome}" + (f"  ({t.error})" if t.error else ""),
        f"Mode:     {t.mode} (up to {t.max_steps} search{'es' if t.max_steps != 1 else ''}); "
        f"stopped because: {t.stop_reason or 'n/a'}",
        "",
    ]
    for s in t.steps:
        if isinstance(s, PlanStep):
            default = (
                "  [model output never validated: a default was used]"
                if s.fell_back_to_default
                else ""
            )
            chose = s.action + (f" {s.tool}  query={s.query!r}" if s.action == "search" else "")
            if s.options:
                chose += f"  options={s.options}"
            out += [
                f"Step {s.index}  PLAN ({s.duration_ms} ms, {s.steps_left} search(es) left){default}",
                "  saw (earlier steps):",
                *[_wrap(line, "    ") for line in s.log_shown.splitlines()],
                f"  chose: {chose}",
                _wrap(f"why: {s.reasoning}", "  "),
                *_attempts(s),
            ]  # fmt: skip
        elif isinstance(s, ToolStep):
            what = {
                "ok": f"{len(s.sources)} sources ({s.new_sources} new)",
                "empty": "no results",
                "error": f"ERROR: {s.error}",
                "skipped": f"SKIPPED, not run: {s.error}",
            }[s.status]
            budget = ""
            if s.dropped_for_budget or s.trimmed_for_budget:
                budget = f"  [evidence budget: {s.trimmed_for_budget} trimmed, {s.dropped_for_budget} dropped]"
            out += [
                f"Step {s.index}  TOOL {s.tool} [{s.role}] ({s.duration_ms} ms)  query={s.query!r}"
                + (f" options={s.options}" if s.options else ""),
                f"  result: {what}{budget}",
            ]  # fmt: skip
            for src in s.sources:
                ident = f" {src.native_id}" if src.native_id else ""
                date = f" {src.published}" if src.published else ""
                out.append(
                    f"    [{src.evidence_id}] {src.title}{ident}{date}  ({len(src.text)} chars)  {src.url}"
                )
        else:
            out += [
                f"Step {s.index}  SYNTHESIS ({s.duration_ms} ms)  may cite: {', '.join(s.evidence_ids) or 'nothing'}",
                f"  {len(s.sourced)} sourced, {len(s.unverified)} unverified"
                + ("  [a reply was rejected and repaired]" if s.repaired else ""),
                *_attempts(s),
            ]  # fmt: skip
            out += [
                _wrap(f"sourced [{', '.join(c['evidence_ids'])}]: {c['text']}", "    ")
                for c in s.sourced
            ]
            out += [_wrap(f"UNVERIFIED: {u['text']} ({u['reason']})", "    ") for u in s.unverified]
            if s.refusal_reason:
                out.append(_wrap(f"refusal: {s.refusal_reason}", "    "))
    out += [
        "",
        "Notices:" if t.notices else "Notices: none",
        *[_wrap(n, "  - ") for n in t.notices],
    ]
    out += ["", "Final answer:", textwrap.indent(t.answer, "  ")]
    return "\n".join(out)


def _table(cols: list[str], rows: list[tuple]) -> str:
    cells = [[str(c) if c is not None else "" for c in r] for r in rows]
    widths = [max([len(c)] + [len(r[i]) for r in cells]) for i, c in enumerate(cols)]

    def line(r: list[str]) -> str:
        return "  ".join(v.ljust(w) for v, w in zip(r, widths, strict=True))

    return "\n".join([line(cols), line(["-" * w for w in widths]), *[line(r) for r in cells]])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="trace_cli", description=__doc__)
    ap.add_argument("--dir", help="trace directory (default: $TRACE_DIR or .traces)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="recent runs")
    ls.add_argument("-n", type=int, default=20)
    show = sub.add_parser("show", help="one run, as a narrative")
    show.add_argument("run_id", help="a run id, or 'latest'")
    show.add_argument("--json", action="store_true", help="the raw trace JSON")
    sql = sub.add_parser("sql", help="a read-only SELECT on tables runs, steps, llm_calls")
    sql.add_argument("query")
    sub.add_parser("reindex", help="rebuild the SQLite index from the JSON files")
    args = ap.parse_args(argv)
    store = TraceStore(args.dir) if args.dir else TraceStore.from_env()
    try:
        if args.cmd == "list":
            cols, rows = store.query(
                "SELECT run_id, outcome, duration_ms AS ms, tools_used AS tools, fallback AS fb, "
                "n_sourced AS sourced, n_unverified AS unverif, substr(question, 1, 50) AS question "
                f"FROM runs ORDER BY run_id DESC LIMIT {int(args.n)}"
            )
            print(_table(cols, rows))
        elif args.cmd == "show":
            trace = store.load(args.run_id)
            print(trace.model_dump_json(indent=2) if args.json else render_trace(trace))
        elif args.cmd == "sql":
            print(_table(*store.query(args.query)))
        else:
            print(f"reindexed {store.reindex()} traces")
    except (ValueError, FileNotFoundError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # sqlite errors, bad trace files
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
