import time

from app.api.ws import _RateLimiter


def test_rate_limiter_allows_the_quota_then_refuses():
    limiter = _RateLimiter(limit=3, window_seconds=60.0)
    assert [limiter.allow() for _ in range(4)] == [True, True, True, False]


def test_rate_limiter_recovers_when_hits_age_out():
    limiter = _RateLimiter(limit=1, window_seconds=0.05)
    assert limiter.allow() is True
    assert limiter.allow() is False
    time.sleep(0.06)
    assert limiter.allow() is True
