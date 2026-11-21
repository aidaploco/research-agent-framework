#!/usr/bin/env python3
"""CLI entry point: python main.py "your question" """

import argparse
import sys

from dotenv import load_dotenv

from research_agent.agent import DEFAULT_MAX_STEPS, answer
from research_agent.llm import LLMError


def main():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Ask a research question; get an answer with checked citations.",
    )
    parser.add_argument("question", nargs="+", help="the research question (quotes are optional)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--max-steps",
        type=int,
        default=None,  # not DEFAULT_MAX_STEPS: argparse detects a conflict by comparing to the
        # default with `is`, and a small int equal to the default looks like "not given"
        metavar="N",
        help=f"most searches the agent may make (default {DEFAULT_MAX_STEPS})",
    )
    mode.add_argument(
        "--single-pass",
        action="store_true",
        help="one search, then answer: the same loop with --max-steps 1",
    )
    args = parser.parse_args()
    max_steps = args.max_steps
    if args.single_pass:
        max_steps = 1
    elif max_steps is None:  # explicit None check: 0 is a (rejected) value, not "unset"
        max_steps = DEFAULT_MAX_STEPS
    if max_steps < 1:
        parser.error("--max-steps must be at least 1")
    load_dotenv()
    try:
        print(answer(" ".join(args.question), max_steps=max_steps))
    except LLMError as e:  # a missing key or an unreachable model: say so, without a traceback
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
