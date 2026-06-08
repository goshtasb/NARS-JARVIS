"""Unit tests for system diagnostics (ADR-019): the live report renders, and the anomaly flags fire
on injected readings (deterministic — no host faking)."""
from actions import diagnostics
from actions.diagnostics import anomaly_flags, system_report


def test_live_report_has_core_metrics() -> None:
    # Real psutil read (read-only, safe in CI). Just assert the shape, not the values.
    out = system_report()
    assert "System report:" in out
    assert "CPU:" in out and "Memory:" in out and "Disk (/):" in out


def test_injected_nominal_readings_flag_nothing() -> None:
    out = system_report({"cpu": 12.0, "mem": 40.0, "disk": 55.0,
                         "battery": (80.0, False), "top": [("Finder", 3.0)]})
    assert "Nothing looks wrong" in out
    assert "Finder 3%" in out


def test_injected_high_readings_raise_the_right_flags() -> None:
    r = {"cpu": 99.0, "mem": 95.0, "disk": 97.0, "battery": (8.0, False), "top": []}
    flags = anomaly_flags(r)
    assert "⚠ CPU pegged" in flags
    assert "⚠ memory pressure high" in flags
    assert "⚠ disk almost full" in flags
    assert "⚠ battery low" in flags
    assert "Anomalies:" in system_report(r)


def test_plugged_in_low_battery_is_not_flagged() -> None:
    # Low charge while charging is normal — only on-battery + low triggers the flag.
    assert anomaly_flags({"cpu": 5, "mem": 5, "disk": 5, "battery": (8.0, True)}) == []


def test_thresholds_are_inclusive_boundaries() -> None:
    assert anomaly_flags({"cpu": diagnostics.CPU_HIGH, "mem": 0, "disk": 0}) == ["⚠ CPU pegged"]
    assert anomaly_flags({"cpu": 89.9, "mem": 0, "disk": 0}) == []
