"""ADR-053 backend: the run_at scheduling primitive (store + runner auto-activation) and the
failed-task `alternatives()` recovery routing. No UI, no real subprocess."""
import types

from actions import alternatives
from overnight import HeldLedger, OvernightQueue
from service.overnight_runner import OvernightRunner


class _Runner:
    def __init__(self):
        self.calls = []
    def perform(self, name, arg=""):
        self.calls.append((name, arg))
        return f"ran {name}"


# ── store: run_at column + time-aware queries ──
def test_enqueue_run_at_and_next_pending_hides_future() -> None:
    q = OvernightQueue()
    q.enqueue("report_system", "", run_at=None)            # manual -> always runnable
    q.enqueue("find_file", "later", run_at=10_000.0)       # scheduled far future
    # at t=0 only the manual task is runnable; the future one is invisible
    assert q.next_pending(now=0.0)["action"] == "report_system"
    assert q.due_scheduled(now=0.0) == 0
    # once its time arrives, the scheduled task becomes due
    assert q.due_scheduled(now=10_001.0) == 1
    rows = {r["action"]: r for r in q.list_all()}
    assert rows["find_file"]["run_at"] == 10_000.0 and rows["report_system"]["run_at"] is None


def test_migration_adds_run_at_to_preexisting_queue(tmp_path) -> None:
    import sqlite3
    p = str(tmp_path / "old.db")
    # simulate a pre-ADR-053 db: the queue table WITHOUT run_at
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE overnight_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,"
               " arg TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',"
               " result TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL)")
    db.execute("INSERT INTO overnight_queue(action,arg,created_at,updated_at) VALUES('report_system','',0,0)")
    db.commit(); db.close()
    q = OvernightQueue(p)                                  # opening it must add run_at, no data loss
    assert "run_at" in {r[1] for r in q._db.execute("PRAGMA table_info(overnight_queue)")}
    assert q.list_all()[0]["action"] == "report_system" and q.list_all()[0]["run_at"] is None


# ── runner: a due scheduled task auto-activates without a manual start ──
def test_runner_auto_activates_when_scheduled_task_is_due() -> None:
    q, led, ar = OvernightQueue(), HeldLedger(), _Runner()
    q.enqueue("report_system", "", run_at=1.0)             # scheduled in the past -> due now
    runner = OvernightRunner(q, led, ar, lambda k, b: None)
    assert not runner.active                               # never manually started
    for _ in range(5):                                     # tick() would call advance(); it self-starts
        runner.advance()
        if not runner.active:
            break
    assert ("report_system", "") in ar.calls              # ran without overnight_start
    assert q.list_all()[0]["status"] == "done"


def test_runner_leaves_future_task_untouched() -> None:
    q, led, ar = OvernightQueue(), HeldLedger(), _Runner()
    q.enqueue("report_system", "", run_at=10_000_000_000.0)  # year ~2286: never due in this test
    runner = OvernightRunner(q, led, ar, lambda k, b: None)
    for _ in range(3):
        runner.advance()
    assert ar.calls == [] and not runner.active and q.list_all()[0]["status"] == "pending"


# ── alternatives(): the routing-recovery matrix (pure) ──
def test_alternatives_routes_by_argument_shape() -> None:
    # the user's exact bug: web reader on a local path -> the local-file tools, web reader first
    assert alternatives("read_article", "/Users/me/PRD.pdf") == ["summarize_file", "read_file"]
    # right family, unreadable input -> the OTHER local-file tool
    assert alternatives("summarize_file", "/Users/me/scan.pdf") == ["read_file"]
    assert alternatives("read_file", "/Users/me/x.pdf") == ["summarize_file"]
    # a URL belongs to the web reader: local-file tool on a URL -> read_article
    assert alternatives("summarize_file", "https://example.com/x") == ["read_article"]
    # web reader failing on a URL (e.g. timeout) -> no tool swap (Retry instead)
    assert alternatives("read_article", "https://example.com/x") == []
    # out of family -> nothing
    assert alternatives("mute", "/x") == []


# ── session handlers: schedule_batch + action_alternatives ──
def _stub():
    from service.session import Session
    q, led, ar = OvernightQueue(), HeldLedger(), _Runner()
    s = types.SimpleNamespace(_overnight_queue=q, _held_ledger=led, _actions=ar,
                              _overnight=OvernightRunner(q, led, ar, lambda k, b: None))
    s._enqueue_items = types.MethodType(Session._enqueue_items, s)   # bind the real shared helper
    return s


def test_schedule_batch_requires_run_at_and_queues_with_it() -> None:
    from service.session import Session
    stub = _stub()
    ok, body = Session._overnight_schedule_batch(stub, [{"action": "report_system"}])  # missing run_at
    assert not ok and "run_at" in body["text"]
    ok, body = Session._overnight_schedule_batch(
        stub, {"items": [{"action": "summarize_file", "arg": "/tmp/a.pdf"}, {"action": "bogus"}],
               "run_at": 5_000.0})
    assert ok and body["queued"] == 1 and body["rejected"] == ["bogus"] and body["run_at"] == 5_000.0
    assert stub._overnight_queue.list_all()[0]["run_at"] == 5_000.0


def test_action_alternatives_enriches_with_label_and_tag() -> None:
    from service.session import Session
    ok, body = Session._action_alternatives(_stub(), {"action": "read_article", "arg": "/tmp/PRD.pdf"})
    assert ok
    names = [a["name"] for a in body["alternatives"]]
    assert names == ["summarize_file", "read_file"]
    assert all("label" in a and "autonomous" in a for a in body["alternatives"])


# ── ADR-054: intent_parse wires grammar -> (fake) model -> gate ──
def test_intent_parse_accepts_validated_intent() -> None:
    import types
    from service.session import Session

    class _LLM:
        def generate_json(self, s, u, g, max_tokens=200):
            return '{"action":"summarize_file","arg":"/tmp/a.pdf","timing":null}'
    ok, body = Session._intent_parse(types.SimpleNamespace(_llm=_LLM()), {"text": "summarize /tmp/a.pdf"})
    assert ok and body["ok"] and body["intent"]["action"] == "summarize_file"
    assert body["intent"]["arg"] == "/tmp/a.pdf"


def test_intent_parse_returns_clarify_for_none_and_missing_model() -> None:
    import types
    from service.session import Session

    class _None:
        def generate_json(self, s, u, g, max_tokens=200):
            return '{"action":"none","arg":"","timing":null}'
    ok, body = Session._intent_parse(types.SimpleNamespace(_llm=_None()), {"text": "order a pizza"})
    assert ok and body["ok"] is False and "clarify" in body
    # no generate_json on the handle (model not loaded) -> graceful clarify, never a crash
    ok, body = Session._intent_parse(types.SimpleNamespace(_llm=object()), {"text": "hi"})
    assert ok and body["ok"] is False
