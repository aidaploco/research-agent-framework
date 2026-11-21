# Research Agent Framework

A research agent for a banking team. It takes a natural-language question, plans which
tool to use, searches (Wikipedia, arXiv), and prints a synthesized answer with
citations. It runs on free tiers only: no paid API keys.

> **Status: work in progress** (milestone M2 of [PLAN.md](PLAN.md)). Sections marked
> *TBD* are filled in as milestones land. This README is updated with each milestone.

## Setup and run

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer (uv installs a
suitable Python for you if you don't have one).

```sh
uv sync                      # install dependencies from uv.lock
cp .env.example .env         # then put your free Groq key in .env
uv run python main.py "What is the Federal Reserve's discount window?"
```

Get a free Groq key at <https://console.groq.com>. No payment details are needed.
A FRED key (optional, for live economic data in a later milestone) is free at
<https://fred.stlouisfed.org/docs/api/api_key.html>.

Development:

```sh
uv run pytest                # unit tests: no network, no API key needed
uv run ruff format --check . && uv run ruff check .
```

## Architecture overview

```
main.py                      CLI entry point
src/research_agent/
  agent.py                   plan -> run one tool -> synthesize
  planner.py                 LLM picks a tool + query as validated JSON
  llm.py                     Groq client (OpenAI-compatible endpoint)
  tools/
    base.py                  Tool interface and Source type
    registry.py              the one place tools are registered and described
    wikipedia.py  arxiv.py   the two tools
```

**Tools are plug-ins.** Each tool subclasses `Tool`: a `name`, a one-line `description`
the planner reads, optional `options`, and `run(query, **options) -> list[Source]`.
A `Source` carries the tool, title, URL, text and, where one exists, a stable ID (an
arXiv ID) and metadata (authors, date), which is what citations are built from. Adding
a tool means writing one class and registering it in `registry.py`; the planner prompt
is generated from the registry, so it needs no other change.

**Planning.** The LLM receives the registered tools and replies with JSON:
`{reasoning, tool, query, options}`. The reply is validated (known tool, non-empty query,
options an object). An invalid reply is sent back to the model with the error for one
retry, then falls back to a default tool rather than failing the run. The tool `none`
lets the planner decline a question that no tool can help with.

**Synthesis.** The answer is split into *From sources* (each claim cited `[n]`) and
*Model inference / caveats* (anything not in the sources), followed by a source list
built by code from the tool results (title, ID, URL), not written by the model.

**Orchestration pattern.** *TBD (M5).* The intended pattern is a ReAct-style loop with
state, so the agent can refine searches and avoid repeating calls. Today it is a
single plan-then-synthesize pass.

## Key design decisions and tradeoffs

### LLM: free cloud tier (Groq) rather than a local model

The spec allows a local model or a free cloud tier. This project uses **Groq's free
tier** with `openai/gpt-oss-120b`.

| | Cloud (Groq free tier, chosen) | Local (e.g. Ollama) |
|---|---|---|
| Speed | Fast: seconds per answer | Slow on CPU, especially for the 7B+ models that reason well |
| Reasoning / JSON reliability | A 120B model: follows the planner's JSON format and the citation format well | Small models (3B) often break structured output; larger ones are slow |
| Reproducibility | Needs a free signup and an API key | No signup, no key, works offline |
| Limits | Rate limits (see below); free-tier terms can change | None beyond your hardware |
| Cost | Free, no card | Free |

I chose cloud because the planner and the synthesis depend on reliable structured
output and reasoning, and because a reviewer can run a question in seconds. The cost is
the signup and the rate limits. The limits I observed in Groq's response headers for
this model on a free key were **8,000 tokens per minute** and a **1,000 request** limit
(the window for the request limit is not verified; limits change, so check the Groq
console). The token limit is the one that matters: it caps how much tool output can be
sent to the model, which is why Wikipedia extracts are truncated to 4,000 characters and
arXiv abstracts to 1,200.

All LLM calls go through `llm.chat()`, which is the swap point for another provider.
Local servers such as Ollama expose an OpenAI-compatible endpoint, so a local backend
should be a change to the URL and model name in that one file. **That is untested**: only
Groq has been run.

### One tool call per planning step

The planner picks a single tool per step; it does not call several tools in parallel.

- **Why:** the 8,000 tokens/minute limit makes parallel retrieval risky (several tools'
  output in one prompt can exceed it), and one call per step keeps each step's reasoning
  and trace easy to read.
- **Cost:** a question that needs two tools (for example *"explain yield curve inversions
  and find recent papers"*) takes two steps instead of one. Until the multi-step loop
  lands (M5), the agent makes only one call per question, so cross-tool questions are
  answered from one tool. This is a known limitation, not a bug.
- **Later:** parallel calls can be added if the rate limit allows.

### arXiv needs retry and spacing

arXiv rate-limits aggressively and answers `Rate exceeded.` with an HTTP 200 body, not a
429. During development a single query took about 25 seconds of retries to succeed. The
arXiv tool spaces requests at least 3 seconds apart and retries with backoff, and raises
a `ToolError` after that. The agent passes the failure to the model so it can say the
tool was unavailable, instead of crashing.

## Agent performance summary

*TBD (M7 runs the 8 reference questions; M8 adds the evaluation harness).*

Early observations (M2, single runs, not a benchmark): the planner chose Wikipedia for
*"What is the Federal Reserve's discount window?"* and arXiv for the credit-risk
literature question, and declined the restaurant question without calling a tool.

## Limitations

Known at this milestone:

- One tool call per question (see above); cross-tool questions are answered from one tool.
- The model sometimes writes its own citation markers (`【1†L1-L4】`) in the answer
  text. The source list at the bottom is correct, but the inline markers need normalizing.
- Wikipedia extracts are truncated, so facts deep in a long article can be missed.
- No FRED tool yet, so current economic data can't be retrieved.
- No tracing or evaluation yet.

## What I would do differently with more time

*TBD (M9).*
