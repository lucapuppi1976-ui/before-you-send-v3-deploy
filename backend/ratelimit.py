"""In-memory sliding-window rate limits per client IP (single worker process)."""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable, Optional, Sequence

MINUTE = 60.0
HOUR = 3600.0
DAY = 86400.0


class RateLimiter:
    """Allow at most `limit` hits per `window` seconds for each key, for every (limit, window) rule.

    A limit of 0 disables that rule. Blocked attempts are not recorded.
    """

    def __init__(self, rules: Sequence[tuple[int, float]], clock: Callable[[], float] = time.monotonic) -> None:
        self.rules = [(limit, window) for limit, window in rules if limit > 0]
        self._longest = max((window for _, window in self.rules), default=0.0)
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def hit(self, key: str) -> Optional[int]:
        """Record a hit and return None, or return the Retry-After seconds if the key is over a limit."""
        if not self.rules:
            return None
        now = self._clock()
        with self._lock:
            self._calls += 1
            if self._calls % 1024 == 0:
                self._sweep(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] <= now - self._longest:
                hits.popleft()
            retry_after = 0.0
            for limit, window in self.rules:
                in_window = [stamp for stamp in hits if stamp > now - window]
                if len(in_window) >= limit:
                    # Free again when the oldest hit that keeps the count at the limit leaves the window.
                    retry_after = max(retry_after, in_window[len(in_window) - limit] + window - now)
            if retry_after > 0:
                return max(1, math.ceil(retry_after))
            hits.append(now)
            return None

    def _sweep(self, now: float) -> None:
        for key in [key for key, hits in self._hits.items() if not hits or hits[-1] <= now - self._longest]:
            del self._hits[key]
