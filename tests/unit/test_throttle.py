from app.security.throttle import LoginThrottle


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_blocks_at_the_limit_and_recovers_after_the_window():
    clock = FakeClock()
    t = LoginThrottle(max_failures=3, window_seconds=60, clock=clock)
    t.record_failure("a@x.test")
    t.record_failure("a@x.test")
    assert t.is_blocked("a@x.test") is False
    t.record_failure("a@x.test")
    assert t.is_blocked("a@x.test") is True
    clock.now += 61
    assert t.is_blocked("a@x.test") is False


def test_keys_are_independent():
    t = LoginThrottle(max_failures=1, window_seconds=60, clock=FakeClock())
    t.record_failure("a@x.test")
    assert t.is_blocked("a@x.test") is True
    assert t.is_blocked("b@x.test") is False


def test_reset_clears_failures():
    t = LoginThrottle(max_failures=2, window_seconds=60, clock=FakeClock())
    t.record_failure("a@x.test")
    t.record_failure("a@x.test")
    t.reset("a@x.test")
    assert t.is_blocked("a@x.test") is False


def test_old_failures_fall_out_of_the_window_one_by_one():
    clock = FakeClock()
    t = LoginThrottle(max_failures=2, window_seconds=10, clock=clock)
    t.record_failure("a")
    clock.now += 6
    t.record_failure("a")
    clock.now += 5  # first failure is now 11s old, second is 5s old
    assert t.is_blocked("a") is False
