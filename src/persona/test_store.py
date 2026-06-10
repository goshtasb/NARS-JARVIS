"""ADR-036 persona store: the O(1) ingestion buffer + the (term,freq,conf) checkpoint that doubles as
the injection source and replay source."""
import tempfile

from persona import PersonaStore


def test_buffer_pending_consume() -> None:
    s = PersonaStore(":memory:")
    s.buffer_event("user wants terse output")
    s.buffer_event("  ")                                   # blank -> ignored
    batch = s.pending_batch(5)
    assert len(batch) == 1 and batch[0]["raw_text"] == "user wants terse output"
    s.consume([batch[0]["id"]])
    assert s.pending_count() == 0
    s.close()


def test_upsert_current_floor_and_all() -> None:
    s = PersonaStore(":memory:")
    s.upsert_concept("<format_directive --> omit_greeting_prose>", 1.0, 0.9)
    s.upsert_concept("<format_directive --> cite_sources_explicitly>", 1.0, 0.5)   # below floor
    s.upsert_concept("<format_directive --> omit_greeting_prose>", 1.0, 0.95)      # upsert (not dup)
    assert [r["term"] for r in s.current(0.75)] == ["<format_directive --> omit_greeting_prose>"]
    assert len(s.all_concepts()) == 2                       # both stored; floor only affects current()
    s.close()


def test_prune_drops_washed_out_and_survives_reopen() -> None:
    path = tempfile.mktemp(suffix=".db")
    s = PersonaStore(path)
    s.upsert_concept("<current_focus --> local_development>", 1.0, 0.9)
    s.upsert_concept("<format_directive --> terse_markdown_tables>", 0.0, 0.05)    # washed out
    assert s.prune(0.10) == 1
    s.close()
    reopened = PersonaStore(path)                           # checkpoint survives restart (replay source)
    assert [r["term"] for r in reopened.all_concepts()] == ["<current_focus --> local_development>"]
    reopened.close()


def test_delete_removes_one_concept() -> None:
    s = PersonaStore(":memory:")
    s.upsert_concept("<format_directive --> omit_greeting_prose>", 1.0, 0.9)
    s.upsert_concept("<current_focus --> local_development>", 1.0, 0.9)
    assert s.delete("<format_directive --> omit_greeting_prose>") == 1
    assert s.delete("<format_directive --> not_present>") == 0           # absent -> no-op
    assert [r["term"] for r in s.all_concepts()] == ["<current_focus --> local_development>"]
    s.close()


if __name__ == "__main__":
    test_buffer_pending_consume()
    test_upsert_current_floor_and_all()
    test_prune_drops_washed_out_and_survives_reopen()
    test_delete_removes_one_concept()
    print("persona/test_store: OK")
