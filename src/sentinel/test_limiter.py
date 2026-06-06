"""Proof tests for the token-bucket backstop: capacity cap + time-based refill."""
from sentinel.limiter import BucketState, try_consume


def test_allows_up_to_capacity_then_denies() -> None:
    state = BucketState(tokens=3.0, last_refill=0.0)
    allowed: list[bool] = []
    for _ in range(5):  # no time passes -> no refill
        state, ok = try_consume(state, 0.0, rate=5.0, capacity=10.0)
        allowed.append(ok)
    assert allowed == [True, True, True, False, False]


def test_refills_over_time() -> None:
    state = BucketState(tokens=0.0, last_refill=0.0)
    state, ok = try_consume(state, 0.0, rate=5.0, capacity=10.0)
    assert ok is False  # empty
    state, ok = try_consume(state, 1.0, rate=5.0, capacity=10.0)  # +5 tokens after 1s
    assert ok is True


if __name__ == "__main__":
    test_allows_up_to_capacity_then_denies()
    test_refills_over_time()
    print("sentinel/test_limiter: OK")
