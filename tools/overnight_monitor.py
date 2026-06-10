#!/usr/bin/env python3
"""Overnight resource monitor — the field-test instrument for the ADR-031/032 unattended run.

Samples the JARVIS daemon's memory/CPU + system memory + (best-effort) thermal pressure on an interval
and appends a CSV, so by morning you can answer: did llama-cpp-python leak, did the machine choke, did
the daemon survive the night? It is a STANDALONE observer — a separate process that only *reads* `ps`/
psutil + `pmset`; it never touches the daemon, the socket, or any JARVIS state.

The most important signal is the simplest: the daemon's RSS over time. A healthy run holds roughly flat
(the model is loaded once; each summarize call allocates and frees). A steady upward climb = a leak.

Usage:
    python3 tools/overnight_monitor.py                 # 60s interval, until Ctrl-C, CSV in $TMPDIR
    python3 tools/overnight_monitor.py --interval 30 --duration 8h --out ~/jarvis_night.csv
Run it in the background AFTER you Commit+Start the batch:
    nohup python3 tools/overnight_monitor.py --duration 8h >/tmp/jarvis_monitor.out 2>&1 &

Honest limits: thermal data via `pmset -g therm` is coarse and present only when the OS is actually
throttling (full power/thermal telemetry needs `sudo powermetrics`, which this script deliberately does
NOT run). It cannot prove the *summaries* stayed coherent — only the Morning Briefing's .summary.md
files show whether the 7B degraded into gibberish by the last document.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime

DAEMON_SIGNATURE = "Python -m service"   # how restart.sh / run-ui.sh launch the daemon
_COLUMNS = ("ts", "daemon_pid", "daemon_rss_mb", "daemon_cpu_pct",
            "sys_mem_used_pct", "sys_mem_avail_mb", "load_1m", "thermal")


def _daemon_pid() -> int | None:
    """The running daemon's pid (by command signature), or None if it isn't up."""
    try:
        out = subprocess.run(["pgrep", "-f", DAEMON_SIGNATURE], capture_output=True, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return None
    pids = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    return pids[0] if pids else None


def _thermal() -> str:
    """Coarse thermal pressure from `pmset -g therm` (no sudo). 'nominal' when not throttling."""
    try:
        out = subprocess.run(["pmset", "-g", "therm"], capture_output=True, text=True, timeout=5).stdout
    except Exception:  # noqa: BLE001
        return "n/a"
    for line in out.splitlines():
        if "CPU_Speed_Limit" in line:
            val = line.split("=")[-1].strip()
            return "nominal" if val == "100" else f"throttled@{val}%"
    return "nominal"


def _sample(psutil) -> dict:
    pid = _daemon_pid()
    vm = psutil.virtual_memory()
    row = {c: "" for c in _COLUMNS}
    row["ts"] = datetime.now().isoformat(timespec="seconds")
    row["sys_mem_used_pct"] = f"{vm.percent:.1f}"
    row["sys_mem_avail_mb"] = f"{vm.available / 1e6:.0f}"
    row["load_1m"] = f"{os.getloadavg()[0]:.2f}"
    row["thermal"] = _thermal()
    if pid is None:
        row["daemon_pid"] = "GONE"        # <-- the daemon crashed / was killed (the 4 AM failure mode)
        return row
    try:
        proc = psutil.Process(pid)
        row["daemon_pid"] = str(pid)
        row["daemon_rss_mb"] = f"{proc.memory_info().rss / 1e6:.0f}"
        row["daemon_cpu_pct"] = f"{proc.cpu_percent(interval=0.5):.0f}"
    except Exception:  # noqa: BLE001 — raced the process exiting
        row["daemon_pid"] = "GONE"
    return row


def _parse_duration(s: str | None) -> float | None:
    if not s:
        return None
    s = s.strip().lower()
    mult = {"s": 1, "m": 60, "h": 3600}.get(s[-1])
    return float(s[:-1]) * mult if mult else float(s)


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitor the JARVIS daemon's resources overnight.")
    ap.add_argument("--interval", type=float, default=60.0, help="seconds between samples (default 60)")
    ap.add_argument("--duration", default=None, help="how long to run, e.g. 8h / 30m (default: until Ctrl-C)")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "jarvis_overnight_monitor.csv"))
    args = ap.parse_args()

    try:
        import psutil
    except ImportError:
        print("psutil is required (it's already a JARVIS dep): pip install psutil", file=sys.stderr)
        sys.exit(1)

    deadline = (time.time() + d) if (d := _parse_duration(args.duration)) else None
    new_file = not os.path.exists(args.out)
    fh = open(args.out, "a", encoding="utf-8")
    if new_file:
        fh.write(",".join(_COLUMNS) + "\n"); fh.flush()

    print(f"[monitor] logging to {args.out} every {args.interval:.0f}s"
          + (f" for {args.duration}" if args.duration else " until Ctrl-C"))
    first_rss = peak_rss = last_rss = None
    gone_logged = False
    try:
        while True:
            row = _sample(psutil)
            fh.write(",".join(row[c] for c in _COLUMNS) + "\n"); fh.flush()
            rss = row["daemon_rss_mb"]
            if rss:
                v = float(rss)
                first_rss = v if first_rss is None else first_rss
                peak_rss = v if peak_rss is None else max(peak_rss, v)
                last_rss = v
                print(f"[{row['ts']}] daemon rss={rss}MB cpu={row['daemon_cpu_pct'] or '?'}% "
                      f"sysmem={row['sys_mem_used_pct']}% thermal={row['thermal']}")
            elif row["daemon_pid"] == "GONE" and not gone_logged:
                print(f"[{row['ts']}] ⚠ DAEMON NOT RUNNING — it crashed or was stopped.")
                gone_logged = True
            if deadline and time.time() >= deadline:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        fh.close()
        if first_rss is not None and last_rss is not None:
            delta = last_rss - first_rss
            grew = delta / first_rss * 100 if first_rss else 0
            verdict = ("LIKELY LEAK — RSS climbed steadily" if grew > 25
                       else "looks stable" if abs(grew) <= 25 else "RSS fell")
            print(f"\n[monitor] summary: first={first_rss:.0f}MB peak={peak_rss:.0f}MB last={last_rss:.0f}MB "
                  f"(Δ{delta:+.0f}MB, {grew:+.0f}%) → {verdict}")
        print(f"[monitor] full CSV: {args.out}")


if __name__ == "__main__":
    main()
