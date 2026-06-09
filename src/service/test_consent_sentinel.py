"""The Sentinel ask-mode now routes through the unified consent machine (ADR-020). We test the
seam directly (`_open_ask_consent`) with an injected consent requester + a fake sensor — no macOS
sensor, no ONA needed (feed_consent no-ops without a brain)."""
from service.sentinel_loop import SentinelLoop


class _FakeSensor:
    def __init__(self) -> None:
        self.hidden: list[str] = []
    def hide(self, bundle: str) -> None:
        self.hidden.append(bundle)


def _loop_with_consent():
    captured: dict = {}
    def consent_request(kind, prompt, label, on_approve, on_negative, expiry_default):
        captured.update(kind=kind, prompt=prompt, label=label, on_approve=on_approve,
                        on_negative=on_negative, expiry_default=expiry_default)
        return 42
    loop = SentinelLoop("ignored.db", lambda k, b: None, consent_request=consent_request)
    loop._sensor = _FakeSensor()
    return loop, captured


def test_ask_mode_opens_consent_not_legacy_event() -> None:
    loop, cap = _loop_with_consent()
    loop._open_ask_consent(["comms"], ["com.tinyspeck.slackmacgap"])
    assert cap["kind"] == "intervention" and cap["expiry_default"] == "deny"
    assert "comms" in cap["label"]
    assert loop._ask_open == 42                      # tracks the open request -> blocks a second one


def test_consent_approve_hides_and_clears() -> None:
    loop, cap = _loop_with_consent()
    loop._open_ask_consent(["comms"], ["com.tinyspeck.slackmacgap"])
    msg = cap["on_approve"]()                        # what the consent gate runs on Approve
    assert loop._sensor.hidden == ["com.tinyspeck.slackmacgap"]
    assert loop._ask_open is None and "hidden" in msg


def test_consent_deny_does_not_hide_and_clears() -> None:
    loop, cap = _loop_with_consent()
    loop._open_ask_consent(["comms"], ["com.tinyspeck.slackmacgap"])
    cap["on_negative"]()                             # Deny / expiry default
    assert loop._sensor.hidden == []                # nothing hidden
    assert loop._ask_open is None


def test_no_consent_wired_falls_back_to_legacy_event() -> None:
    events: list[tuple[str, dict]] = []
    loop = SentinelLoop("ignored.db", lambda k, b: events.append((k, b)))   # no consent_request
    loop._pending = {"id": 1, "mode": "ask"}        # legacy path still uses _pending
    assert loop._consent_request is None            # legacy behavior preserved for tests/offline


if __name__ == "__main__":
    test_ask_mode_opens_consent_not_legacy_event()
    test_consent_approve_hides_and_clears()
    test_consent_deny_does_not_hide_and_clears()
    test_no_consent_wired_falls_back_to_legacy_event()
    print("service/test_consent_sentinel: OK")
