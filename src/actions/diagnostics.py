"""System diagnostics (ADR-019/040). Imperative Shell at the edge, pure formatting core.

`report_system` is JARVIS's answer to "what's my CPU?": a read-only snapshot (CPU / memory / disk /
battery / top processes) plus deterministic anomaly flags. `audio_report` is the matching SENSOR for
the volume/mute actuators (ADR-040 sensor–actuator parity: if JARVIS can set a state, it must be able
to read it — otherwise "why doesn't my volume work?" gets answered with CPU numbers). Every report
names its own scope in its verdict line (scope-honest: a clean report must never read as a verdict on
something it didn't measure). Injected readings/spawn keep both unit-testable without faking the host.
"""
from __future__ import annotations

import re

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


# ── network (ADR-046): the read-only "what's using my connection" sensor. Local inspection only —
# per-process bandwidth (nettop), established connections (lsof), Wi-Fi link quality (system_profiler).
# No egress: it never pings or fetches; it reports what THIS machine is doing, which is exactly the
# gap that made JARVIS fall back to generic web advice for "what's slowing my internet". ──
def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _clean_proc(name: str) -> str:
    """nettop process tokens look like 'Google Chrome H.98583'; drop the trailing .PID for display."""
    base = name.rsplit(".", 1)
    return base[0] if len(base) == 2 and base[1].isdigit() else name


def parse_nettop_delta(raw: str) -> list[tuple[str, int]]:
    """`nettop -P -d -L 2 -s N -J bytes_in,bytes_out` emits each process TWICE — cumulative then the
    interval delta. Keeping the LAST value per process yields the delta (bytes moved in the sample).
    Pure -> [(process, bytes), …] sorted descending, zero-traffic processes dropped."""
    last: dict[str, int] = {}
    for line in (raw or "").splitlines():
        parts = line.split(",")
        if len(parts) >= 3 and parts[1].strip().isdigit() and parts[2].strip().isdigit():
            last[_clean_proc(parts[0].strip())] = int(parts[1]) + int(parts[2])
    return sorted(((p, b) for p, b in last.items() if b > 0), key=lambda x: -x[1])


def parse_connections(raw: str) -> list[tuple[str, int]]:
    """Count ESTABLISHED TCP connections per process from `lsof +c 0 -nP -iTCP -sTCP:ESTABLISHED`,
    skipping loopback. Pure -> [(process, count), …] sorted descending. Locates the peer by the '->'
    token (not a fixed column) so it's robust to lsof's IPv4/IPv6 column-count differences."""
    counts: dict[str, int] = {}
    for line in (raw or "").splitlines()[1:]:
        parts = line.split()
        peer = next((p for p in parts if "->" in p), "")
        if not peer or "127.0.0.1" in peer or "[::1]" in peer:
            continue
        name = parts[0].replace("\\x20", " ")
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])


def parse_wifi(raw: str) -> dict:
    """Pull PHY mode / channel / signal / tx-rate from `system_profiler SPAirPortDataType` (the
    'Current Network Information' block — the first values after it). Pure; {} when off/ethernet."""
    out: dict = {}
    seen_current = False
    for line in (raw or "").splitlines():
        s = line.strip()
        if "Current Network Information" in s:
            seen_current = True
        if not seen_current:
            continue
        for key, field in (("PHY Mode:", "phy"), ("Channel:", "channel"),
                           ("Signal / Noise:", "signal"), ("Transmit Rate:", "rate")):
            if s.startswith(key) and field not in out:
                out[field] = s[len(key):].strip()
        if {"phy", "channel", "signal", "rate"} <= out.keys():
            break
    return out


def net_report(spawn) -> str:
    """The network report behind `network_status` (ADR-046): top bandwidth talkers, busiest
    connections, and Wi-Fi link quality — read-only, local, no egress. `spawn` is the safespawn seam."""
    def run(argv, timeout):
        try:
            r = spawn(argv, capture_output=True, text=True, timeout=timeout)
            return getattr(r, "stdout", "") or ""
        except Exception:  # noqa: BLE001 — a missing tool / timeout degrades that section, never crashes
            return ""

    talkers = parse_nettop_delta(run(["nettop", "-P", "-d", "-L", "2", "-s", "2",
                                      "-J", "bytes_in,bytes_out"], 10))
    conns = parse_connections(run(["lsof", "+c", "0", "-nP", "-iTCP", "-sTCP:ESTABLISHED"], 10))
    wifi = parse_wifi(run(["system_profiler", "SPAirPortDataType"], 10))

    lines = ["Network activity (this Mac, last ~2s):"]
    if talkers:
        lines.append("- Top bandwidth right now: "
                     + ", ".join(f"{p} {_human_bytes(b)}/2s" for p, b in talkers[:4]))
    else:
        lines.append("- Top bandwidth right now: (nothing measurable in the sample)")
    if conns:
        lines.append("- Most open connections: "
                     + ", ".join(f"{p} ({n})" for p, n in conns[:4]))
    if wifi:
        wifi_bits = [v for k in ("phy", "channel", "signal", "rate") if (v := wifi.get(k))]
        lines.append("- Wi-Fi: " + " | ".join(wifi_bits))
    # Scope-honest verdict (ADR-040/045): this is LOCAL only — it can't see the router, ISP, or other
    # devices. JARVIS itself (the `python -m service` daemon) holding any bandwidth would show above.
    top = talkers[0] if talkers else None
    if top and not any(j in top[0].lower() for j in ("python", "jarvis", "nar")):
        lines.append(f"Biggest local consumer is {top[0]} — not JARVIS. This shows only this Mac; it "
                     "can't see your router, ISP, or other devices on the network.")
    else:
        lines.append("This shows only this Mac's own network use — it can't see your router, ISP, or "
                     "other devices. For whole-network slowness, check those separately.")
    return "\n".join(lines)


# ── installed-apps disk usage (ADR-047): the sensor for "what's the largest application?" — there was
# no app/disk-by-app organ, so the 7B fell back to find_file (a filename search) and returned nothing. ──
_APPS_ROOT = "/Applications"


def parse_du_sizes(raw: str, root: str) -> list[tuple[str, int]]:
    """Parse `du -k -d 1 <root>` (each line: '<KB>\\t<path>') -> [(basename, KB), …] sorted desc.
    Pure. The <root> total line is dropped; tolerant of tab- OR space-separated du output (app names
    contain spaces, so the path is everything after the leading number)."""
    out: list[tuple[str, int]] = []
    for line in (raw or "").splitlines():
        m = re.match(r"\s*(\d+)\s+(.+?)\s*$", line)
        if not m:
            continue
        kb, path = int(m.group(1)), m.group(2)
        if path.rstrip("/") == root.rstrip("/"):
            continue
        out.append((path.rsplit("/", 1)[-1], kb))
    return sorted(out, key=lambda x: -x[1])


def largest_apps_report(spawn) -> str:
    """The report behind `largest_apps` (ADR-047): the biggest app bundles in /Applications by on-disk
    size. Read-only `du` through the safespawn seam; never raises."""
    try:
        r = spawn(["du", "-k", "-d", "1", _APPS_ROOT], capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001 — a slow/again-denied scan reports, never crashes the turn
        return f"Couldn't measure the applications folder ({exc})."
    apps = parse_du_sizes(getattr(r, "stdout", "") or "", _APPS_ROOT)
    if not apps:
        return "Couldn't read application sizes (no readable apps in /Applications)."
    lines = ["Largest applications (in /Applications, by on-disk size):"]
    for name, kb in apps[:6]:
        label = name[:-4] if name.endswith(".app") else name
        lines.append(f"- {label}: {_human_bytes(kb * 1024)}")
    # Scope-honest verdict (ADR-040): these are /Applications bundle sizes only.
    lines.append(f"The largest is {apps[0][0][:-4] if apps[0][0].endswith('.app') else apps[0][0]}. "
                 "Sizes cover /Applications only — not apps in your user folder, caches, or system files.")
    return "\n".join(lines)
