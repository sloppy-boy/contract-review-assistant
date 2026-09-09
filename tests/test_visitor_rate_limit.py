from app.visitor_rate_limit import SlidingWindowRateLimiter


def test_sliding_window_limits_one_key_and_isolates_another():
    limiter = SlidingWindowRateLimiter(limit=3, window_seconds=3600)

    assert [limiter.allow("ip-a", now=1000)[0] for _ in range(3)] == [True, True, True]
    allowed, retry_after = limiter.allow("ip-a", now=1000)

    assert allowed is False
    assert retry_after == 3600
    assert limiter.allow("ip-b", now=1000)[0] is True
    assert limiter.allow("ip-a", now=4601)[0] is True
