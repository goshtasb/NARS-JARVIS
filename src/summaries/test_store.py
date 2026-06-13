"""ADR-058 durable summary archive: add/list/get, newest-first ordering, the `has` idempotency guard
the backfill relies on, and SURVIVING a simulated daemon restart (the reason it's on-disk, not memory)."""
import tempfile

from summaries import SummaryArchive


def test_add_list_get_newest_first() -> None:
    a = SummaryArchive(":memory:")
    s1 = a.add("Q3-PRD.pdf", "/docs/Q3-PRD.pdf", "first summary", now=1.0)
    s2 = a.add("notes.txt", "/docs/notes.txt", "second summary body", now=2.0)
    rows = a.list()
    assert [r["id"] for r in rows] == [s2, s1]                  # newest first
    assert rows[0]["source_name"] == "notes.txt" and rows[0]["chars"] == len("second summary body")
    assert "text" not in rows[0]                                # list omits the body
    got = a.get(s1)
    assert got["text"] == "first summary" and got["source_path"] == "/docs/Q3-PRD.pdf"
    assert a.get(999) is None
    a.close()


def test_has_guards_backfill_idempotency() -> None:
    a = SummaryArchive(":memory:")
    a.add("doc.pdf", "/x/doc.pdf", "body", now=1.0)
    assert a.has("/x/doc.pdf", "body")                          # already archived
    assert not a.has("/x/doc.pdf", "different body")
    assert not a.has("/x/other.pdf", "body")
    a.close()


def test_archive_survives_a_restart() -> None:
    path = tempfile.mktemp(suffix=".db")
    a = SummaryArchive(path)
    sid = a.add("report.pdf", "/r/report.pdf", "the durable summary", now=1.0)
    a.close()                                                   # simulate the daemon recycling
    reopened = SummaryArchive(path)                             # fresh process, same db
    assert reopened.get(sid)["text"] == "the durable summary"
    assert len(reopened.list()) == 1
    reopened.close()


if __name__ == "__main__":
    test_add_list_get_newest_first()
    test_has_guards_backfill_idempotency()
    test_archive_survives_a_restart()
    print("summaries/test_store: OK")
