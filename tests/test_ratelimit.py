"""Sliding-window limiter used for unlock attempts and AI calls."""
from __future__ import annotations

from backend.ratelimit import HOUR, MINUTE, RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_per_minute_and_per_hour_windows():
    clock = Clock()
    limiter = RateLimiter([(5, MINUTE), (30, HOUR)], clock=clock)
    assert [limiter.hit("ip") for _ in range(5)] == [None] * 5
    assert limiter.hit("ip") == 60  # the oldest hit leaves the minute window in 60 s
    clock.now += 30
    assert limiter.hit("ip") == 30
    clock.now += 30.5
    assert limiter.hit("ip") is None  # the minute window has room again
    assert limiter.hit("other-ip") is None  # keys are independent


def test_hour_limit_kicks_in_after_several_minutes():
    clock = Clock()
    limiter = RateLimiter([(5, MINUTE), (30, HOUR)], clock=clock)
    allowed = 0
    for _ in range(12):  # 12 minutes, 5 attempts each
        for _ in range(5):
            if limiter.hit("ip") is None:
                allowed += 1
        clock.now += MINUTE
    assert allowed == 30
    retry_after = limiter.hit("ip")
    assert retry_after is not None and retry_after > MINUTE


def test_zero_disables_a_rule():
    limiter = RateLimiter([(0, MINUTE)])
    assert all(limiter.hit("ip") is None for _ in range(100))
