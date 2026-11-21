# Q5, with a FRED API key

Question: What is the current US unemployment rate and how has it changed over the past year?

Command: `uv run python main.py "<question>"` (default multi-step mode), run on 2026-11-21 with `FRED_API_KEY` set.
The agent searched FRED once (series UNRATE) and finished. A single live run; the reference-question
runs with traces are collected in a later milestone.

```
## From sources
- The US unemployment rate was 4.1 percent in October 2026. [F1]
- The unemployment rate fell by 0.3 percentage points over the past year, from 4.4 percent in September 2025 to 4.1 percent in October 2026. [F1]

## Model inference / caveats
- Caveat: The data are seasonally adjusted monthly observations ending in October 2026; more recent changes after that date are not captured.

## Sources
[F1] FRED: Unemployment Rate (UNRATE) - https://fred.stlouisfed.org/series/UNRATE
```
