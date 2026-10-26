"""Sliding-window limit on failed sign-ins, keyed by account email.
In-process only. A multi-replica deployment should keep these counters in Redis."""
from __future__ import annotations

import time
from collections import deque
from typing import Callable


class LoginThrottle:
    def __init__(self, max_failures: int, window_seconds: float,
                 clock: Callable[[], float] = time.monotonic):
        self.max_failures = max_failures
        self.window = window_seconds
        self.clock = clock
        self._failures: dict[str, deque[float]] = {}

    def _prune(self, key: str) -> int:
        """Drop failures outside the window and return how many remain."""
        hits = self._failures.get(key)
        if hits is None:
            return 0
        cutoff = self.clock() - self.window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if not hits:
            del self._failures[key]
            return 0
        return len(hits)

    def is_blocked(self, key: str) -> bool:
        return self._prune(key) >= self.max_failures

    def record_failure(self, key: str) -> None:
        self._prune(key)
        self._failures.setdefault(key, deque()).append(self.clock())

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
