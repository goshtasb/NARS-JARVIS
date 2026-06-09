"""Bounded agent-loop routing (ADR-024 Phase 2). Functional Core (S-02) — pure decision logic.

For arbitrary targets with no recipe, JARVIS navigates → re-perceives → acts in one utterance. This
module holds the two pure pieces: `resolve_surface` (map a navigate target to a safe System Settings
deep link — fail-safe to None) and `agent_route` (decide the next step from the model's parsed
directives). The stateful orchestration (open, hop counter, consent gate) lives in `service.session`.

Safety: navigation can only open a vetted surface here; actuation is always routed to the GATED
consent path by the session. Unknown target / no actionable directive → fail-safe (None / "giveup").
"""
from __future__ import annotations

from actions import resolve as resolve_action

# Navigate target keyword(s) -> System Settings deep link. Verified live via `axdump`; unknown targets
# resolve to None and are refused (no arbitrary launches). Settings panes only in v1.
_A11Y_DISPLAY = "x-apple.systempreferences:com.apple.preference.universalaccess?Seeing_Display"
_NAV_SURFACES: dict[str, str] = {
    # Accessibility → Display: real, arbitrary toggles (reduce transparency / differentiate / etc.).
    "transparency": _A11Y_DISPLAY,
    "reduce motion": _A11Y_DISPLAY,
    "differentiate": _A11Y_DISPLAY,
    "accessibility": _A11Y_DISPLAY,
    "focus": "x-apple.systempreferences:com.apple.Focus-Settings.extension",
    "do not disturb": "x-apple.systempreferences:com.apple.Focus-Settings.extension",
    "bluetooth": "x-apple.systempreferences:com.apple.BluetoothSettings",
    "wi-fi": "x-apple.systempreferences:com.apple.wifi-settings-extension",
    "wifi": "x-apple.systempreferences:com.apple.wifi-settings-extension",
    "sound": "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "volume": "x-apple.systempreferences:com.apple.Sound-Settings.extension",
    "display": "x-apple.systempreferences:com.apple.Displays-Settings.extension",
}


def resolve_surface(target: str) -> str | None:
    """Map a navigate target to a vetted deep link, or None (fail-safe; the session then refuses)."""
    t = (target or "").strip().lower()
    if not t:
        return None
    for keyword, link in _NAV_SURFACES.items():
        if keyword in t:
            return link
    return None


def agent_route(actions: list[tuple[str, str]]):
    """Decide the next loop step from the model's parsed [[DO:]] directives. Prefers actuation over
    further navigation. Returns ("act", name, arg) | ("navigate", target) | ("giveup",)."""
    for name, arg in actions:                       # an actionable ax_* verb wins (the goal)
        a = resolve_action(name)
        if a is not None and a.kind == "ax":
            return ("act", name, arg)
    for name, arg in actions:                       # else a further navigation hop
        if name == "navigate":
            return ("navigate", arg)
    return ("giveup",)                              # nothing actionable -> fail safe
