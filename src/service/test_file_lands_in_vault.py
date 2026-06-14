"""v1.24.0 integration fix: a document attached/briefed in CHAT must LAND in the permanent vault, not be
discarded after the summary bubble. On file-job completion the daemon must (1) emit the chat result, (2)
archive the summary SYNCHRONOUSLY (so Activity › Summary shows it was received), and (3) trigger the heavy
guarded distillation ASYNCHRONOUSLY (the off-loop LearnJob), never blocking the chat loop."""
from service.session import Session


class _FakeFileJob:
    """Stand-in for the off-loop SummaryJob — returns a canned summary then EOF, no model spawned."""
    def __init__(self, summary):
        self._events = [("result", summary), ("eof", None)]
    def fileno(self):
        return -7
    def read(self):
        evs, self._events = self._events, [("eof", None)]
        return evs
    def cleanup(self):
        pass


def test_chat_document_lands_in_vault_and_archive(tmp_path) -> None:
    events: list = []
    s = Session(db_path=str(tmp_path / "j.db"), on_event=lambda k, b: events.append((k, b)))
    try:
        # decouple the test from the real (heavy) distillation worker: record the async hand-off instead.
        distilled: list = []
        s._spawn_learn = lambda text, source="": distilled.append(source)   # type: ignore[method-assign]

        fake = _FakeFileJob("Vendor shall notify within 72 hours of a breach.")
        path = str(tmp_path / "vendor_nda.pdf")
        # a live Activity row (running) like _file_summarize creates before the worker finishes
        tid = s._overnight_queue.enqueue("summarize_file", path)
        s._overnight_queue.mark(tid, "running", result="reading…")
        s._file_jobs[fake.fileno()] = {"job": fake, "token": 1, "path": path, "tid": tid,
                                       "name": "vendor_nda.pdf", "text": None, "error": None}
        s._read_file_job(fake.fileno())

        # 1) the chat bubble still goes out immediately
        results = [b for k, b in events if k == "file_result"]
        assert results and results[0]["ok"] and results[0]["name"] == "vendor_nda.pdf"
        # 2) the live Activity row transitioned running -> done (visible in Activity › Now then Log)
        row = [r for r in s._overnight_queue.list_all() if r["id"] == tid][0]
        assert row["status"] == "done", row
        # 3) archived synchronously -> visible in Activity › Summary (summary_list reads this)
        listed = s._summaries.list()
        assert any(r.get("source_name") == "vendor_nda.pdf" for r in listed), listed
        # 4) the heavy guarded distillation was handed to the OFF-LOOP worker against the raw file path
        assert distilled == [path], distilled
        assert fake.fileno() not in s._file_jobs               # reaped
    finally:
        s.close()


def test_failed_file_read_does_not_touch_the_vault(tmp_path) -> None:
    events: list = []
    s = Session(db_path=str(tmp_path / "j.db"), on_event=lambda k, b: events.append((k, b)))
    try:
        distilled: list = []
        s._spawn_learn = lambda text, source="": distilled.append(source)   # type: ignore[method-assign]

        class _Err(_FakeFileJob):
            def __init__(self):
                self._events = [("error", "scanned image-only PDF"), ("eof", None)]
        fake = _Err()
        s._file_jobs[fake.fileno()] = {"job": fake, "token": 2, "path": str(tmp_path / "scan.pdf"),
                                       "name": "scan.pdf", "text": None, "error": None}
        s._read_file_job(fake.fileno())

        assert [b for k, b in events if k == "file_result"][0]["ok"] is False
        assert distilled == []                                  # nothing learned from a failed read
        assert s._summaries.list() == [] or all(r.get("source_name") != "scan.pdf" for r in s._summaries.list())
    finally:
        s.close()


if __name__ == "__main__":
    import tempfile
    test_chat_document_lands_in_vault_and_archive(tempfile.mkdtemp().__class__("/tmp"))
    print("test_file_lands_in_vault: OK")
