"""One retry policy for every external call: a few attempts, short backoff, each retry traced.

The model client, arXiv and Wikipedia each retry on their own failure signals (an HTTP status, a
timeout, a rate-limit message in a 200 body), but they share the loop: try, wait briefly, try
again, give up after a few attempts, and say what happened. Each failed attempt is noted in the
active trace, so the trace explains where the time went.
"""

import time
from collections.abc import Callable
from typing import TypeVar

from . import tracing

T = TypeVar("T")


class Retry(Exception):
    """Raised by an attempt to say: this failed in a way worth retrying."""

    def __init__(self, reason: str, wait: float | None = None):
        super().__init__(reason)
        self.reason = reason
        self.wait = wait  # overrides the backoff, e.g. a server's Retry-After


class RetriesExhausted(Exception):
    def __init__(self, reason: str, attempts: int):
        super().__init__(f"{reason} after {attempts} attempts")
        self.reason = reason
        self.attempts = attempts


def with_retries(
    label: str,
    attempt: Callable[[int], T],
    *,
    attempts: int,
    backoff: tuple[float, ...],
    on_retry: Callable[[str, float], None] | None = None,
) -> T:
    """Call `attempt(n)` until it returns, raising `Retry` to ask for another try.

    Waits `backoff[n]` seconds (the last value repeats) or the `Retry.wait` the attempt asked
    for. Any other exception propagates at once. After the last attempt, raises
    `RetriesExhausted` with the last reason. No wait follows the final attempt.
    """
    reason = "no attempt made"
    for n in range(attempts):
        try:
            return attempt(n)
        except Retry as r:
            reason = r.reason
            if n == attempts - 1:
                break
            wait = r.wait if r.wait is not None else backoff[min(n, len(backoff) - 1)]
            tracing.note_retry(
                label, f"attempt {n + 1} of {attempts} failed ({reason}); waiting {wait:g}s"
            )
            if on_retry:
                on_retry(reason, wait)
            time.sleep(wait)
    raise RetriesExhausted(reason, attempts)
