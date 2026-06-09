"""AX verb dispatch (ADR-021) — validates a GUI-actuation directive and routes it through consent.

The brain side of "JARVIS clicks a control": the LLM emits `[[DO: ax_press: <id>]]` or
`[[DO: ax_set_value: <id> <value>]]`; this validates the verb against the closed catalog and the id
against the CURRENT epoch's element set, then opens a consent request whose on-approve **emits an
`actuate` event** to the app (it never executes here — the app holds the Accessibility grant and the
live element refs). Kept as a small injected function so it unit-tests with a real ConsentService and
fake emitter, no Session/socket/OS needed.
"""
from __future__ import annotations

from typing import Callable

from actions import AX_VERBS

# emit_actuate(epoch, element_id, verb, args) -> None   (the daemon sends this event to the app)
EmitActuate = Callable[[int, str, str, dict], None]


def find_control_id(dom: str, role_kw: str, title_kw: str) -> str | None:
    """Find the id of a control in a serialized AX DOM whose line matches role+title (case-insensitive).
    DOM lines look like:  [sld_1] AXSlider "Brightness" = 0.6 . Pure — used by navigation recipes."""
    rl, tl = role_kw.lower(), title_kw.lower()
    for line in dom.splitlines():
        low = line.lower()
        if line.startswith("[") and "]" in line and rl in low and tl in low:
            return line[1:line.index("]")]
    return None


def dispatch_ax(consent, emit_actuate: EmitActuate, ids: set[str], epoch: int,
                verb: str, arg: str) -> str:
    """Validate (verb, id[, value]) and open a consent gate; on approve, emit the actuate event.
    Returns a user-facing string. Never actuates directly; an invalid verb/id is a safe refusal."""
    if verb not in AX_VERBS:
        return f"I can't do that (unknown UI action: {verb})."
    parts = (arg or "").split()
    if not parts:
        return f"I can't do that — {verb} needs an element id."
    element_id, rest = parts[0], parts[1:]
    if element_id not in ids:
        return ("I can't act on that element — it isn't on the screen I last read "
                "(the view may have changed; ask me again).")

    args: dict = {}
    if verb in ("ax_set_value", "ax_set_checked"):
        if not rest:
            return f"I can't do that — {verb} needs a value (e.g. 45, or 1/0 for a toggle)."
        try:
            args["value"] = float(rest[0])
        except ValueError:
            return f"I can't do that — {rest[0]!r} isn't a number."
        label = f"set {element_id} to {rest[0]}"
    else:  # ax_press
        label = f"click {element_id}"

    def _approve() -> str:
        emit_actuate(epoch, element_id, verb, args)
        return f"actuating: {label} (the app will confirm)."

    consent.request(kind="action", prompt=f"Approve: {label}?", label=label,
                    on_approve=_approve, expiry_default="deny")
    return f"⏳ Awaiting your approval: {label}"
