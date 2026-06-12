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


def test_dispatch_catalog_schema_is_mixed_and_excludes_ax() -> None:
    # ADR-033: the canvas palette — work/query/diag autonomous, argv/nav held, no ax/agent/habit.
    from service.session import Session
    ok, body = Session._catalog_schema(types.SimpleNamespace(), "")
    by = {a["name"]: a for a in body["actions"]}
    assert by["summarize_file"]["autonomous"] is True and by["empty_trash"]["autonomous"] is False
    assert not any(a["kind"] in ("ax", "agent", "habit") for a in body["actions"])


def test_dispatch_enqueue_batch_queues_valid_and_rejects_unknown() -> None:
    from service.session import Session
    q = OvernightQueue()
    stub = types.SimpleNamespace(_overnight_queue=q)
    ok, body = Session._overnight_enqueue_batch(
        stub, [{"action": "find_file", "arg": "x"}, {"action": "bogus"}, {"action": "summarize_file", "arg": "/tmp/a"}])
    assert ok and body["queued"] == 2 and body["rejected"] == ["bogus"]
    assert len(q.list_all()) == 2


def test_dispatch_briefing_dismiss_done_purges() -> None:
    from service.session import Session
    q = OvernightQueue()
    t = q.enqueue("find_file", "x"); q.mark(t, "done")
    q.enqueue("empty_trash")                                   # stays pending
    ok, body = Session._briefing_dismiss_done(types.SimpleNamespace(_overnight_queue=q), "")
    assert ok and body["cleared"] == 1 and len(q.list_all()) == 1


if __name__ == "__main__":
    test_runner_runs_safe_holds_rest_and_completes()
    test_dispatch_overnight_enqueue_validates_against_catalog()
    test_dispatch_briefing_approve_executes_held_action()
    test_dispatch_catalog_schema_is_mixed_and_excludes_ax()
    test_dispatch_enqueue_batch_queues_valid_and_rejects_unknown()
    test_dispatch_briefing_dismiss_done_purges()
    print("service/test_overnight_runner: OK")


class _ErrRunner:
    """Stand-in whose read-only action REPORTS an error as an [ERROR:] string (never raises) — the
    exact case that was silently stamped 'done' before the fix."""
    def perform(self, name, arg=""):
        return "[ERROR: \"x.pdf\" is a local file or non-URL, not a web page. Use summarize_file.]"


def test_error_string_result_is_marked_failed_not_done() -> None:
    # The silent-failure bug: a safe action returning an [ERROR:] string must land as FAILED, not done.
    q, led = OvernightQueue(), HeldLedger()
    q.enqueue("read_article", "/Users/me/doc.pdf")          # safe (query) -> runs, returns [ERROR]
    runner = OvernightRunner(q, led, _ErrRunner(), lambda k, b: None)
    runner.start(); _drain(runner)
    row = q.list_all()[0]
    assert row["status"] == "failed"                        # not silently "done"
    assert row["result"].startswith("[ERROR")              # the error is preserved + surfaced
