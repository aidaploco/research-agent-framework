# `tests/`

Run everything with:

```sh
uv run pytest
```

## Layout

| Path | What it covers |
|---|---|
| [`unit/`](unit/) | Fast tests that need no network and no API key. External calls (Wikipedia, Groq) are mocked. |
| [`fixtures/`](fixtures/) | Shared test data (recorded tool responses will live here). |
| [`conftest.py`](conftest.py) | Shared fixtures. |

Live calls to Groq and the real APIs are never part of `tests/`; they belong to the evals.
