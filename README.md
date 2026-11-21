# Research Agent Framework

A research agent for a banking team. It takes a natural-language question, plans which
tool to use, searches (Wikipedia, arXiv), and prints an answer in which every sourced
claim carries a checked citation. It runs on free tiers only: no paid API keys.

> **Status: work in progress.** Milestones M0 to M6 are implemented (see [PLAN.md](PLAN.md));
> the 8 reference runs and the evaluation harness are next.
> Sections marked *TBD* are filled in as those land. This README is updated with each milestone.

## Setup and run

Requirements: [uv](https://docs.astral.sh/uv/) and Python 3.11 or newer (uv installs a
suitable Python for you if you don't have one).

```sh
uv sync                      # install dependencies from uv.lock
cp .env.example .env         # then put your free Groq key in .env
uv run python main.py "What is the Federal Reserve's discount window?"
```

Get a free Groq key at <https://console.groq.com>. No payment details are needed.
A FRED key (optional, but needed for current economic data such as the unemployment rate) is
free at <https://fred.stlouisfed.org/docs/api/api_key.html>; put it in `.env` as `FRED_API_KEY`.
Without it the agent still runs, and says plainly that live data could not be retrieved.

By default the agent may make up to 3 searches, one tool at a time, refining as it goes. For
one search and then an answer, add `--single-pass` (or set the cap with `--max-steps N`):

```sh
uv run python main.py --single-pass "What is the Federal Reserve's discount window?"
```

Each step the agent takes is printed to stderr (`[plan 1] ...`, `[plan 2] ...`) and so is the
path of the run's trace (`[trace] ...`); add `2>/dev/null` to see only the answer.

Every run writes a trace. To read the last one:

```sh
uv run python -m research_agent.trace_cli show latest      # the run as a narrative
uv run python -m research_agent.trace_cli list             # recent runs
uv run python -m research_agent.trace_cli sql "SELECT type, tool, status, duration_ms FROM steps"
```

Development:

```sh
uv run pytest                # unit tests: no network, no API key needed
uv run ruff format --check . && uv run ruff check .
```

The unit tests never touch the network: an autouse fixture fails any test that opens a
socket, and they use shared fakes (`tests/fakes.py`): a scripted fake LLM, fake tools, and
recorded HTTP responses. Live calls belong to the evaluation runs, not the unit tests.

## Using the agent

### Ask a question

```sh
uv run python main.py "What are the Basel III capital requirements for banks?"
```

Put the question in quotes. The agent works out which source suits it:

| Kind of question | Where it goes | Example |
|---|---|---|
| Definitions, how a mechanism works, history | Wikipedia | *What is the Federal Reserve's discount window and how does it work?* |
| Research literature, "recent papers" | arXiv | *What recent academic research exists on using machine learning for credit risk assessment?* |
| Current or recent economic statistics | FRED (needs a free key) | *What is the current US unemployment rate and how has it changed over the past year?* |
| Nothing it can help with | refused, with no search and no model call | *What is the best restaurant in New York City?* |

A question that needs two sources (for example *"explain yield curve inversions and find recent
papers"*) is answered in steps: the agent searches one tool, looks at what came back, and
searches the next, up to 3 searches. If a search finds nothing or the wrong thing it changes the
query instead of giving up. Add `--single-pass` for one search only. Questions about current
economic data go to FRED; without a FRED key the agent says live data could not be retrieved and
states no current figure (see *FRED: current data, and what happens without a key*).

### Read the answer

The answer goes to stdout; the plan and the trace path go to stderr. An abridged answer:

```
## From sources
- Basel III requires banks to have a minimum Common Equity Tier 1 (CET1) ratio of 4.5% of risk-weighted assets. [W1]
- Basel III introduced a minimum leverage ratio of 3%, calculated as Tier 1 capital divided by total leverage exposure. [W1]

## Model inference / caveats
- Inference: The framework combines quality, quantity, buffers, leverage limits and liquidity standards.
- Caveat: The sources do not list updates to the standards after the initial publication.

## Sources
[W1] Basel III - https://en.wikipedia.org/wiki/Basel_III
[W2] Basel Committee on Banking Supervision - https://en.wikipedia.org/wiki/Basel_Committee_on_Banking_Supervision
```

- **From sources:** statements taken from the retrieved sources. Each carries citation markers
  (`[W1]`, `[A2]`: the letter is the tool, Wikipedia or arXiv, and the number is the source),
  written by code after it checked the source exists and that every figure in the statement is
  in it. The *Sources* list at the bottom gives title, ID and URL so you can open each one.
- **Model inference / caveats:** what the model adds itself. Treat these as reasoning, not as
  retrieved fact.
- **Unverified** (only when present): statements the model made that failed the checks, kept
  visible and labelled with the reason, never silently dropped. Do not rely on them.
- **Notices** (only when present): written by code, for example that a tool was unavailable
  and another one answered in its place, or that no current figure is available because the live
  data source could not be reached.
- **Gaps:** the sources answered part of the question, and this says what is missing.
- **Cannot answer from sources:** the sources did not answer the question, or it was out of
  scope. The agent says so instead of guessing.

### Look at what the agent did

Each run is traced; `uv run python -m research_agent.trace_cli show latest` explains every
decision (see *Observability* below).

### Configuration

Set these in `.env` (copy `.env.example`) or in the environment.

| Variable | Required | Meaning |
|---|---|---|
| `GROQ_API_KEY` | yes | Free key from <https://console.groq.com>. |
| `LLM_MODEL` | no | Groq model id; default `openai/gpt-oss-120b`. Only the default has been tested. The model must support JSON-schema output. |
| `TRACE_DIR` | no | Where traces and the index are written; default `.traces`. |
| `FRED_API_KEY` | no | Free key for the FRED tool (current economic data). Without it FRED reports that live data cannot be retrieved. The key is sent only to FRED and never appears in errors, notices or traces. |

### Troubleshooting

- **`error: GROQ_API_KEY is not set`:** create `.env` from `.env.example` and add your key.
- **`error: Groq API error 429`:** the free tier's rate limit (about 8,000 tokens per minute for
  the default model). The client already retries with the `Retry-After` the API gives; wait a
  minute and run again. Running several questions back to back can trigger it.
- **A notice says FRED is unavailable and no current figure is stated:** `FRED_API_KEY` is not set
  (or FRED could not be reached). Add the key to `.env`; until then the agent will not state a
  current value, by design.
- **A "Notices" section says arXiv was unavailable:** arXiv rate-limits and sometimes times
  out. The agent retried three times with short backoff, then tried Wikipedia; the notice says
  which sources the answer used. Run it again later for paper results.
- **"Cannot answer from sources":** the retrieved articles did not contain the answer. Try
  rewording with the topic's proper name (for example "Basel III", not "bank capital rules").
  `trace_cli show latest` shows what was searched and retrieved.
- **`[pacing] waiting 24.4s for the token allowance`:** normal on the free tier. A multi-step run
  sends more text to the model than the 8,000 tokens per minute allow, so the client waits for
  the allowance to refill instead of failing. `--single-pass` avoids most of it.
- **A slow run:** most of the time is the model, the network and pacing. A multi-step run can
  take a minute; arXiv adds up to about 40 seconds when it is rate-limiting.

### Add a tool

1. Write a class in `src/research_agent/tools/` that subclasses `Tool`: set `name`, a one-letter
   `id_prefix`, a one-line `description` (the planner chooses tools by reading it), and, if it
   takes options, an `options_model` (a Pydantic model with a `Field(description=...)` per
   option). Implement `run(query, **options) -> list[Source]`, raising `ToolError` on failure.
2. Register it in `default_registry()` in `tools/registry.py`.
3. Test it with `FakeHTTPResponse` and `FakeTool` from `tests/fakes.py`.
4. If it returns *current* figures, set `live_data = True` on the class: when it is unavailable the
   agent then adds the "no current figure" notice automatically.

Nothing else changes: the planner prompt is generated from the registry, evidence IDs and
citations come from the prefix, and failures flow through the same retry, fallback and notice
path. (IDs stay unique even if two tools share a prefix; distinct letters just read better.)

## Architecture overview

```
main.py                      CLI entry point (clean error messages, exit code 1 on failure)
src/research_agent/
  agent.py                   the research loop: plan a step, run it, repeat, then synthesize
  planner.py                 LLM picks the next action: search, finish or refuse (Pydantic Plan)
  synthesis.py               structured claims, validation, demotion, rendering
  grounding.py               checks that figures in a claim are in the cited sources
  evidence.py                evidence IDs (W1, A2, ...) and the per-run evidence size budget
  llm.py                     Groq client (token pacing, Retry-After cap) + structured() output
  tracing.py                 trace models, the run recorder, JSON + SQLite storage
  trace_cli.py               list / show / sql / reindex for traces
  tools/
    base.py                  Tool interface, Source model, options models
    registry.py              the one place tools are registered and described
    wikipedia.py  arxiv.py   Wikipedia and arXiv
    fred.py                  FRED: current and historical US economic statistics
```

**Tools are plug-ins.** Each tool subclasses `Tool`: a `name`, an `id_prefix`, a one-line
`description` the planner reads, an `options_model` (a Pydantic model that both documents
and validates its options), and `run(query, **options) -> list[Source]`. A `Source` is a
Pydantic model with the tool, title, URL, text, the source's own ID where one exists (an
arXiv ID) and metadata (authors, date). Adding a tool means writing one class and
registering it in `registry.py`; the planner prompt is generated from the registry.

**Planning.** Each step the LLM receives the question, the tools it may use, how many searches
are left, and a compact log of what the loop already did (tool, query, outcome, and the titles
found, never source text). It returns a `Plan`: an `action` (`search`, `finish` or `refuse`),
`reasoning`, and for a search the `tool`, `query` and `options`. The reply is parsed and
validated with Pydantic, then checked by code against rules the model cannot be trusted with:
it may **refuse only before any evidence exists** and **finish only after some does**, it may
not pick a tool that already failed this run, and a search's options must be valid for that
tool (types are coerced: the model sometimes writes `"true"` for a boolean). A reply that breaks
a rule is sent back with the reason for one retry, then a safe default is used rather than
failing the run. A refusal ends the run without searching or calling the model again.

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
5. writes the `[W1][A2]` markers itself and strips any marker the model wrote;
6. holds the model's **own words** (inferences, caveats, a refusal explanation) to the same figure
   check against all retrieved sources. A figure that no source states, for example a rate
   remembered from training, is shown under *Unverified*, never as inference. The model's own
   arithmetic is not trusted either: a derived figure that no source states is flagged.

The output has separate sections: *Notices* (written by code: tool failures and
substitutions), *From sources* (validated claims with citations),
*Unverified* (only if something was demoted), *Model inference / caveats*, and *Sources*
(title, ID, URL, built from the tool results, never written by the model).

**Orchestration pattern: a ReAct-style loop with state kept in code.** The agent alternates
*reason* (the planner picks an action after seeing what has happened) and *act* (run one tool),
then observes the result, until it decides it has enough. The state lives in ordinary code, not
in the model:

- a **step cap** (`--max-steps`, default 3), so the loop always ends; `--single-pass` is the same
  loop with the cap at 1, which makes the single-pass vs multi-step comparison fair;
- a **seen-set**: a repeated search (same tool, query and options) is never executed; it is
  recorded as a skipped step and ends the loop;
- a per-run **failed-tool set**: a tool that failed is not offered or retried on later steps, so a
  dead arXiv costs one failure, not one per step;
- one **evidence budget** for the whole run (see below), so the prompt always fits the free tier;
- a **fallback** to another tool only on the last allowed step: before that, a failure is left
  to the planner, which can see it and choose differently.

The loop stops on: the planner choosing *finish*, the step cap, a repeated search, two steps in a
row that found nothing new, or a spent evidence budget. The reason is recorded in the trace.

*Why this pattern.* The questions that need more than one search are exactly the ones where the
second search depends on the first result (a definition, then papers on it; a query that came
back empty, then a better one). Plan-and-execute would fix the searches before seeing any result,
so it cannot refine. A fixed state machine would need a state for every shape of question. A
ReAct loop handles both with one prompt, and keeping its bookkeeping in code means the model's
mistakes (repeating a query, refusing after it has evidence, calling a dead tool) are caught by
rules instead of by hope. The costs are more model calls per question, and a planner that
sometimes stops too early or too late; both are measured below.

## Observability: traces

Every run produces a structured trace, saved even when the run crashes. The trace is a
Pydantic model written as one JSON file per run in `.traces/` (override with `TRACE_DIR`),
which is the record, plus a SQLite index (`.traces/index.db`) for queries. It records, in
order:

| Step | What it holds |
|---|---|
| **plan** (one per step) | the tools offered, the compact log of earlier steps the model was shown, how many searches were left, the model's stated reasoning, the action (`search`, `finish`, `refuse`) with its tool, query and options, whether the model's output never validated and a default was used, and every model call exactly as sent and received (including replies that were rejected), with its token counts, any wait, and HTTP retries |
| **tool** | the tool called, whether it was the planned tool or a fallback, its input, status (`ok`, `empty`, `error`, or `skipped` for a repeat or an already-failed tool that the loop refused to run), the error or reason, the sources it returned with the evidence IDs they were given, how many were new, how many were trimmed or dropped for the evidence budget, and each retry the tool made inside the call |
| **synthesis** | which evidence IDs the model could cite, every model call and the validation problem that caused a retry, and the result: each claim as sourced or unverified (with the reason), inferences, caveats and any refusal |

The trace also holds the question, the model name, the mode (`single-pass` or `multi-step`) and
step cap, **why the loop stopped** (`finish`, `step_cap`, `duplicate`, `no_new_evidence`,
`budget`, `tool_unavailable` or `refuse`), timing per step, the outcome (`answered`, `refused`,
`out_of_scope` or `error`), the notices, and the final answer text. A reviewer can
follow a decision from the offered tools, to the reasoning, to the retrieved evidence, to the
claims that survived validation. `trace_cli show` prints exactly that as a narrative:

```
Step 1  PLAN (458 ms)
  chose: wikipedia  query='Basel III'
  why: Wikipedia provides a concise overview of Basel III capital requirements.
    model call 1 (openai/gpt-oss-120b, 457 ms): accepted
Step 2  TOOL wikipedia [planned] (2565 ms)  query='Basel III'
  result: 2 sources
    [W1] Basel III  (8000 chars)  https://en.wikipedia.org/wiki/Basel_III
    [W2] Basel Committee on Banking Supervision  (3000 chars)  https://en.wikipedia.org/wiki/...
Step 3  SYNTHESIS (2221 ms)  may cite: W1, W2
  7 sourced, 0 unverified
    sourced [W1]: Basel III requires banks to have a minimum Common Equity Tier 1 (CET1) ratio ...
```

The SQLite index has three tables (`runs`, `steps`, `llm_calls`), so questions like "how often
did the planner's output need a repair?", "which runs fell back to another tool?" or "how many
tokens did multi-step runs use compared with single-pass?" are one `SELECT` away (`trace_cli sql` accepts read-only queries only). The index can always be rebuilt
from the JSON files with `trace_cli reindex`.

Notes on what a trace is and is not:

- **"Reasoning" is the model's stated one-sentence reason** in its plan, not hidden
  chain-of-thought. The exact prompts and replies are recorded, so the decision can be
  checked against what the model was shown.
- **"Raw tool output" is the list of sources as the tool returned it** after the tool's own
  truncation (for example 8,000 characters of the top Wikipedia article), not the raw HTTP
  response.
- Traces include full prompts and retrieved text, so they can be large. They stay local
  (`.traces/` is gitignored); sample traces for the reference questions are committed with
  their outputs in a later milestone. An index from an older version of the schema is rebuilt
  from the JSON files automatically.

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

Single-digit plain integers are not checked, and plain years only need to be present (a year
also matches the ISO dates it is part of, so "2026" is found in `2026-10-01`). When the clause
around a number has little context, the words of its whole sentence are used, so a subject stated
earlier ("the unemployment rate has fallen, moving from 4.4% to 4.1%") still counts. Month names
and identifiers such as `COVID-19` or `U-3` are not treated as keywords or quantities.
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
this model on a free key were **8,000 tokens per minute** and **1,000 requests** (the headers
show it resetting about a day later; limits change, so check the Groq console). The token limit is the one that matters: it caps how much tool output can be
sent to the model, which is why there is an evidence budget (see *Token budget and pacing*),
and why Wikipedia extracts are truncated (8,000 characters for the top article, 3,000 for
others) and arXiv abstracts to 1,200.

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

### One tool call per step, several steps per question

The planner picks a single tool per step; it does not call several tools in parallel. A question
that needs two tools takes two steps.

- **Why:** the free tier's 8,000 tokens per minute makes parallel retrieval risky (several tools'
  output in one prompt can exceed it), and one call per step keeps each step's reasoning and the
  trace easy to read. It is also what makes refinement possible: each search is chosen after the
  previous result is known.
- **Cost:** more model calls per question, and, because every call counts against the token
  allowance, more waiting (measured below: about 4x the time on a cross-tool question).
- **Later:** parallel calls could be added if a higher rate limit allowed.

### Token budget and pacing

The free tier limits *tokens per minute*, and this was measured, not assumed: sending a
deliberately oversized request returned HTTP 413 reporting the **prompt tokens plus
`max_completion_tokens`** as the request size (9,698 against the limit of 8,000, for a prompt of
about 9,600 tokens and an output allowance of 100). So the output allowance counts. Groq's
response headers show the bucket refilling continuously at limit/60 tokens a second, and the
1,000-request limit resets on roughly a daily window.

Three things follow in the code:

- **One evidence budget per run** (16,000 characters, about 4,000 to 5,000 tokens), enforced as
  sources enter the evidence store: a source that crosses the budget is trimmed, later ones are
  dropped, and the loop stops when it is spent. The prompt, the grounding check and the trace
  therefore all see exactly the same text. The planner is only shown titles, never bodies.
- **Pacing:** before each call the client computes the tokens available now from the last
  response's headers and the refill rate, and waits if the request would not fit, instead of
  failing. A request that could never fit (over 8,000 tokens by itself) is refused before it is
  sent. Waits are printed (`[pacing] ...`) and recorded in the trace.
- **`Retry-After` is capped at 30 seconds.** If the API asks for longer (a daily quota, not a
  per-minute limit) the client stops with a clear error instead of sleeping for hours.

### FRED: current data, and what happens without a key

FRED is the source of *current* statistics, so the rule for it is strict: **a current figure either
comes from FRED or is not stated.** (The endpoints and the 13 series IDs in the planner's catalog
were checked against the live API.)

- **What it returns:** one source per call: the series title, units, frequency and last-updated
  date, the latest value, the value about a year earlier, and the change, which is **computed in
  code**, not by the model, so the grounding check can verify it. Daily series are summarized
  (the latest five observations), and FRED's `.` marker for a missing value is skipped.
- **Without a key** the tool fails at once, with no network call, saying live data cannot be
  retrieved. The loop treats that like any failed tool, so the planner can turn to Wikipedia. The
  code (not the model) then adds a notice: *no current figure from fred is available, so none is
  stated here; anything below from other sources is general background that may be out of date.*
  The model is also told not to state any value for any date and to give only definitions and
  methods; that steer is best effort (see Limitations), but the notice and the checks below are not.
- **No figure from memory:** every figure the model states must be in a retrieved source: in a
  claim (checked against its citations) and in its own inferences and caveats (checked against
  all sources). The model's remembered "current rate" therefore lands under *Unverified*.
- **The API key is protected.** FRED takes the key as a URL parameter, and a `requests` error
  message contains the URL. No error, notice or trace is built from exception text (only the
  exception's type name or FRED's own `error_message`), and tests check that a key never reaches
  an error, a trace or a notice.

Q5 both ways is in [`outputs/`](outputs/): [with a key](outputs/q5-with-fred-key.md) (4.1% in
October 2026, down 0.3 points from a year earlier, from the FRED series page) and
[without a key](outputs/q5-without-fred-key.md).

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
  requests at least 3 seconds apart. The worst case for arXiv is about 40 seconds. One helper
  (`retry.py`) runs the loop for arXiv, Wikipedia and the model client, and notes every failed
  attempt, with its reason and wait, in the trace (under the tool step or the model call).
- **Tradeoff:** during development a single arXiv query once needed about 25 seconds of
  retries to clear a rate limit. Short backoff means more runs give up on arXiv and fall back,
  in exchange for never hanging for minutes.
- **A failure is shown to the planner:** in a multi-step run the failed tool is marked as failed
  in the log and not offered again, so the planner can choose another tool or query itself.
- **Fallback:** only on the *last* allowed step (always, in single-pass mode), when no later step
  could react, the agent tries the other registered tools with the same query (without the
  planned tool's options). Anything they return is used as evidence.
- **Disclosure:** the code, not the model, prints a **Notices** section saying which tool
  was unavailable and why, and, if another tool answered instead, that the sources come from
  that tool and may not cover what the planned one would have. The model is told as well, so
  it can caveat. A substitute that returns nothing is not presented as an answer.
- **Not done:** circuit breakers, and measuring answer quality under partial failure
  (that is the Tier 3 reliability item; the Tier 3 choice here is the evaluation harness).
  A tool that works but returns nothing does not trigger a fallback; in a multi-step run the
  planner sees "no results" in its log and refines the query.

## Agent performance summary

*TBD (M7 runs the 8 reference questions; M8 adds the evaluation harness).*

### Q5, the unemployment rate, both ways (M6, live, one run each)

| | Result |
|---|---|
| With a FRED key | Planned FRED, then finished after one search. Two sourced claims: 4.1% in October 2026, and a 0.3-point fall from 4.4% a year earlier, both cited to the FRED series. No demotions. |
| Without a key | FRED failed instantly; notice: no current figure available, none stated. Planned Wikipedia and finished. A "Gaps" section says the current rate and its change are not in the sources. Three sourced claims about dated historical rates (2017, 2019, 2020) and **no current rate**. The model's caveat about "the current (2026) rate" was caught as its own words and shown under Unverified. |

Along the way the free-text check wrongly demoted correct statements three times (a subject in the
previous clause, month names that do not match ISO dates, a year that only appears inside an ISO
date) and once treated the `19` in `COVID-19` as a quantity; each is fixed and is a regression test.

### Single-pass vs multi-step (M5, live, one run each: not a benchmark)

The same loop with one search (`--single-pass`) against the default of up to three. Numbers come
from the runs' traces (`trace_cli sql`); the final side-by-side outputs for the README come with
the reference runs (M7).

| Question | Mode | Searches | Sourced claims | Prompt + output tokens | Time |
|---|---|---|---|---|---|
| Yield curve inversions and recessions, and recent papers (cross-tool) | single-pass | arXiv | 4 | 1,532 + 566 | 15 s |
| | multi-step | Wikipedia, then arXiv | 7 | 7,818 + 2,049 | 60 s (52 s waiting for the token allowance) |
| Fed response to 2008 vs COVID-19 (comparison) | single-pass | arXiv (no results) | 0: refused | 1,084 + 202 | 29 s (arXiv retries) |
| | multi-step (after prompt tuning) | Wikipedia twice, then finish | 4 sourced, 1 unverified, and a stated gap | 5,988 + 1,260 | 13 s |

What this shows:

- **Cross-tool questions are where multi-step wins.** On the yield-curve question single-pass could
  only reach arXiv and answered from two papers; multi-step added Wikipedia's definition of an
  inverted yield curve first, giving a fuller answer. It cost about five times the tokens and four
  times the time, almost all of it waiting for the free tier's token allowance.
- **Multi-step recovers from a bad first search.** In the comparison question single-pass chose
  arXiv with a five-word query that matched nothing and had to refuse; multi-step started from
  Wikipedia and found the 2008 article.
- **It is not a complete answer.** The Wikipedia search for the COVID-19 response returned an
  article about COVID-19 policy in April 2020 rather than the Federal Reserve's actions, and the
  planner called the evidence "sufficient" and finished. The answer covers 2008, says plainly that
  the COVID-19 side is missing, and does not invent it. Two earlier problems in the same run were
  fixed by prompt changes: the planner repeated a failed query (the loop's repeat guard caught
  it), and the synthesis prompt made the model discard the half of the answer it could support.

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

- **The planner's judgement is the weak point of multi-step.** It sometimes calls evidence
  sufficient when it is not (the 2008 vs COVID-19 question), and a model that is told not to
  repeat a search sometimes does (the loop skips the repeat and stops). The step cap, repeat
  guard and no-progress rule bound the damage; they do not make the planner right.
- **Multi-step is slow on the free tier**: more model calls against 8,000 tokens a minute means
  waiting (about 50 seconds of pacing on the cross-tool question above). `--single-pass` trades
  completeness for speed.
- The evidence budget (16,000 characters) means a run that gathers a lot trims or drops later
  sources, and the model never sees what was cut; the trace records it.
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
- **FRED:** the planner picks a series from a short catalog in the tool's description, or the tool
  searches FRED by keyword and takes the most popular match (it names the other candidates, and
  a wrong pick can be refined on the next step). "A year earlier" is the nearest observation within
  70 days, so around a gap in the data (FRED has no October 2025 unemployment figure) the dates
  differ from exactly one year; the dates are always stated.
- **Without a FRED key, the model may still list dated historical figures** from Wikipedia despite
  being told to give only definitions. They are sourced, dated and covered by the notice that
  anything below may be out of date, but they are not the "how the rate is measured" background
  that was asked for. What is guaranteed is that no current rate appears and no figure comes from
  the model's memory; the steer toward definitions only is best effort.
- Results above are single live runs on one day; the planner and the sources vary from run to run.
- No evaluation harness yet.

## What I would do differently with more time

*TBD (M9).*
