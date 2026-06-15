"""Slice 4 — the bulk-ingest Functional-Core helpers: folder scan (filter + dedup + cap), the serial task
pick, and the cumulative progress body (counters + server-authored label). Pure; no daemon, no model."""
import os

from service.corpus import next_triage_task, progress_body, scan_folder
from triage.parameter import normalize
from triage.paramstore import ParamStore
from triage.structure import Anchor


def _mk(d, name, content=b"%PDF-1.4 test"):
    p = os.path.join(d, name)
    with open(p, "wb") as fh:
        fh.write(content)
    return p


def test_scan_folder_filters_hidden_nonpdf_and_dedups(tmp_path) -> None:
    d = str(tmp_path)
    _mk(d, "a.pdf"); _mk(d, "b.pdf"); _mk(d, "notes.txt"); _mk(d, ".hidden.pdf")
    scan = scan_folder(d, {"a.pdf"}, lambda p: os.path.basename(p))     # a.pdf already in the corpus
    assert [os.path.basename(p) for p in scan.to_enqueue] == ["b.pdf"]  # txt + hidden skipped silently
    assert scan.skipped_dup == 1 and scan.skipped_invalid == 0 and scan.truncated == 0


def test_scan_folder_drops_oversize(tmp_path) -> None:
    d = str(tmp_path); _mk(d, "big.pdf", b"x" * 100)
    scan = scan_folder(d, set(), lambda p: p, hard_cap=10)
    assert scan.to_enqueue == [] and scan.skipped_invalid == 1


def test_scan_folder_truncates_at_cap_without_silence(tmp_path) -> None:
    d = str(tmp_path); _mk(d, "a.pdf"); _mk(d, "b.pdf"); _mk(d, "c.pdf")
    scan = scan_folder(d, set(), lambda p: os.path.basename(p), max_files=2)
    assert len(scan.to_enqueue) == 2 and scan.truncated == 1            # the 3rd is reported, not dropped silently


def test_next_triage_task_is_fifo_pending_only() -> None:
    rows = [{"id": 3, "action": "triage_file", "status": "done"},
            {"id": 5, "action": "triage_file", "status": "pending"},
            {"id": 4, "action": "summarize_file", "status": "pending"},   # other action -> ignored
            {"id": 7, "action": "triage_file", "status": "pending"}]
    assert next_triage_task(rows)["id"] == 5                            # lowest pending id
    assert next_triage_task([]) is None
    assert next_triage_task([{"id": 1, "action": "triage_file", "status": "running"}]) is None


def test_progress_body_label_states() -> None:
    empty = progress_body([], 0)
    assert empty["state"] == "idle" and empty["total"] == 0 and empty["label"] == ""
    mid = progress_body([{"action": "triage_file", "status": "done"}] * 12
                        + [{"action": "triage_file", "status": "pending"}] * 38, 12)
    assert mid["state"] == "ingesting" and mid["done"] == 12 and mid["total"] == 50 and mid["in_flight"] == 38
    assert mid["label"].startswith("Corpus baseline: 12 of 50 documents ingested")
    done = progress_body([{"action": "triage_file", "status": "done"}] * 5
                         + [{"action": "summarize_file", "status": "pending"}], 5)   # summary rows excluded
    assert done["state"] == "idle" and done["total"] == 5 and "Baseline complete: 5 contracts" in done["label"]
    assert "1 contract in corpus" in progress_body([{"action": "triage_file", "status": "done"}], 1)["label"]


def test_paramstore_known_doc_ids_distinct() -> None:
    s = ParamStore()
    try:
        p = normalize({"raw_quote": "72 hours", "role": "r", "value": "72", "unit": "hours",
                       "is_qualitative": False}, clause_type="ct", anchor=Anchor(1, (0.0, 0.0, 1.0, 1.0)))
        s.add_parameters("doc1", [p]); s.add_parameters("doc2", [p]); s.add_parameters("doc1", [p])  # re-ingest
        assert s.known_doc_ids() == {"doc1", "doc2"}
    finally:
        s.close()
