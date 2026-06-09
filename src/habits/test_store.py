"""Unit tests for HabitStore: introspection (ADR-027) + Phase-2 context columns/migration (ADR-028)."""
import sqlite3
import tempfile

from habits import HabitStore


def test_list_all_and_delete() -> None:
    s = HabitStore(":memory:")
    s.record("h09_mute", "h09", "mute", "", 1.0, 0.9)
    s.record("h14_dark_mode", "h14", "dark_mode", "", 1.0, 0.5)
    rows = s.list_all()
    assert {r["key"] for r in rows} == {"h09_mute", "h14_dark_mode"}
    assert all({"key", "bucket", "action", "arg", "frequency", "confidence"} <= set(r) for r in rows)
    s.delete("h09_mute")
    assert {r["key"] for r in s.list_all()} == {"h14_dark_mode"}
    s.close()


def test_for_context_filters_by_bucket_daytype_app_scope() -> None:
    s = HabitStore(":memory:")
    s.record("h16_mute_weekday_app_zoom", "h16", "mute", "", 1.0, 0.9,
             day_type="weekday", app="app_zoom", scope="context")
    s.record("h16_mute", "h16", "mute", "", 1.0, 0.9, scope="base")              # base, not context
    s.record("h16_mute_weekend_app_zoom", "h16", "mute", "", 1.0, 0.9,
             day_type="weekend", app="app_zoom", scope="context")                # wrong day_type
    hits = s.for_context("h16", "weekday", "app_zoom")
    assert [r["key"] for r in hits] == ["h16_mute_weekday_app_zoom"]             # only the exact match
    s.close()


def test_migration_adds_phase2_columns_to_an_old_table() -> None:
    fd = tempfile.mktemp(suffix=".db")
    # simulate a pre-ADR-028 habits table (no day_type/app/scope)
    db = sqlite3.connect(fd)
    db.execute("CREATE TABLE habits (key TEXT PRIMARY KEY, bucket TEXT, action TEXT, arg TEXT, "
               "frequency REAL, confidence REAL, last_proposed TEXT DEFAULT '', updated_at REAL)")
    db.execute("INSERT INTO habits VALUES('h09_mute','h09','mute','',1.0,0.5,'',0)")
    db.commit(); db.close()
    s = HabitStore(fd)                       # opening migrates in place
    rows = s.list_all()
    assert rows and rows[0]["scope"] == "base" and rows[0]["app"] == ""   # columns added, defaulted
    s.close()


if __name__ == "__main__":
    test_list_all_and_delete()
    test_for_context_filters_by_bucket_daytype_app_scope()
    test_migration_adds_phase2_columns_to_an_old_table()
    print("habits/test_store: OK")
