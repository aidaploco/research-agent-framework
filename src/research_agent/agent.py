"""M1 thin slice: search Wikipedia once, then synthesize a cited answer."""

import sys

from . import llm
from .tools import wikipedia

SYSTEM = """You are a careful research assistant for a bank's research team.
Answer the question using ONLY the numbered sources provided.
Format:
## From sources
Claims taken from the sources, each followed by a citation like [1] or [2].
## Model inference / caveats
Anything you add that is NOT in the sources (reasoning, inference, gaps, outdated risk). Say so explicitly.
If the sources do not answer the question, or the question is outside banking/finance/economics research, say that plainly instead of guessing. Never invent citations or figures."""


def make_query(question):
    """Turn a natural-language question into a short Wikipedia search query."""
    q = llm.chat(
        [
            {
                "role": "system",
                "content": "Reply with ONLY a 2-5 word Wikipedia search query (the key topic term, no punctuation or quotes) that best finds the article needed to answer the user's question.",
            },
            {"role": "user", "content": question},
        ],
        max_tokens=60,
    )
    return q.strip().strip("\"'.") or question


def answer(question):
    query = make_query(question)
    print(f"[search] Wikipedia: {query!r}", file=sys.stderr)
    sources = wikipedia.search(query)
    if sources:
        context = "\n\n".join(
            f"[{i}] {s['title']} ({s['url']})\n{s['text']}" for i, s in enumerate(sources, 1)
        )
    else:
        context = "(no sources were found)"
    text = llm.chat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nSources:\n{context}"},
        ]
    )
    refs = "\n".join(f"[{i}] {s['title']} - {s['url']}" for i, s in enumerate(sources, 1))
    return f"{text}\n\n## Sources\n{refs or '(none)'}"
