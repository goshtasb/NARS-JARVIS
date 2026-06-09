"""Unit tests for the consent shell (ADR-020): continuations fire exactly once on the right outcome,
expiry default-resolves, double-resolve is a safe no-op, and the events carry the right shape. A fake
clock makes expiry deterministic; a recording emit captures the event plane."""
from service.consent_service import ConsentService


class _Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t
    def __call__(self) -> float:
        return self.t


def _service(clock: _Clock | None = None, ttl: float = 120.0):
    events: list[tuple[str, dict]] = []
    svc = ConsentService(lambda k, b: events.append((k, b)), clock=clock or _Clock(), default_ttl=ttl)
    return svc, events


def test_request_emits_consent_request_with_deadline() -> None:
    clk = _Clock(1000.0)
    svc, events = _service(clk, ttl=60.0)
    rid = svc.request("action", "Run X?", "X")
    assert events == [("consent_request", {"id": rid, "kind": "action", "prompt": "Run X?",
                                           "label": "X", "expires_at": 1060.0, "server_now": 1000.0})]


def test_approve_runs_only_on_approve_thunk() -> None:
    svc, events = _service()
    fired: list[str] = []
    rid = svc.request("action", "Run?", "X",
                      on_approve=lambda: fired.append("approve") or "did it",
                      on_negative=lambda: fired.append("deny"))
    msg = svc.resolve(rid, accepted=True)
    assert fired == ["approve"] and msg == "did it"
    assert ("consent_closed", {"id": rid, "reason": "approved"}) in events


def test_deny_runs_negative_thunk() -> None:
    svc, events = _service()
    fired: list[str] = []
    rid = svc.request("action", "Run?", "X",
                      on_approve=lambda: fired.append("approve"),
                      on_negative=lambda: fired.append("deny"))
    svc.resolve(rid, accepted=False)
    assert fired == ["deny"]
    assert ("consent_closed", {"id": rid, "reason": "denied"}) in events


def test_double_resolve_is_noop() -> None:
    svc, _ = _service()
    fired: list[str] = []
    rid = svc.request("action", "Run?", "X", on_approve=lambda: fired.append("x"))
    svc.resolve(rid, accepted=True)
    msg = svc.resolve(rid, accepted=True)               # racing/duplicate click
    assert fired == ["x"]                               # ran exactly once (no double-execute)
    assert "expired" in msg or "no pending" in msg


def test_unknown_id_is_safe() -> None:
    svc, _ = _service()
    assert "no pending" in svc.resolve(123, accepted=True)


def test_expiry_applies_default_deny() -> None:
    clk = _Clock(1000.0)
    svc, events = _service(clk, ttl=30.0)
    fired: list[str] = []
    rid = svc.request("action", "Run?", "X", expiry_default="deny",
                      on_approve=lambda: fired.append("approve"),
                      on_negative=lambda: fired.append("deny"))
    clk.t = 1031.0                                      # past the 30s TTL
    svc.sweep()
    assert fired == ["deny"]                            # default-deny outcome ran
    assert ("consent_closed", {"id": rid, "reason": "expired"}) in events
    assert "no pending" in svc.resolve(rid, accepted=True)   # already swept -> gone


def test_expiry_default_approve_keeps() -> None:
    # An undo-style request (action already happened): a timeout means KEEP, not undo.
    clk = _Clock(1000.0)
    svc, _ = _service(clk, ttl=10.0)
    fired: list[str] = []
    svc.request("undo", "Keep?", "kept", expiry_default="approve",
                on_approve=lambda: fired.append("keep"), on_negative=lambda: fired.append("undo"))
    clk.t = 1011.0
    svc.sweep()
    assert fired == ["keep"]


def test_snapshot_shape() -> None:
    clk = _Clock(2000.0)
    svc, _ = _service(clk, ttl=60.0)
    svc.request("action", "Run?", "X")
    snap = svc.snapshot()
    assert snap["server_now"] == 2000.0
    assert len(snap["requests"]) == 1 and snap["requests"][0]["expires_at"] == 2060.0


def test_failing_continuation_does_not_raise() -> None:
    svc, events = _service()
    def boom():
        raise RuntimeError("nope")
    rid = svc.request("action", "Run?", "X", on_approve=boom)
    msg = svc.resolve(rid, accepted=True)               # must not propagate
    assert "approved: X" in msg                          # fell back to default message
    assert ("consent_closed", {"id": rid, "reason": "approved"}) in events


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("service/test_consent_service: OK")
