"""Scheduler with per-source rate budgets.

The budget is a ceiling, not a target. Sources are polled at the slowest
cadence that still answers the question, because getting a source blocked costs
far more than a few hours of staleness.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class RateBudget:
    source_id: str
    max_requests_per_minute: int
    min_seconds_between_requests: Decimal


class RateLimiter:
    """Token-bucket plus a hard minimum gap between requests."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._window: dict[str, list[float]] = defaultdict(list)

    def acquire(self, budget: RateBudget, now: Optional[float] = None) -> float:
        """Return the number of seconds to wait before the next request."""
        now = now if now is not None else time.monotonic()

        gap_wait = 0.0
        last = self._last.get(budget.source_id)
        if last is not None:
            elapsed = now - last
            gap_wait = max(0.0, float(budget.min_seconds_between_requests) - elapsed)

        window = [t for t in self._window[budget.source_id] if now - t < 60.0]
        self._window[budget.source_id] = window
        window_wait = 0.0
        if len(window) >= budget.max_requests_per_minute:
            window_wait = 60.0 - (now - window[0])

        wait = max(gap_wait, window_wait)
        if wait == 0.0:
            self._last[budget.source_id] = now
            window.append(now)
        return wait
