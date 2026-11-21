# Q5, without a FRED API key

Question: What is the current US unemployment rate and how has it changed over the past year?

Command: `FRED_API_KEY= uv run python main.py "<question>"` (default multi-step mode), run on 2026-11-21.
FRED fails at once without a network call; the code-written Notices section says no current figure
is available and none is stated. The agent then used Wikipedia. The model still listed dated
historical figures from the article despite being told to give only definitions: they are sourced
and dated, and the notice says anything below may be out of date, but they are not what was asked
for. No current rate appears and no figure comes from the model's memory. A single live run.

```
## Notices
- The fred tool was unavailable (FRED_API_KEY is not set, so live economic data cannot be retrieved (a free key: https://fred.stlouisfed.org/docs/api/api_key.html)). No current figure from fred is available, so none is stated here; anything below from other sources is general background that may be out of date.

## From sources
- As of September 2017, the U-3 unemployment rate in the United States was 4.2%, representing 6.8 million unemployed people. [W1]
- In September 2019, the U.S. unemployment rate dropped to 3.5%, near the lowest rate in 50 years. [W1]
- In April 2020, the unemployment rate rose to 14.7% due to the coronavirus pandemic. [W1]

## Gaps
The question asks for the current US unemployment rate and its change over the past year, but the provided sources do not contain any recent data to answer this.

## Model inference / caveats
- Caveat: The sources only provide unemployment rates for specific past dates (2017, 2019, 2020) and do not contain the current unemployment rate or its change over the past year.
- Caveat: No up-to-date figures are available because the live data source (FRED) was unavailable.

## Sources
[W1] Unemployment in the United States - https://en.wikipedia.org/wiki/Unemployment_in_the_United_States
```
