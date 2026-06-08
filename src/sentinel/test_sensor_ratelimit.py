"""ADR-015: the UI actuator (Sensor.hide/unhide) is token-bucket rate-limited so a context-manipulated
trigger can't spam app visibility. Tested with an injected clock + a fake helper stdin (no Swift)."""
from sentinel.sensor import _ACTUATE_CAPACITY, Sensor


class _FakeStdin:
    def __init__(self) -> None:
        self.closed = False
        self.lines: list[str] = []
    def write(self, s: str) -> None:
        self.lines.append(s.strip())
    def flush(self) -> None:
        pass


class _FakeProc:
    def __init__(self) -> None:
        self.stdin = _FakeStdin()


def _sensor_at(clock: list[float]) -> Sensor:
    s = Sensor(now=lambda: clock[0])
    s._proc = _FakeProc()  # type: ignore[assignment]  # bypass the real Swift helper
    return s


def test_burst_past_capacity_is_dropped() -> None:
    clock = [0.0]
    s = _sensor_at(clock)
    for _ in range(int(_ACTUATE_CAPACITY) + 3):
        s.hide("com.app")
    assert len(s._proc.stdin.lines) == int(_ACTUATE_CAPACITY)   # only capacity admitted
    assert s._actuate_overflow == 3                              # the rest dropped + counted


def test_tokens_refill_over_time() -> None:
    clock = [0.0]
    s = _sensor_at(clock)
    for _ in range(int(_ACTUATE_CAPACITY)):
        s.hide("com.app")                                       # drain the bucket
    s.hide("com.app")
    assert s._actuate_overflow == 1                              # immediately after drain -> dropped
    clock[0] = 100.0                                            # plenty of time to refill
    s.hide("com.app")
    assert s._proc.stdin.lines[-1] == "hide com.app"            # admitted again after refill


def test_hide_and_unhide_share_one_budget() -> None:
    clock = [0.0]
    s = _sensor_at(clock)
    for _ in range(int(_ACTUATE_CAPACITY)):
        s.hide("com.app")                                       # consume the whole budget with hides
    s.unhide("com.app")
    assert s._actuate_overflow == 1                             # unhide draws the same bucket -> dropped
    assert "unhide com.app" not in s._proc.stdin.lines


def test_dry_run_never_actuates() -> None:
    # ADR-016: a dry_run Sensor is the hard backstop — it never sends a hide/unhide, regardless of
    # the rate budget (so even a missed call site in the loop cannot actuate live).
    clock = [0.0]
    s = Sensor(now=lambda: clock[0], dry_run=True)
    s._proc = _FakeProc()  # type: ignore[assignment]
    s.hide("com.app")
    s.unhide("com.app")
    assert s._proc.stdin.lines == []                            # nothing ever sent to the helper


if __name__ == "__main__":
    test_burst_past_capacity_is_dropped()
    test_tokens_refill_over_time()
    test_hide_and_unhide_share_one_budget()
    test_dry_run_never_actuates()
    print("sentinel/test_sensor_ratelimit: OK")
