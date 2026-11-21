#!/usr/bin/env python3
"""Run the eight reference questions and save each answer with its trace.

    uv run python scripts/run_reference.py                  # all eight, multi-step (the default)
    uv run python scripts/run_reference.py --single-pass    # all eight, one search each
    uv run python scripts/run_reference.py --only 4 6       # just those questions

Each run writes outputs/<mode>/qN.md (the question, the settings, the answer and a short summary
taken from the run's trace) and keeps the trace itself in outputs/traces/<mode>/, readable with
`python -m research_agent.trace_cli --dir outputs/traces/<mode> show <run id>`. Questions run one
after another; the model client paces itself to the free tier's token limit, so a full run takes
several minutes. Needs GROQ_API_KEY (and FRED_API_KEY for question 5).
"""

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from research_agent import agent
from research_agent.llm import LLMError, model_name
from research_agent.reference import REFERENCE_QUESTIONS
from research_agent.tracing import SynthesisStep, ToolStep, TraceStore

OUT = Path(__file__).resolve().parent.parent / "outputs"


def summary_line(trace) -> str:
    tools = [s for s in trace.steps if isinstance(s, ToolStep) and s.status != "skipped"]
    synth = next((s for s in trace.steps if isinstance(s, SynthesisStep)), None)
    calls = [a for s in trace.steps for a in getattr(s, "llm_attempts", [])]
    return (
        f"outcome `{trace.outcome}`; stopped because `{trace.stop_reason}`; "
        f"{len(tools)} tool call(s) ({', '.join(dict.fromkeys(s.tool for s in tools)) or 'none'}); "
        f"{len(synth.sourced) if synth else 0} sourced claim(s), "
        f"{len(synth.unverified) if synth else 0} unverified; "
        f"{sum(a.prompt_tokens for a in calls)} prompt + {sum(a.completion_tokens for a in calls)} "
        f"output tokens; {sum(a.waited_ms for a in calls) / 1000:.0f}s waiting for the token "
        f"allowance; {trace.duration_ms / 1000:.0f}s in total"
    )


def run_one(n: int, kind: str, question: str, mode: str, max_steps: int) -> None:
    store = TraceStore(OUT / "traces" / mode)
    flag = "--single-pass " if mode == "single-pass" else ""
    started = time.time()
    try:
        text = agent.answer(question, traces=store, max_steps=max_steps)
        trace = store.load("latest")
        info = f"Run `{trace.run_id}`: {summary_line(trace)}."
    except LLMError as e:
        text, info = f"(the run failed: {e})", "The run failed before an answer was produced."
        print(f"q{n}: FAILED: {e}", file=sys.stderr)
    (OUT / mode).mkdir(parents=True, exist_ok=True)
    (OUT / mode / f"q{n}.md").write_text(
        f"# Q{n} ({kind}), {mode}\n\n"
        f"**Question:** {question}\n\n"
        f'**Command:** `uv run python main.py {flag}"{question}"`  \n'
        f"**Model:** `{model_name()}`; run on {datetime.now(UTC):%Y-%m-%d}; "
        f"up to {max_steps} search(es).\n\n"
        f"{info}\n\n"
        f"## Answer\n\n```\n{text}\n```\n"
    )
    print(f"q{n} ({mode}) done in {time.time() - started:.0f}s", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--single-pass", action="store_true", help="one search per question")
    parser.add_argument("--only", type=int, nargs="+", metavar="N", help="question numbers to run")
    args = parser.parse_args()
    load_dotenv(find_dotenv(usecwd=True))
    mode, max_steps = (
        ("single-pass", 1) if args.single_pass else ("multi-step", agent.DEFAULT_MAX_STEPS)
    )
    chosen = [q for q in REFERENCE_QUESTIONS if not args.only or q[0] in args.only]
    for n, kind, question in chosen:
        run_one(n, kind, question, mode, max_steps)


if __name__ == "__main__":
    main()
