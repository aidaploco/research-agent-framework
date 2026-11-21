#!/usr/bin/env python3
"""CLI entry point: python main.py "your question" """

import sys

from dotenv import load_dotenv

from research_agent.agent import answer
from research_agent.llm import LLMError


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python main.py "your research question"')
    load_dotenv()
    try:
        print(answer(" ".join(sys.argv[1:])))
    except LLMError as e:  # a missing key or an unreachable model: say so, without a traceback
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
