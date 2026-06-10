"""ADR-031 durable state: the queue's lifecycle + restart-safety, and the held-ledger SURVIVING a
simulated daemon restart (the whole reason it's not the in-memory consent ledger)."""
import tempfile

from overnight import HeldLedger, OvernightQueue


def test_queue_enqueue_next_pending_and_mark() -> None:
    q = OvernightQueue(":memory:")
    t1 = q.enqueue("find_file", "spec")
    t2 = q.enqueue("report_system", "")
    assert q.next_pending()["id"] == t1                 # FIFO by id
    q.mark(t1, "done", result="found 3 files")
    nxt = q.next_pending()
    assert nxt["id"] == t2 and nxt["action"] == "report_system"
    rows = {r["id"]: r for r in q.list_all()}
    assert rows[t1]["status"] == "done" and rows[t1]["result"] == "found 3 files"
    q.close()


def test_reset_running_reverts_zombies_to_pending() -> None:
    q = OvernightQueue(":memory:")
    tid = q.enqueue("find_file", "x")
    q.mark(tid, "running")                               # crash mid-task
    q.reset_running()
    assert q.next_pending()["id"] == tid                # self-healed back to pending
    q.close()


def test_held_ledger_survives_a_restart() -> None:
    path = tempfile.mktemp(suffix=".db")
    led = HeldLedger(path)
    hid = led.hold(task_id=7, action="empty_trash", arg="", reason="argv requires approval")
    led.close()                                         # simulate the daemon recycling at 3 AM
    reopened = HeldLedger(path)                         # fresh process, same db
    pend = reopened.pending()
    assert len(pend) == 1 and pend[0]["id"] == hid and pend[0]["action"] == "empty_trash"
    reopened.resolve(hid, accepted=True)
    assert reopened.pending() == []                     # resolved -> no longer awaiting
    assert reopened.get(hid)["disposition"] == "approved"
    reopened.close()


if __name__ == "__main__":
    test_queue_enqueue_next_pending_and_mark()
    test_reset_running_reverts_zombies_to_pending()
    test_held_ledger_survives_a_restart()
    print("overnight/test_store: OK")
