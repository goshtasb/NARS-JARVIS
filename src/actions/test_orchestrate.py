"""ADR-049 Context Orchestration bootstrap — verified actuation, all offline (fake spawn, no real
osascript). Asserts the actuate argv, the read-back verify, tri-state outcomes, and input handling."""
from actions import orchestrate


class _Spawn:
    """Records argv; returns a canned `get volume settings` for the read-back. `readback` is the
    output-volume the verify step will see (simulating what macOS reports)."""
    def __init__(self, readback: int = 20) -> None:
        self.calls: list[list[str]] = []
        self._readback = readback
    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        out = (f"output volume:{self._readback}, input volume:50, alert volume:100, output muted:false"
               if argv[-1] == "get volume settings" else "")
        return type("R", (), {"stdout": out, "returncode": 0})()


def test_set_volume_actuates_then_verifies() -> None:
    spawn = _Spawn(readback=20)
    out = orchestrate.set_volume(spawn, "20")
    # actuate first (zero-TCC StandardAdditions, no `tell application`), then read-back to verify
    assert spawn.calls[0] == ["osascript", "-e", "set volume output volume 20"]
    assert spawn.calls[1] == ["osascript", "-e", "get volume settings"]
    assert "verified" in out and "20/100" in out


def test_set_volume_within_macos_step_tolerance_still_verifies() -> None:
    # macOS quantizes to ~6.25 steps: request 50, hardware lands at 44 -> still verified (within tolerance).
    out = orchestrate.set_volume(_Spawn(readback=44), "50")
    assert "verified" in out and "44/100" in out


def test_set_volume_reports_unverified_when_state_didnt_land() -> None:
    out = orchestrate.set_volume(_Spawn(readback=80), "20")   # asked 20, reads 80 -> NOT verified
    assert "not verified" in out and "80/100" in out


def test_set_volume_clamps_and_rejects_garbage() -> None:
    assert "111" not in orchestrate.set_volume(_Spawn(readback=100), "150")   # clamps to 100
    assert orchestrate.set_volume(_Spawn(), "loud").startswith("I need a number")


def test_actuate_failure_is_reported_not_raised() -> None:
    def boom(argv, **kw):
        raise OSError("osascript missing")
    assert orchestrate.set_volume(boom, "20").startswith("Couldn't set the volume")


def test_dispatch_unknown_action_refuses() -> None:
    assert orchestrate.dispatch("frobnicate", "", _Spawn()).startswith("I don't know that")


if __name__ == "__main__":
    test_set_volume_actuates_then_verifies()
    test_set_volume_within_macos_step_tolerance_still_verifies()
    test_set_volume_reports_unverified_when_state_didnt_land()
    test_set_volume_clamps_and_rejects_garbage()
    test_actuate_failure_is_reported_not_raised()
    test_dispatch_unknown_action_refuses()
    print("actions/test_orchestrate: OK")
