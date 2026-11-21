# PLAN — Multi-Tool Research Agent Framework

Status: planning only. No code, no `git init`, no installs yet.
Checklist wording is quoted or closely taken from the spec. IDs are cited by the milestones.

---

## 1. Requirements checklist

### Scenario (S)
- [ ] **S1** Build "a multi-tool research agent framework that takes a natural language question, plans which tools to use, executes searches, and synthesizes a cited answer."
- [ ] **S2** "The agent must work without paid API keys", and "a reviewer must be able to reproduce your results without spending money."
- [ ] **S3** Tools are "free, open APIs."
- [ ] **S4** It is a foundation: "No orchestration framework, no tool registry, no eval harness", so these have to be built as infrastructure.

### Tools (T)
- [ ] **T1** Integrate "at least two" of: Wikipedia (REST + Action API), arXiv API, FRED API.
- [ ] **T2** "You may add additional free APIs to demonstrate extensibility, but the core agent must work well with at least two of the above."
- [ ] **T3** "Each tool should be a self-contained module with a consistent interface."

### Reference questions (Q)
- [ ] **Q0** "Run your agent against all of the following questions and include the outputs in your submission."
- [ ] **Q1** What is the Federal Reserve's discount window and how does it work? (single-source factual)
- [ ] **Q2** What are the Basel III capital requirements for banks? (single-source factual)
- [ ] **Q3** What recent academic research exists on using machine learning for credit risk assessment? (academic search)
- [ ] **Q4** How did the Federal Reserve's monetary policy response to the 2008 financial crisis differ from its response to COVID-19? (multi-source synthesis)
- [ ] **Q5** What is the current US unemployment rate and how has it changed over the past year? (data retrieval, FRED)
- [ ] **Q6** Explain the relationship between yield curve inversions and recessions. Are there recent academic papers on this topic? (cross-tool synthesis)
- [ ] **Q7** What is the best restaurant in New York City? (out-of-scope: "should refuse or caveat")
- [ ] **Q8** What are the implications of quantum computing for banking encryption? (speculative / emerging)
- Note: "your agent does not need to ace every one, but we will evaluate how it handles each."

### Tier 1: Working Agent (required) (A)
- [ ] **A1** The agent takes a research question, calls tools, and returns a synthesized answer with citations.
- [ ] **A2** "A reviewer should be able to clone your repo, install dependencies, and run your agent by following the README — without reading your code first."
- [ ] **A3** "The agent receives a natural language question and decides which tool(s) to call."
- [ ] **A4** "Use an LLM to drive planning and synthesis." Either (a) a locally-runnable model (Ollama, llama.cpp, Hugging Face Transformers) or (b) a free cloud API tier (Google AI Studio, Groq, or similar).
- [ ] **A5** Document the LLM choice and tradeoffs in the README.
- [ ] **A6** Document the orchestration approach in the README: the pattern (ReAct, plan-and-execute, custom FSM, etc.) and why.
- [ ] **A7** The final answer "directly addresses the question asked."
- [ ] **A8** The final answer "cites its sources with enough detail to verify (article titles, paper IDs, URLs)."
- [ ] **A9** The final answer "distinguishes between information retrieved from tools and any reasoning or inference the model adds."
- [ ] **A10** "Runnable from the command line — e.g., `python agent.py "What is Basel III?"` — and print the answer with citations to stdout."
- [ ] **A11** Quality bar: "A polished Tier 1 with a reliable two-tool agent is a strong submission. A rushed attempt at everything is not."

### Tier 2: Production Thinking (expected) (M)
- [ ] **M1** Handle questions that need "more than one round of tool calls."
- [ ] **M2** "Refine its search based on intermediate results (not just one-shot)."
- [ ] **M3** "Maintain state across steps — the agent should know what it has already retrieved and avoid redundant calls."
- [ ] **M4** "Show the difference: pick at least two of the reference questions where multi-step produces a better answer than single-pass and include both outputs in your README." This implies a single-pass mode must exist.
- [ ] **O1** "Every run produces a structured trace."
- [ ] **O2** Each step logs: tool selected, input to the tool, raw tool output, the agent's reasoning at that step, and the final synthesis.
- [ ] **O3** Traces are stored "in a queryable format (structured JSON, SQLite, or similar)."
- [ ] **O4** "A reviewer should be able to look at a trace and understand exactly why the agent made each decision."

### Tier 3: Depth (pick any; "depth on one is better than surface-level on all") (D)
Pick one. Details are in the Ambiguities section.
- [ ] **D-EVAL** Evaluation harness:
  - "a benchmark of at least 15 questions"
  - spanning factual lookup, multi-source synthesis, and unanswerable/out-of-scope
  - per question: expected sources, key facts that should appear, facts that should NOT appear
  - measure answer relevance, source attribution accuracy, and appropriate refusal
- [ ] **D-REL** Tool reliability and fallback:
  - retry logic, fallback strategies (e.g. arXiv down, so Wikipedia partially answers), circuit breakers
  - inject synthetic failures into the test suite and demonstrate graceful degradation
  - measure how answer quality changes when tools are partially unavailable
- [ ] **D-EXT** Framework extensibility:
  - add a third or fourth tool
  - show the agent chooses tools intelligently (arXiv for academic, Wikipedia for general)
  - a tool-routing evaluation measuring selection accuracy across question types
  - show the abstraction that makes adding a tool a plug-in operation

### Deliverables (R)
- [ ] **R1** "Push your code to a public GitHub repository."
- [ ] **R2** "Your solution must be written in Python."
- [ ] **R3** Single-command run (e.g. `python agent.py "your question"`).
- [ ] **R4** README.md with four named sections:
  - (a) Architecture overview and key design decisions
  - (b) Setup and run instructions, including Python version and dependency install
  - (c) Agent performance summary and honest assessment of limitations
  - (d) What you would do differently with more time
- [ ] **R5** "Include your prompt-log.md capturing all AI tool interactions (required)."
- [ ] **R6** "Your agent should be runnable and testable without paid API keys."

### Evaluation / grading signals (G)
- [ ] **G1** "We evaluate both what you built and how you built it (via your prompt log and commit history)."
- [ ] **G2** "Building with AI is required and expected — what matters is how effectively you direct it."
- [ ] **G3** Depth beats breadth, and polish beats rushing (see A11 and Tier 3).

---

## 2. Ambiguities and assumptions

### What the spec says about constraints
- **Libraries:** the spec is silent beyond "must be written in Python." It does not name or ban agent frameworks such as LangChain or LlamaIndex. It says "No orchestration framework" exists at the company and that "you are building the foundation." **Assumption:** write the orchestration loop, tool registry and tracer ourselves, with only thin dependencies (HTTP client, LLM client SDK, pytest).
- **APIs:** Wikipedia, arXiv and FRED are the named options. Any additional API must be free.
- **Prior code:** the spec is silent. The working directory is empty, so we start from scratch.
- **AI usage:** required and expected. It is graded through prompt-log.md and commit history, not hidden.
- **Time budget:** the spec is silent. **Assumption:** favour a polished two-tool core over breadth.
- **Grading:** the signals are listed under G1–G3.

### Ambiguities
| # | Ambiguity | Assumption |
|---|---|---|
| 1 | The tool table says "No Signup Required", but FRED needs a free key and Q5 is tagged FRED. | The agent must work with no key. Without `FRED_API_KEY`, the FRED tool disables itself, and Q5 falls back to Wikipedia with an explicit "no live data" caveat. With a key, Q5 gets real data. The README says which results used which. |
| 2 | Local model or free cloud tier? This is the user's call. | Recommended default: a local model via Ollama (nothing to sign up for, reproducible, no rate limits), behind an LLM-client interface so a free cloud provider can be swapped in. Free-tier limits and current model names are not stated from memory; **verify at M1**. |
| 3 | Where do the 8 outputs live? | `outputs/q1.md` to `outputs/q8.md`, plus a summary table in the README. Traces are stored separately. |
| 4 | What do "recent" (Q3, Q6) and "current" (Q5) mean? | "Recent" means roughly the last 3–5 years, enforced by sorting arXiv by date and showing dates in citations. "Current" means the latest FRED observation, with its date printed. The run date is stamped in the output. |
| 5 | The scenario mentions "internal docs" but no tool exists for them. | Out of scope for the core. It could become the optional third tool (a local-docs search) if D-EXT is chosen. |
| 6 | Which single Tier 3 item? This is the user's call. | Recommended: **D-EVAL** (evaluation harness). It fits the "no eval harness" scenario, it exercises Q7's refusal behaviour, and it is the most defensible to review. D-EXT is the runner-up. |
| 7 | JSON or SQLite for traces? | One JSON file per run (easy to read), plus a SQLite index for queries. If only one, SQLite with JSON columns. |
| 8 | How is the "tool-retrieved vs. inference" split shown? | The answer has two labelled parts: "From sources" (each claim carries a `[n]` citation) and "Model inference / caveats" (uncited). |
| 9 | How is Q7 (out-of-scope) handled? | The agent checks scope at planning. It declines or caveats and does not invent citations. |
| 10 | What does "single-pass" mean for M4? | A flag (e.g. `--max-steps 1`) runs plan, one round of tools, then synthesize. Multi-step allows refinement rounds. Both modes are run on the same questions for the comparison. |
| 11 | Python version? | The spec is silent. Assume 3.10 or later, declared in the README, in `pyproject.toml` or `requirements.txt`. |
| 12 | Small local models are weak at structured output. | Keep the loop simple (a JSON action format with validation and retry on parse failure). Say so honestly in the limitations section. |

---

## Decisions made (by Aida)

- **LLM backend:** Groq free tier, `openai/gpt-oss-120b`. Google AI Studio signup did not work. Tradeoff vs a local model is documented in the README.
- **Tier 3:** the evaluation harness (D-EVAL).
- **FRED without a key:** do not retrieve or guess. Say live unemployment data cannot be retrieved without the FRED key. Background from Wikipedia on how the rate is measured is allowed, labelled as general context that may be out of date. Never state a current rate from model memory.
- **Planner:** one tool call per step for now. Documented as a tradeoff in the README.
- **Unverified claims (M3):** a claim whose evidence ID is unknown gets one repair retry; if still unknown it is moved out of the sourced section and labelled unverified, never dropped. Rationale (also in the README): dropping silently hides information, and demoting keeps retrieved information separate from the model's own inference.
- **Structured claims (M3):** the model returns `{text, evidence_ids}` claims (IDs like W1, A2, assigned per tool prefix); code writes the citation markers and validates the IDs. Pydantic is used for structured outputs where applicable.
- **Tool failures (M3 follow-up):** short timeout and backoff (3 attempts, 10s timeout), fallback to another registered tool, and a code-written Notices section that names the failed tool, the reason and any substitute. Unit tests use fakes only, with the network blocked.
- **README:** kept up to date with every milestone.
- **Layout:** uv, `src/` layout, CI with ruff, tests, gitleaks and a `ci-success` gate (done). Other items from the agent-chassis comparison (offline vs live evals, deterministic graders) are adopted as the milestones reach them.

---

## 3. Milestones

Each milestone ends runnable and committed. Closed checklist IDs are listed.

- **M0 — Repo hygiene.**
  - `git init`, `.gitignore`, and confirm the git author is Aida Ploco only, with no Claude trailer on any commit.
  - One commit per milestone.
  - Commit `prompt-log.md` and `.claude/` as they stand.
  - **Do not push or create the public repo until Aida says so in that turn.**
  - Closes: part of G1, R5.
- **M1 — Thinnest end-to-end slice.**
  - One tool (Wikipedia search and summary), one LLM call, and `python main.py "question"` printing an answer with URL citations. (Entry point is `main.py`; the spec's `agent.py` is only an example, "e.g.". The orchestration code lives in `src/research_agent/agent.py`.)
  - Choose and verify the LLM backend here, and confirm free-tier or local facts at this point.
  - Closes: A1, A4 (partially), A10, R2, R3.
- **M2 — Second tool and a tool interface.**
  - Add arXiv behind a common `Tool` interface and a small registry.
  - Add the LLM planner that picks tools.
  - Closes: T1, T3, A3, S3.
- **M3 — Cited, split synthesis.** *(Implemented on branch `m3-structured-synthesis`: structured claims, evidence IDs, demotion, number grounding, out-of-scope refusal.)*
  - Source list with titles, IDs and URLs.
  - "From sources" and "Model inference" sections.
  - Scope check for Q7.
  - Closes: A7, A8, A9, Q7 behaviour.
- **M4 — Tracing.** *(Implemented on branch `m4-tracing`: Pydantic trace models, JSON files plus a SQLite index, a `trace_cli` to list, show and query.)*
  - Structured trace per run: step, tool, input, raw output, reasoning and final synthesis. Stored as JSON, with a SQLite index.
  - A small script to query or pretty-print a trace.
  - Closes: O1–O4.
- **M5 — Multi-step loop with state.** *(Implemented on branch `m5-multistep-loop`: planner actions, step cap, seen-set, failed-tool set, evidence budget, token pacing, `--single-pass`; the shared retry helper is a separate commit.)*
  - A ReAct-style loop with a step cap, a retrieved-items memory, and dedupe of repeated calls.
  - A single-pass flag for comparison.
  - Closes: M1–M3, A6 (design decision).
- **M6 — FRED tool.** *(Implemented on branch `m6-fred`: FRED tool, live-data notices, a figure guard on the model's own words; Q5 both ways in `outputs/`.)*
  - Optional key, graceful disable without it, and Q5 handled both ways.
  - Closes: T1 (third tool), Q5.
- **M7 — Run all 8 reference questions.**
  - Save outputs and traces.
  - Single-pass versus multi-step on at least two questions (candidates: Q4, Q6).
  - Closes: Q0–Q8, M4.
- **M8 — Tier 3 depth (one item).** *(Changed: not built. The Tier 3 item claimed is tool reliability and fallback, done in M2, M3 and M5; the evaluation harness is described in the README as what would have been built.)*
  - Recommended: the evaluation harness, with 15 or more questions, expected sources, must-include facts, must-not-include facts, and metrics for relevance, attribution and refusal.
  - Closes: one D item.
- **M9 — README and final audit.**
  - Four required sections, LLM tradeoffs, orchestration rationale, performance summary with limitations, and what to do differently.
  - Fresh-clone test from the README alone.
  - Confirm `prompt-log.md` is complete.
  - Closes: R4, A2, A5, A6, R6, G3.
- **M10 — Publish (needs explicit go-ahead).**
  - Create the public GitHub repo and push.
  - Closes: R1.

---

## 4. Risks and open items

### prompt-log.md (graded deliverable, R5) — status after M0 fix
- **Fixed:** the Stop hook fired but logged nothing. It ran without error (31-45 ms), yet no entry was written. Likely cause: the hook's `session_id` differed from the transcript's, and the script exited silently on a missing transcript path. It now uses `last_assistant_message` if the harness supplies it, falls back to the newest project transcript, and retries while the final message flushes. A gitignored `.claude/hooks/debug.log` records what the hook receives.
- **Not yet confirmed live:** it was tested on synthetic input and a truncated real transcript. A live "Response" entry must appear after this turn ends. If not, check `debug.log`.
- **Backfilled:** the first exchange and the responses to the 18:06 test prompt and the spec prompt were added, labelled "(reconstructed)".
- **Still true:** tool calls and their output are not logged, only prompts and prose replies.

### Other risks
- A small local model may produce unreliable JSON actions or weak synthesis.
- Free APIs fail or rate-limit (arXiv in particular), so retry and timeout handling is needed even outside the Tier 3 reliability option.
- Nothing is a git repo yet, and commit history is graded, so M0 comes first.
