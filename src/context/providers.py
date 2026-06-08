"""Dynamic-context providers (ADR-010). Functional Core (S-02) — pure given their inputs.

The offline LLM lives in a "timeless void": it has no clock, no system awareness. Rather than rely
on brittle local tool-calling, we INJECT a few tokens of fresh live facts into every turn. These
providers render that block; the imperative shell (service.session) supplies the wall clock, the
psutil snapshot, and the (optional) sentinel foreground — so this layer reads no I/O and unit-tests
with plain values. The block is labelled so the model answers FROM it and never tries to memorise it
(the volatile guard in `context.volatile` is the deterministic backstop for that).
"""
from __future__ import annotations

from datetime import datetime

_HEADER = "Current context (live — answer from this; do NOT memorize it):"


def clock_fact(now: datetime) -> str:
    """Human + machine readable local date/time, e.g. 'Saturday 2026-06-07 20:35 (local)'. Pure."""
    return "date/time: " + now.strftime("%A %Y-%m-%d %H:%M") + " (local)"


def system_fact(snapshot: str | None) -> str | None:
    """Format the daemon's psutil snapshot (session._last, e.g. 'cpu=12% mem=63%'); None if absent."""
    snapshot = (snapshot or "").strip()
    return f"system: {snapshot}" if snapshot and snapshot != "no poll yet" else None


def foreground_fact(category: str | None, attention: str | None) -> str | None:
    """Coarse foreground app CATEGORY + attention from the Flow Sentinel — None when the sentinel is
    off / has no reading. Never an app name or window content (TCC-free design)."""
    if not category:
        return None
    tail = f" attention={attention}" if attention else ""
    return f"foreground={category}{tail}"


def render_live_context(now: datetime, snapshot: str | None = None,
                        foreground: tuple[str | None, str | None] | None = None) -> str:
    """Compose the live-context block from the available providers. Date/time is always present;
    system and foreground are included only when supplied. Empty string is never returned (clock
    always renders)."""
    lines = [clock_fact(now)]
    sys_line = system_fact(snapshot)
    if sys_line:
        lines.append(sys_line)
    if foreground is not None:
        fg = foreground_fact(foreground[0], foreground[1])
        if fg:
            lines.append(fg)
    return _HEADER + "\n" + "\n".join(f"- {ln}" for ln in lines)
