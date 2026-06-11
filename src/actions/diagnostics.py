"""System diagnostics (ADR-019/040). Imperative Shell at the edge, pure formatting core.

`report_system` is JARVIS's answer to "what's my CPU?": a read-only snapshot (CPU / memory / disk /
battery / top processes) plus deterministic anomaly flags. `audio_report` is the matching SENSOR for
the volume/mute actuators (ADR-040 sensor–actuator parity: if JARVIS can set a state, it must be able
to read it — otherwise "why doesn't my volume work?" gets answered with CPU numbers). Every report
names its own scope in its verdict line (scope-honest: a clean report must never read as a verdict on
something it didn't measure). Injected readings/spawn keep both unit-testable without faking the host.
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


# The "all clear" verdict line (ADR-040 scope-honest). Pulled out as a constant so the conversational
# layer can DROP it (ADR-045) when the user asked a neutral data question rather than a health one —
# "which app uses the most memory" should not get an unsolicited "nothing looks wrong" editorial.
NOMINAL_VERDICT = "Nothing looks wrong in these metrics (CPU / memory / disk / battery)."


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
    # Scope-honest verdict (ADR-040): name what was measured, so a clean report can never masquerade
    # as "X is fine" for an X (audio, keyboard, …) this report does not cover.
    lines.append(("Anomalies: " + "; ".join(flags)) if flags else NOMINAL_VERDICT)
    return "\n".join(lines)


def drop_nominal_verdict(report: str) -> str:
    """Strip ONLY the 'all clear' verdict line (ADR-045) — a real `Anomalies:` line is always kept,
    because a surfaced problem is never unsolicited. Pure; idempotent; a no-op on any non-report
    string (e.g. a test stub's 'ran report_system')."""
    return "\n".join(ln for ln in report.splitlines() if ln.strip() != NOMINAL_VERDICT)


# ── audio (ADR-040): the read-back sensor for the volume/mute actuators ──
def parse_volume_settings(raw: str) -> dict | None:
    """Parse osascript's `get volume settings` line ("output volume:19, input volume:55, alert
    volume:100, output muted:false") -> {output, input, alert, muted}. Pure; None if unparseable.
    A 'missing value' field (no output device / some hardware) parses to None for that key."""
    out: dict = {}
    for part in (raw or "").strip().split(","):
        k, _, v = part.partition(":")
        k, v = k.strip().lower(), v.strip().lower()
        if not _:
            continue
        if k == "output muted":
            out["muted"] = (v == "true") if v in ("true", "false") else None
        elif k in ("output volume", "input volume", "alert volume"):
            out[k.split()[0]] = int(v) if v.isdigit() else None
    return out if "output" in out and "muted" in out else None


def audio_report(spawn) -> str:
    """The sound-state report behind `audio_status`: output/input/alert volume + mute, with
    deterministic interpretation flags. Read-only; `spawn` is the sanctioned safespawn seam."""
    try:
        result = spawn(["osascript", "-e", "get volume settings"],
                       capture_output=True, text=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 — report the failure, never crash the turn
        return f"Couldn't read the sound state ({exc})."
    raw = (getattr(result, "stdout", "") or "").strip()
    s = parse_volume_settings(raw)
    if s is None:
        return f"Couldn't read the sound state (unexpected reply: {raw[:80]!r})."
    lines = ["Sound state:"]
    if s.get("output") is not None:
        lines.append(f"- Output volume: {s['output']}/100")
    if s.get("muted") is not None:
        lines.append(f"- Muted: {'YES' if s['muted'] else 'no'}")
    if s.get("input") is not None:
        lines.append(f"- Input (mic) volume: {s['input']}/100")
    if s.get("alert") is not None:
        lines.append(f"- Alert volume: {s['alert']}/100")
    flags = []
    if s.get("muted"):
        flags.append("⚠ output is MUTED — no sound will play until unmuted")
    elif s.get("output") == 0:
        flags.append("⚠ output volume is 0 — effectively silent")
    elif s.get("output") is not None and s["output"] <= 15:
        flags.append(f"⚠ output volume is very low ({s['output']}/100)")
    # Scope-honest verdict (ADR-040): this reads macOS's software audio state only.
    lines.append(("Flags: " + "; ".join(flags)) if flags
                 else "Software audio state looks fine (volume audible, not muted). This does not "
                      "test speakers/keys — if a physical volume key does nothing, check Keyboard "
                      "settings ('Use F1, F2… as standard function keys') or the key itself.")
    return "\n".join(lines)
