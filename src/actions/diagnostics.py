"""System diagnostics (ADR-019). Imperative Shell at the edge (reads psutil), pure formatting core.

`report_system` is JARVIS's answer to "what's my CPU?" and the seed of "is something broken?": a
read-only snapshot (CPU / memory / disk / battery / top processes) plus deterministic anomaly flags.
No subprocess — psutil only. `system_report(readings=...)` takes injected readings so the flag
thresholds are unit-testable without faking the host. v1 troubleshooting = the flags; LLM-reasoned
diagnosis over this report is a documented v2 follow-on.
"""
from __future__ import annotations

# Thresholds at/above which a metric is flagged as anomalous. Conservative — these are "something is
# clearly wrong" levels, not routine load.
CPU_HIGH = 90.0
MEM_HIGH = 90.0
DISK_HIGH = 90.0
BATTERY_LOW = 20.0


def _read() -> dict:
    """Read the live host metrics via psutil. The only impure function here."""
    import psutil

    vm = psutil.virtual_memory()
    du = psutil.disk_usage("/")
    batt = psutil.sensors_battery()
    procs: list[tuple[str, float]] = []
    for p in psutil.process_iter(["name", "memory_percent"]):
        try:
            procs.append((p.info["name"] or "?", float(p.info["memory_percent"] or 0.0)))
        except Exception:  # noqa: BLE001 — a vanished/again-denied process must not abort the scan
            continue
    procs.sort(key=lambda x: x[1], reverse=True)
    return {
        "cpu": psutil.cpu_percent(interval=0.3),
        "mem": vm.percent,
        "disk": du.percent,
        "battery": (batt.percent, batt.power_plugged) if batt is not None else None,
        "top": procs[:3],
    }


def anomaly_flags(r: dict) -> list[str]:
    """Deterministic 'what looks wrong' flags for a readings dict. Pure — the v1 troubleshooter."""
    flags: list[str] = []
    if r.get("cpu", 0) >= CPU_HIGH:
        flags.append("⚠ CPU pegged")
    if r.get("mem", 0) >= MEM_HIGH:
        flags.append("⚠ memory pressure high")
    if r.get("disk", 0) >= DISK_HIGH:
        flags.append("⚠ disk almost full")
    batt = r.get("battery")
    if batt is not None and not batt[1] and batt[0] <= BATTERY_LOW:
        flags.append("⚠ battery low")
    return flags


def system_report(readings: dict | None = None) -> str:
    """A human-readable system report with anomaly flags. `readings` injectable for tests; defaults
    to a live psutil read. Pure given `readings`."""
    r = readings if readings is not None else _read()
    lines = [
        "System report:",
        f"- CPU: {r['cpu']:.0f}%",
        f"- Memory: {r['mem']:.0f}% used",
        f"- Disk (/): {r['disk']:.0f}% used",
    ]
    batt = r.get("battery")
    if batt is not None:
        pct, plugged = batt
        lines.append(f"- Battery: {pct:.0f}% ({'plugged in' if plugged else 'on battery'})")
    top = r.get("top") or []
    if top:
        lines.append("- Top memory: " + ", ".join(f"{name} {mem:.0f}%" for name, mem in top))
    flags = anomaly_flags(r)
    lines.append(("Anomalies: " + "; ".join(flags)) if flags
                 else "Nothing looks wrong — all metrics nominal.")
    return "\n".join(lines)
