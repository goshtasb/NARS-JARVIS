"""Unit tests for the pure consent core (ADR-020): the ledger's one-shot resolve, expiry partition,
and wire-safe snapshot. Pure — no time, no I/O."""
from consent import APPROVED, DENIED, EXPIRED, OPEN, ConsentLedger, ConsentRequest


def _req(rid: int, expires_at: float = 100.0, expiry_default: str = "deny") -> ConsentRequest:
    return ConsentRequest(id=rid, kind="action", prompt=f"do {rid}?", label=f"act {rid}",
                          created_at=0.0, expires_at=expires_at, expiry_default=expiry_default)


def test_open_and_get() -> None:
    led = ConsentLedger()
    led.open(_req(1))
    assert led.get(1) is not None and led.get(1).status == OPEN
    assert led.get(99) is None


def test_resolve_is_one_shot() -> None:
    led = ConsentLedger()
    led.open(_req(1))
    first = led.resolve(1, accepted=True)
    assert first is not None and first.status == APPROVED
    assert led.resolve(1, accepted=True) is None        # second resolve -> gone (idempotent basis)
    assert led.get(1) is None


def test_resolve_denied_status() -> None:
    led = ConsentLedger()
    led.open(_req(1))
    assert led.resolve(1, accepted=False).status == DENIED


def test_resolve_unknown_is_none() -> None:
    assert ConsentLedger().resolve(42, accepted=True) is None


def test_expire_due_partitions_by_now() -> None:
    led = ConsentLedger()
    led.open(_req(1, expires_at=50.0))
    led.open(_req(2, expires_at=150.0))
    due = led.expire_due(now=100.0)
    assert [r.id for r in due] == [1] and due[0].status == EXPIRED
    assert led.get(1) is None and led.get(2) is not None  # only the overdue one was popped


def test_snapshot_lists_only_open_wire_safe() -> None:
    led = ConsentLedger()
    led.open(_req(1))
    led.open(_req(2))
    led.resolve(2, accepted=True)
    snap = led.snapshot()
    assert [r["id"] for r in snap] == [1]
    assert set(snap[0]) == {"id", "kind", "prompt", "label", "expires_at"}  # no continuation on the wire


if __name__ == "__main__":
    test_open_and_get()
    test_resolve_is_one_shot()
    test_resolve_denied_status()
    test_resolve_unknown_is_none()
    test_expire_due_partitions_by_now()
    test_snapshot_lists_only_open_wire_safe()
    print("consent/test_ledger: OK")
