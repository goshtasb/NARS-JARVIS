"""v1.24.0 Sprint 1: the FSEvents-edge receptor. Re-validates the denylist (defense-in-depth), enforces
the size cap + micro-ingest budget, dedupes, and keeps the coarse-rescan path bounded — all without the
Swift helper (we feed it the JSON payloads the edge would flush)."""
import os

import sentinel.ingest_watch as iw
from sentinel.ingest_watch import IngestWatcher


def _mk(root: str, rel: str, size: int = 10) -> str:
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("x" * size)
    return p


def test_paths_payload_filters_and_enqueues(tmp_path) -> None:
    w = IngestWatcher(":memory:", watch_dir=str(tmp_path))
    good = _mk(str(tmp_path), "notes.md")
    code = _mk(str(tmp_path), "app.py")
    noise = _mk(str(tmp_path), "node_modules/lib.js")        # denied dir
    binary = _mk(str(tmp_path), "logo.png")                 # non-allowlisted ext
    n = w.ingest_payload({"paths": [good, code, noise, binary]})
    assert n == 2 and {r["path"] for r in w.queue.all()} == {good, code}
    w.queue.close()


def test_denylist_revalidated_even_if_edge_missed(tmp_path) -> None:
    # an allowlisted extension INSIDE a denied dir must still be dropped daemon-side (we never trust the wire)
    w = IngestWatcher(":memory:", watch_dir=str(tmp_path))
    noise = _mk(str(tmp_path), ".git/config.json")
    assert w.ingest_payload({"paths": [noise]}) == 0 and w.queue.count() == 0
    w.queue.close()


def test_dedupe_same_path(tmp_path) -> None:
    w = IngestWatcher(":memory:", watch_dir=str(tmp_path))
    p = _mk(str(tmp_path), "a.md")
    w.ingest_payload({"paths": [p]})
    w.ingest_payload({"paths": [p]})                        # same file again -> one row (UNIQUE path)
    assert w.queue.count() == 1
    w.queue.close()


def test_rescan_marker_prunes_denied_and_walks(tmp_path) -> None:
    w = IngestWatcher(":memory:", watch_dir=str(tmp_path))
    _mk(str(tmp_path), "doc1.md")
    _mk(str(tmp_path), "sub/doc2.txt")
    _mk(str(tmp_path), "node_modules/dep/index.js")         # pruned, never descended
    n = w.ingest_payload({"rescan": str(tmp_path)})
    assert n == 2 and {os.path.basename(r["path"]) for r in w.queue.all()} == {"doc1.md", "doc2.txt"}
    w.queue.close()


def test_oversized_file_not_queued(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(iw, "_HARD_CAP", 100)               # absurd-payload guard
    w = IngestWatcher(":memory:", watch_dir=str(tmp_path))
    assert w.ingest_payload({"paths": [_mk(str(tmp_path), "big.txt", size=500)]}) == 0
    w.queue.close()


def test_micro_ingest_budget_defers_heavy_on_low_battery(tmp_path, monkeypatch) -> None:
    w = IngestWatcher(":memory:", watch_dir=str(tmp_path))
    heavy = _mk(str(tmp_path), "heavy.md", size=20 * 1024)  # > 5 KB "light" threshold
    class _Batt:
        power_plugged = False
        percent = 20
    monkeypatch.setattr(w, "_battery", lambda: _Batt())
    w.ingest_payload({"paths": [heavy]})
    assert w.queue.all()[0]["status"] == "deferred"         # heavy + on battery <50% -> held for AC
    w.queue.close()


def test_out_of_watch_paths_rejected(tmp_path) -> None:
    # containment: an allowlisted file OUTSIDE the designated watch root must never be enqueued
    watch = tmp_path / "watched"; watch.mkdir()
    outside = tmp_path / "elsewhere"; outside.mkdir()
    w = IngestWatcher(":memory:", watch_dir=str(watch))
    inside = _mk(str(watch), "ok.md")
    evil = _mk(str(outside), "secret.md")
    n = w.ingest_payload({"paths": [inside, evil]})
    assert n == 1 and {os.path.basename(r["path"]) for r in w.queue.all()} == {"ok.md"}
    w.queue.close()


def test_rescan_outside_watch_rejected(tmp_path) -> None:
    watch = tmp_path / "watched"; watch.mkdir()
    outside = tmp_path / "elsewhere"; outside.mkdir()
    _mk(str(outside), "secret.md")
    w = IngestWatcher(":memory:", watch_dir=str(watch))
    assert w.ingest_payload({"rescan": str(outside)}) == 0 and w.queue.count() == 0   # arbitrary root refused
    w.queue.close()
