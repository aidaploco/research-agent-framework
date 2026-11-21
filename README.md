# Research Agent Framework

A research agent for a banking team. It takes a natural-language question, plans which
tool to use, searches (Wikipedia, arXiv), and prints an answer in which every sourced
claim carries a checked citation. It runs on free tiers only: no paid API keys.

> **Status: work in progress** (milestone M3 of [PLAN.md](PLAN.md)). Sections marked
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

The plan the agent chose is printed to stderr (`[plan] ...`); add `2>/dev/null` to see
only the answer.

Development:

```sh
uv run pytest                # unit tests: no network, no API key needed
uv run ruff format --check . && uv run ruff check .
```

The unit tests never touch the network: an autouse fixture fails any test that opens a
socket, and they use shared fakes (`tests/fakes.py`): a scripted fake LLM, fake tools, and
recorded HTTP responses. Live calls belong to the evaluation runs, not the unit tests.

## Architecture overview

```
main.py                      CLI entry point
src/research_agent/
  agent.py                   plan -> run one tool -> synthesize -> render
  planner.py                 LLM picks a tool + query (Pydantic Plan)
  synthesis.py               structured claims, validation, demotion, rendering
  grounding.py               checks that figures in a claim are in the cited sources
  evidence.py                evidence IDs (W1, A2, ...) for every retrieved source
  llm.py                     Groq client + structured() for schema-validated output
  tools/
    base.py                  Tool interface, Source model, options models
    registry.py              the one place tools are registered and described
    wikipedia.py  arxiv.py   the two tools
```

**Tools are plug-ins.** Each tool subclasses `Tool`: a `name`, an `id_prefix`, a one-line
`description` the planner reads, an `options_model` (a Pydantic model that both documents
and validates its options), and `run(query, **options) -> list[Source]`. A `Source` is a
Pydantic model with the tool, title, URL, text, the source's own ID where one exists (an
arXiv ID) and metadata (authors, date). Adding a tool means writing one class and
registering it in `registry.py`; the planner prompt is generated from the registry.

**Planning.** The LLM receives the registered tools and returns a `Plan`
(`reasoning`, `tool`, `query`, `options`). The reply is parsed and validated with
Pydantic, then checked against the registry (known tool, non-empty query, options valid
for that tool, with types coerced: the model sometimes writes `"true"` for a boolean).
An invalid reply is sent back to the model with the error for one retry, then falls back
to a default tool rather than failing the run. The tool `none` declines a question that
no tool can help with; the agent then refuses without spending an LLM call.

**Evidence IDs.** Every retrieved source gets an ID from its tool's prefix and a counter:
`W1`, `W2` for Wikipedia, `A1`, `A2` for arXiv. IDs are stable for the whole run, and a
source with a URL already stored keeps its original ID.

**Synthesis: structured claims, checked by code.** The model does not write free text
with citation markers. It returns a Pydantic `Answer`: a list of `claims`, each
`{text, evidence_ids}`, plus `inferences` (reasoning beyond the sources), `caveats`, and a
`refusal_reason`. The schema is sent as a strict JSON-schema response format and the reply
is validated again by Pydantic. Code then:

1. checks every evidence ID exists in this run's evidence store;
2. checks that the numbers in a claim are really in the cited sources, and near the right
   words (see *Grounding* below);
3. on a failure, shows the model exactly what was wrong and lets it repair **once**;
4. **demotes** any claim that still fails to an **Unverified** section, and labels it
   with the reason;
5. writes the `[W1][A2]` markers itself and strips any marker the model wrote.

The output has separate sections: *Notices* (written by code: tool failures and
substitutions), *From sources* (validated claims with citations),
*Unverified* (only if something was demoted), *Model inference / caveats*, and *Sources*
(title, ID, URL, built from the tool results, never written by the model).

**Orchestration pattern.** *TBD (M5).* The intended pattern is a ReAct-style loop with
state, so the agent can refine searches and avoid repeating calls. Today it is a single
plan-then-synthesize pass.

## Key design decisions and tradeoffs

### Grounding: normalized numbers, checked in context

For every figure in a claim, code looks for it in the cited sources in two steps.

- **Normalized match.** Numbers are compared by value, not spelling: `1,200` = `1200`,
  `$46 billion` = `46,000,000,000`, `4.5%` = `4.5 percent`, `100 bps` = `100 basis points`,
  "thirty" = `30`, unicode dashes and no-break-space thousands separators are handled, and
  ISO dates compare as dates. Units must agree when both sides state one, so 100 basis
  points does not ground "100%".
- **Proximity.** A matching number only counts if a keyword from the claim's own words
  around that number (bounded at the nearest comma or sentence end) appears near it in the
  source, within the same sentence. This catches a real number attached to the wrong fact:
  a source's "3 percent" next to "leverage ratio" does not ground a claim about a dividend
  cap. A number alone on its own line, as in lists, is described by the lead-in before it.

Single-digit plain integers are not checked, and plain years only need to be present.
The check is keyword proximity, not understanding; see Limitations. The model is also asked
to write one atomic fact per claim, which makes the check sharper.

### Unverified claims are demoted, not dropped

When a claim cites an evidence ID that does not exist, cites nothing, or contains a figure
that is not in its cited sources, the claim is moved out of *From sources* into
*Unverified* with the reason. It is not deleted. **Dropping it silently would hide
information** from the reader: they could not tell that the model said something it could
not support, or why. Demoting fits the spec better: retrieved information stays
separated from the model's own inference, the reader still sees the statement, and the
label makes clear it is not backed by a retrieved source.

### LLM: free cloud tier (Groq) rather than a local model

The spec allows a local model or a free cloud tier. This project uses **Groq's free
tier** with `openai/gpt-oss-120b`.

| | Cloud (Groq free tier, chosen) | Local (e.g. Ollama) |
|---|---|---|
| Speed | Fast: seconds per answer | Slow on CPU, especially for the 7B+ models that reason well |
| Reasoning / JSON reliability | A 120B model: follows JSON schemas and the citation format well | Small models (3B) often break structured output; larger ones are slow |
| Reproducibility | Needs a free signup and an API key | No signup, no key, works offline |
| Limits | Rate limits (see below); free-tier terms can change | None beyond your hardware |
| Cost | Free, no card | Free |

I chose cloud because the planner and the synthesis depend on reliable structured
output and reasoning, and because a reviewer can run a question in seconds. The cost is
the signup and the rate limits. The limits I observed in Groq's response headers for
this model on a free key were **8,000 tokens per minute** and a **1,000 request** limit
(the window for the request limit is not verified; limits change, so check the Groq
console). The token limit is the one that matters: it caps how much tool output can be
sent to the model, which is why Wikipedia extracts are truncated (8,000 characters for the
top article, 3,000 for others) and arXiv abstracts to 1,200.

All LLM calls go through `llm.py`, which is the swap point for another provider. Local
servers such as Ollama expose an OpenAI-compatible endpoint, so a local backend should be
a change to the URL and model name in that one file. **That is untested**: only Groq has
been run. Strict JSON-schema output, which synthesis relies on, was verified on Groq with
this model; a local model may not support it.

### Pydantic and structured outputs

Pydantic models define the data that crosses the model boundary and the tool boundary:
`Plan`, `Answer`/`Claim`, `Source` and each tool's options. `llm.structured()` sends the
model's JSON schema as the response format, validates the reply, runs an optional semantic
check, and retries once with the problem described. The `Answer` schema uses Groq's
**strict** mode. The `Plan` schema does not: strict schemas reject free-form objects, and a
plan's `options` differ per tool, so options are validated afterwards against the chosen
tool's options model instead.

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

### Wikipedia: exact title first

Wikipedia's full-text search ranks an article titled exactly "Basel III" below pages that
merely mention it. The tool therefore looks up the exact title first (following
redirects, skipping disambiguation pages), then adds full-text search hits. The planner is
asked to name the article it expects. Wikipedia also returns HTTP 429 under bursts, so
requests retry with backoff.

### Tool failures: short retries, a fallback, and a notice

Real APIs fail. arXiv rate-limits aggressively and answers `Rate exceeded.` with an HTTP 200
body, not a 429, and it occasionally times out; Wikipedia returns 429 under bursts.

- **Retry:** each tool makes up to **3 attempts**, with a **10-second timeout** per request
  and a **short backoff** (arXiv 2s then 4s, Wikipedia 1s then 2s). arXiv also spaces
  requests at least 3 seconds apart. The worst case for arXiv is about 40 seconds.
- **Tradeoff:** during development a single arXiv query once needed about 25 seconds of
  retries to clear a rate limit. Short backoff means more runs give up on arXiv and fall back,
  in exchange for never hanging for minutes.
- **Fallback:** if the planned tool is still unavailable, the agent tries the other
  registered tools with the same query (without the planned tool's options). Anything they
  return is used as evidence.
- **Disclosure:** the code, not the model, prints a **Notices** section saying which tool
  was unavailable and why, and, if another tool answered instead, that the sources come from
  that tool and may not cover what the planned one would have. The model is told as well, so
  it can caveat. A substitute that returns nothing is not presented as an answer.
- **Not done:** circuit breakers, and measuring answer quality under partial failure
  (that is the Tier 3 reliability item; the Tier 3 choice here is the evaluation harness).
  A tool that works but returns nothing does not trigger a fallback; refining the search is
  the job of the multi-step loop (M5).

## Agent performance summary

*TBD (M7 runs the 8 reference questions; M8 adds the evaluation harness).*

Early observations (M3, single runs, not a benchmark):

- *Discount window:* planned Wikipedia (`Discount window`); every claim cited `[W1]` and
  passed validation. Before the number-word fix, a correct claim ("thirty days") was
  wrongly demoted, because the model wrote `30` and the source spelled it out.
- *Basel III capital requirements:* planned Wikipedia (`Basel III`); returned the CET1,
  buffer, Tier 1, leverage and liquidity figures, all checked against the source. Earlier
  in development this question refused: first the wrong article was retrieved, then the
  right article was truncated before the capital ratios. Both were fixed in the tools.
- *Credit-risk research:* planned arXiv; five papers with arXiv IDs, and precise figures
  (ROC-AUC values) matched the abstracts.
- *Best restaurant in NYC:* planned `none` and refused without calling a tool or the model.
- *arXiv unavailable (simulated by pointing the tool at a dead port, Groq and Wikipedia
  live):* three attempts, then the Notices section named arXiv and the `ConnectionError`,
  said the sources came from Wikipedia instead, and the model refused because Wikipedia's
  article did not cover the question. The run took about 11 seconds.
- Re-running the Discount window and Basel III questions after the grounding rewrite kept
  every claim sourced (9 and 11 sourced claims, none demoted). Earlier versions of the check
  wrongly demoted correct claims three times (a spelled-out number, a number alone on its
  line, and a stem collision between "dividend" and "divided"); each is now a regression test.

## Limitations

Known at this milestone:

- One tool call per question (see above); cross-tool questions are answered from one tool.
- **The grounding check is keyword proximity, not understanding.** It confirms a number is
  in a cited source near words from the claim; it cannot tell that a claim reverses the
  passage's meaning. Two figures in one unpunctuated clause share context, so swapped figures
  there can pass. Single-digit plain integers are not checked and years only need to be
  present. Spelled-out numbers are matched for one to twenty, the tens and "hundred" only;
  units are not converted (basis points to percent) and month-name dates are not parsed.
  A correct claim can occasionally be demoted for these reasons; it is labelled, not hidden.
- Valid IDs prove a citation exists, not that the cited passage supports the claim's meaning.
- Wikipedia extracts are still truncated (8,000 characters for the top article), so facts
  deep in a long article can be missed.
- arXiv is slow and sometimes unavailable. The fallback tool may not be a good substitute
  (Wikipedia is not a substitute for papers); the notice says so, but the answer is weaker.
- No FRED tool yet, so current economic data can't be retrieved.
- No tracing or evaluation yet.

## What I would do differently with more time

*TBD (M9).*
