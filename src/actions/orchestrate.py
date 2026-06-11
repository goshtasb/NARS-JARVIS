"""Context Orchestration Layer (ADR-049) — VERIFIED actuation. Imperative Shell at the edge (osascript
via the injected spawn seam), pure parsing reused from diagnostics.

Bootstrap (sync barrel): `set_volume` — the safest possible first muscle: zero-TCC (StandardAdditions
`set volume`, no app target → no Automation prompt), reversible, ungated (a single reversible primitive
is below the consent threshold, ADR-020), and — the reason it's the bootstrap — its state is
read-back-verifiable with code that already exists (`diagnostics.parse_volume_settings`). So it proves
the orchestration loop `actuate → verify → report` end-to-end with parts already in the tree.

Verification is tri-state by design (ADR-049): a synchronous-readable state like volume verifies inline
here (VERIFIED / FAILED); event-driven async states (app launch / Focus) verify off the NSWorkspace
stream and live in the next increment — they are NOT faked with a hardcoded timeout here.
"""
from __future__ import annotations

from typing import Callable

from .diagnostics import parse_volume_settings

# macOS quantizes output volume to ~16 hardware steps (~6.25 apart), so a read-back rarely equals the
# request exactly. Verification confirms the state landed within one step, and reports the ACTUAL value.
_STEP_TOLERANCE = 7


def set_volume(spawn: Callable, arg: str) -> str:
    """Set output volume to 0–100 and VERIFY by reading it back. Zero-TCC, reversible. Never raises."""
    raw = str(arg).strip()
    try:
        level = int(float(raw))
    except (TypeError, ValueError):
        return f"I need a number 0–100 to set the volume (got {raw!r})."
    level = max(0, min(100, level))                      # clamp; the read-back reports what actually landed
    # ── actuate (zero-TCC StandardAdditions; no `tell application`, so no Automation prompt) ──
    try:
        spawn(["osascript", "-e", f"set volume output volume {level}"],
              capture_output=True, text=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 — report, never crash the turn
        return f"Couldn't set the volume ({exc})."
    # ── verify by read-back (the loop's whole point: never fire-and-hope) ──
    try:
        r = spawn(["osascript", "-e", "get volume settings"], capture_output=True, text=True, timeout=10)
    except Exception as exc:  # noqa: BLE001
        return f"Set volume to {level}, but couldn't verify it ({exc})."
    s = parse_volume_settings(getattr(r, "stdout", "") or "")
    actual = s.get("output") if s else None
    if actual is None:
        return f"Set volume to {level}, but couldn't read it back to verify."
    if abs(actual - level) <= _STEP_TOLERANCE:
        return f"Volume set to {actual}/100 (verified)."
    return f"Tried to set volume to {level}, but it reads {actual}/100 — not verified."


_ACTIONS: dict[str, Callable[[Callable, str], str]] = {
    "set_volume": set_volume,
}


def dispatch(name: str, arg: str, spawn: Callable) -> str:
    """Run a verified orchestration action by name. Unknown name → safe refusal (closed set)."""
    fn = _ACTIONS.get(name)
    return fn(spawn, arg) if fn else f"I don't know that orchestration action ({name})."
