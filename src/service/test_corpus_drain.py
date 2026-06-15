"""Slice 4 — the AC-gated serial bulk-ingest drainer, at the Session boundary. Mirrors the file-lands-in-vault
pattern: construct the real Session, monkeypatch the off-loop spawn / mock AC power, drive the methods
directly (no select loop, no model). Proves: idles on battery, resumes on AC, stays serial, settles the
durable queue + emits corpus_progress on completion, and dedups already-ingested contracts at ingest."""
import os

from service.session import Session
from triage.devscan import file_doc_id
from triage.parameter import normalize
from triage.structure import Anchor


class _FakeTriageJob:
    """Stand-in for the off-loop TriageJob: a pending tick, a result body, then EOF — no subprocess."""
    def __init__(self, body, fd=-11):
        self._events = [("pending", {"salient_count": 1}), ("result", body), ("eof", None)]
        self._fd = fd
    def fileno(self):
        return self._fd
    def read(self):
        evs, self._events = self._events, [("eof", None)]
        return evs
    def cleanup(self):
        pass


def test_drain_idles_on_battery_resumes_on_ac(tmp_path) -> None:
    s = Session(db_path=str(tmp_path / "j.db"))
    try:
        spawned: list = []
        s._spawn_triage = lambda path, tid=None: spawned.append((path, tid))   # record, don't subprocess
        s._overnight_queue.enqueue("triage_file", "/x/a.pdf")
        s._on_ac_power = lambda: False                      # battery -> the brutal extraction must not run
        s._drain_corpus()
        assert spawned == []
        s._on_ac_power = lambda: True                       # plugged in -> the durable queue resumes
        s._drain_corpus()
        assert len(spawned) == 1 and spawned[0][0] == "/x/a.pdf" and spawned[0][1] is not None
    finally:
        s.close()


def test_drain_is_serial_one_bulk_job_at_a_time(tmp_path) -> None:
    s = Session(db_path=str(tmp_path / "j.db"))
    try:
        spawned: list = []
        s._spawn_triage = lambda path, tid=None: spawned.append((path, tid))
        s._on_ac_power = lambda: True
        s._overnight_queue.enqueue("triage_file", "/x/a.pdf")
        s._overnight_queue.enqueue("triage_file", "/x/b.pdf")
        s._triage_jobs[-99] = {"job": None, "doc": "a.pdf", "body": None, "tid": 1}   # one already in flight
        s._drain_corpus()
        assert spawned == []                                # serial: never spawn a second concurrently
        del s._triage_jobs[-99]
        s._drain_corpus()
        assert len(spawned) == 1                            # now it picks the next pending one
    finally:
        s.close()


def test_bulk_completion_settles_queue_and_emits_progress(tmp_path) -> None:
    events: list = []
    s = Session(db_path=str(tmp_path / "j.db"), on_event=lambda k, b: events.append((k, b)))
    try:
        tid = s._overnight_queue.enqueue("triage_file", "/x/a.pdf")
        body = {"doc": "a.pdf", "doc_id": "h", "salient_count": 1, "state": "empty", "findings": []}
        fake = _FakeTriageJob(body)
        s._triage_jobs[fake.fileno()] = {"job": fake, "doc": "a.pdf", "body": None, "tid": tid}
        s._read_triage_job(fake.fileno())
        row = [r for r in s._overnight_queue.list_all() if r["id"] == tid][0]
        assert row["status"] == "done"                      # the bulk queue row was settled
        prog = [b for k, b in events if k == "corpus_progress"]
        assert prog and prog[-1]["done"] == 1 and prog[-1]["total"] == 1
        assert fake.fileno() not in s._triage_jobs          # reaped
    finally:
        s.close()


def test_corpus_ingest_enqueues_and_dedups_already_ingested(tmp_path) -> None:
    folder = tmp_path / "contracts"; folder.mkdir()
    pa = folder / "a.pdf"; pa.write_bytes(b"%PDF-1.4 A")
    (folder / "b.pdf").write_bytes(b"%PDF-1.4 B")
    s = Session(db_path=str(tmp_path / "j.db"))
    try:
        # pretend a.pdf was already ingested: its content hash is in the ParamStore -> must be skipped
        p = normalize({"raw_quote": "72 hours", "role": "r", "value": "72", "unit": "hours",
                       "is_qualitative": False}, clause_type="ct", anchor=Anchor(1, (0.0, 0.0, 1.0, 1.0)))
        s._paramstore.add_parameters(file_doc_id(str(pa)), [p])
        ok, body = s.dispatch("corpus_ingest", {"path": str(folder)})
        assert ok and body["queued"] == 1 and body["skipped_dup"] == 1
        queued = [r for r in s._overnight_queue.list_all() if r["action"] == "triage_file"]
        assert len(queued) == 1 and os.path.basename(queued[0]["arg"]) == "b.pdf"   # only the new contract
    finally:
        s.close()


def test_corpus_ingest_rejects_non_folder(tmp_path) -> None:
    s = Session(db_path=str(tmp_path / "j.db"))
    try:
        ok, body = s.dispatch("corpus_ingest", {"path": str(tmp_path / "nope")})
        assert ok is False and "Not a folder" in body["text"]
    finally:
        s.close()
