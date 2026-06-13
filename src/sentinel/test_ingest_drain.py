"""v1.24.0 Sprint 2: the ingestion drain state machine. The capture event is a stale hint — every row is
re-validated at drain time. Proves the ghost-path (deleted -> gone), the identical-rewrite inference skip,
genuine-change re-ingest, the temporal-backoff retry cap for transient OS errors, crash recovery, and
out-of-scope rejection."""
import errno
import os
import time

from sentinel.ingest_drain import IngestDrain
from sentinel.ingest_queue import IngestQueue


def _mk(root: str, name: str, body: str = "hello") -> str:
    p = os.path.join(root, name)
    with open(p, "w") as f:
        f.write(body)
    return p


def _setup(tmp_path, ingested, **kw):
    q = IngestQueue(":memory:")
    d = IngestDrain(q, str(tmp_path), ingest_fn=lambda p: ingested.append(p), **kw)
    return q, d


def test_deleted_file_transitions_to_gone(tmp_path) -> None:
    ingested: list = []
    q, d = _setup(tmp_path, ingested)
    p = _mk(str(tmp_path), "a.md"); q.enqueue(p, 5)
    os.remove(p)                                       # the ghost path: gone before the drain wakes
    d.drain_once()
    assert q.all()[0]["status"] == "gone" and ingested == []   # terminal, no exception, no inference


def test_identical_rewrite_skips_inference(tmp_path) -> None:
    ingested: list = []
    q, d = _setup(tmp_path, ingested)
    p = _mk(str(tmp_path), "doc.md", "the same content"); q.enqueue(p, 16)
    d.drain_once()
    assert ingested == [p] and q.all()[0]["status"] == "done"
    time.sleep(0.01)
    _mk(str(tmp_path), "doc.md", "the same content")    # rewrite identical bytes (new mtime) + re-capture
    q.enqueue(p, 16)
    d.drain_once()
    assert ingested == [p]                               # same hash -> NOT re-ingested
    assert q.all()[0]["status"] == "done"


def test_changed_content_re_ingests(tmp_path) -> None:
    ingested: list = []
    q, d = _setup(tmp_path, ingested)
    p = _mk(str(tmp_path), "doc.md", "v1"); q.enqueue(p, 2); d.drain_once()
    _mk(str(tmp_path), "doc.md", "v2 is different"); q.enqueue(p, 14); d.drain_once()
    assert ingested == [p, p]                            # genuinely changed content -> re-ingested


def test_transient_oserror_backs_off_then_gone(tmp_path) -> None:
    ingested: list = []
    def flaky_stat(_path):
        raise OSError(errno.ENXIO, "device not configured")   # an unmounted external drive
    clock = [1000.0]
    q, d = _setup(tmp_path, ingested, cap=3, backoff_s=3600.0,
                  clock=lambda: clock[0], stat_fn=flaky_stat)
    p = _mk(str(tmp_path), "a.md"); q.enqueue(p, 5)
    d.drain_once()                                       # attempt 1 -> backoff
    row = q.all()[0]
    assert row["status"] == "pending" and row["attempts"] == 1 and row["next_attempt_at"] == 1000.0 + 3600
    assert d.drain_once() is None                        # still inside the backoff window -> not eligible
    clock[0] = 1000.0 + 3601; d.drain_once()             # attempt 2
    assert q.all()[0]["attempts"] == 2
    clock[0] += 3601; d.drain_once()                     # attempt 3 -> at cap -> terminal
    assert q.all()[0]["status"] == "gone" and ingested == []


def test_reset_running_recovers_crash(tmp_path) -> None:
    q = IngestQueue(":memory:")
    p = _mk(str(tmp_path), "a.md"); q.enqueue(p, 5)
    q.claim_next(now=time.time(), on_ac=True)            # claimed -> running, then "crash"
    assert q.all()[0]["status"] == "running"
    assert q.reset_running() == 1 and q.all()[0]["status"] == "pending"
    q.close()


def test_out_of_scope_path_is_gone(tmp_path) -> None:
    ingested: list = []
    watch = tmp_path / "w"; watch.mkdir()
    outside = tmp_path / "o"; outside.mkdir()
    q = IngestQueue(":memory:")
    d = IngestDrain(q, str(watch), ingest_fn=lambda p: ingested.append(p))
    evil = _mk(str(outside), "x.md"); q.enqueue(evil, 5)
    d.drain_once()
    assert q.all()[0]["status"] == "gone" and ingested == []   # outside the watch root -> never ingested


def test_deferred_held_until_ac(tmp_path) -> None:
    ingested: list = []
    q, d = _setup(tmp_path, ingested)
    p = _mk(str(tmp_path), "heavy.md", "x" * 100); q.enqueue(p, 100, status="deferred")
    assert d.drain_once(on_ac=False) is None and ingested == []   # on battery -> not eligible
    d.drain_once(on_ac=True)                                       # on AC -> drained
    assert ingested == [p] and q.all()[0]["status"] == "done"
