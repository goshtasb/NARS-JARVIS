"""Unit tests for SentinelStore belief persistence (ADR-011). In-memory SQLite; no ONA."""
from sentinel.store import SentinelStore


def test_record_and_read_beliefs() -> None:
    s = SentinelStore()
    s.record_belief("<distracted_hide_comms --> [approved]>", 1.0, 0.857, now=1.0)
    s.record_belief("<steady --> [baseline]>", 0.9, 0.85, now=1.0)
    got = dict((t, (f, c)) for t, f, c in s.beliefs())
    assert got["<distracted_hide_comms --> [approved]>"] == (1.0, 0.857)
    assert got["<steady --> [baseline]>"] == (0.9, 0.85)


def test_record_belief_upserts_latest_truth() -> None:
    s = SentinelStore()
    s.record_belief("<distracted_hide_comms --> [approved]>", 1.0, 0.50, now=1.0)
    s.record_belief("<distracted_hide_comms --> [approved]>", 1.0, 0.857, now=2.0)  # later, stronger
    rows = s.beliefs()
    assert len(rows) == 1                                   # one row, not duplicated
    assert rows[0] == ("<distracted_hide_comms --> [approved]>", 1.0, 0.857)


def test_beliefs_coexist_with_kpi_tables() -> None:
    s = SentinelStore()
    s.record_focus_block(1.0, 60.0)
    s.record_intervention(2.0, True)
    s.record_belief("<distracted_hide_media --> [approved]>", 1.0, 0.857, now=3.0)
    assert len(s.beliefs()) == 1                            # belief table independent of KPI tables
    assert s.kpi() is not None


if __name__ == "__main__":
    test_record_and_read_beliefs()
    test_record_belief_upserts_latest_truth()
    test_beliefs_coexist_with_kpi_tables()
    print("sentinel/test_store: OK")


def test_enabled_defaults_on_and_persists(tmp_path) -> None:
    # ADR-048: auto-start preference. Defaults ON when never set; a choice survives a reopen (restart).
    db = str(tmp_path / "s.db")
    assert SentinelStore(db).enabled() is True                  # never set -> default ON
    s = SentinelStore(db); s.set_enabled(False)
    assert SentinelStore(db).enabled() is False                 # deliberate OFF persists across restart
    SentinelStore(db).set_enabled(True)
    assert SentinelStore(db).enabled() is True                  # back ON persists


def test_usage_log_records_and_reads_recent() -> None:
    # ADR-050 slice: content-blind app-switch log round-trips, time-filtered.
    s = SentinelStore()
    s.record_usage("com.todesktop.x", "dev", now=1000.0)
    s.record_usage("com.tinyspeck.slackmacgap", "comms", now=1100.0)
    s.record_usage("com.apple.safari", "web", now=50.0)        # older than the window
    recent = s.recent_usage(since=900.0)
    assert [e["bundle"] for e in recent] == ["com.todesktop.x", "com.tinyspeck.slackmacgap"]  # oldest first, filtered
    assert recent[0]["bucket"] == "dev" and recent[0]["created_at"] == 1000.0
    assert s.recent_usage(since=2000.0) == []                  # nothing newer
