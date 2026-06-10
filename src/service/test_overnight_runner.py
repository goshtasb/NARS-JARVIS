"""ADR-031 runner + dispatch: read-only tasks run unattended, everything else is held, and the morning
approval (briefing_resolve) is the consent gate that actually executes a held action."""
import types

from overnight import HeldLedger, OvernightQueue
from service.overnight_runner import OvernightRunner


class _Runner:
    """ActionRunner stand-in — records perform() calls instead of touching the OS."""
    def __init__(self):
        self.calls = []
    def perform(self, name, arg=""):
        self.calls.append((name, arg))
        return f"ran {name}"


def _drain(runner):
    for _ in range(20):
        runner.advance()
        if not runner.active:
            break


def test_runner_runs_safe_holds_rest_and_completes() -> None:
    q, led, ar = OvernightQueue(), HeldLedger(), _Runner()
    q.enqueue("find_file", "spec")     # safe (query)
    q.enqueue("empty_trash", "")       # held (argv + confirm, destructive)
    q.enqueue("report_system", "")     # safe (diag)
    runner = OvernightRunner(q, led, ar, lambda k, b: None)
    assert runner.start() == 3
    _drain(runner)
    assert ("find_file", "spec") in ar.calls and ("report_system", "") in ar.calls
    assert ("empty_trash", "") not in ar.calls           # never run unattended
    held = led.pending()
    assert len(held) == 1 and held[0]["action"] == "empty_trash"
    st = {r["action"]: r["status"] for r in q.list_all()}
    assert st == {"find_file": "done", "empty_trash": "held", "report_system": "done"}
    assert not runner.active                              # drained -> inactive


def test_dispatch_overnight_enqueue_validates_against_catalog() -> None:
    from service.session import Session
    q, led, ar = OvernightQueue(), HeldLedger(), _Runner()
    stub = types.SimpleNamespace(_overnight_queue=q, _held_ledger=led, _actions=ar,
                                 _overnight=OvernightRunner(q, led, ar, lambda k, b: None))
    ok, _ = Session._overnight_enqueue(stub, {"action": "find_file", "arg": "spec"})
    assert ok
    ok, body = Session._overnight_enqueue(stub, "bogus_action")
    assert not ok and "unknown action" in body["text"]   # only catalog actions can be queued


def test_dispatch_briefing_approve_executes_held_action() -> None:
    from service.session import Session
    q, led, ar = OvernightQueue(), HeldLedger(), _Runner()
    stub = types.SimpleNamespace(_overnight_queue=q, _held_ledger=led, _actions=ar,
                                 _overnight=OvernightRunner(q, led, ar, lambda k, b: None))
    Session._overnight_enqueue(stub, {"action": "find_file", "arg": "spec"})
    Session._overnight_enqueue(stub, {"action": "empty_trash"})
    Session._overnight_start(stub, "")
    _drain(stub._overnight)
    ok, body = Session._briefing(stub, "")
    assert any(d["action"] == "find_file" for d in body["done"])
    assert len(body["held"]) == 1 and body["held"][0]["action"] == "empty_trash"
    hid = body["held"][0]["id"]
    ok, _ = Session._briefing_resolve(stub, {"id": hid, "accepted": True})
    assert ok and ("empty_trash", "") in ar.calls         # approval IS the consent gate -> it runs now
    # denying a second time is a safe no-op (already resolved)
    ok, body = Session._briefing_resolve(stub, {"id": hid, "accepted": False})
    assert ok and "no held action" in body["text"]


if __name__ == "__main__":
    test_runner_runs_safe_holds_rest_and_completes()
    test_dispatch_overnight_enqueue_validates_against_catalog()
    test_dispatch_briefing_approve_executes_held_action()
    print("service/test_overnight_runner: OK")
