#!/usr/bin/env python3
"""CLI entry point: python main.py "your question" """

import sys

from dotenv import load_dotenv

from research_agent.agent import answer


def main():
    if len(sys.argv) < 2:
        sys.exit('Usage: python main.py "your research question"')
    load_dotenv()
    print(answer(" ".join(sys.argv[1:])))


if __name__ == "__main__":
    main()
