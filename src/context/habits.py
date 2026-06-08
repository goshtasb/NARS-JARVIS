"""Learned-habit translation (ADR-012). Functional Core (S-02) — pure.

Bridges the procedural Sentinel brain to the declarative Knowledge brain: the sentinel stores learned
autonomy authorizations as Narsese (`<distracted_hide_<cat> --> [approved]> {f c}`, persisted in
`sentinel_beliefs`, ADR-011). Dumping that syntax into the 7B invites it to ignore or mis-read it, so
we translate the CLOSED belief vocabulary into plain-English directives with a deterministic template
(no LLM call) — the same "dictionary lookup beats an inference round-trip" reflex used elsewhere.

Only CONFIDENT beliefs are surfaced, filtered by the Narsese expectation (= conf·(freq−0.5)+0.5):
confidently favorable -> a positive directive; confidently negative (an explicit user denial) -> a
negative safety directive; the uncertain middle -> omitted (never let the LLM see unconverged data).
`context` must not depend on `service`, so the expectation math is re-copied here (it is likewise
duplicated in service.autonomy and sentinel.surprise).
"""
from __future__ import annotations

import re

EXP_FLOOR = 0.85          # matches service.autonomy.EXP_FLOOR
_MAX_CATEGORY_LEN = 40    # a humanized category longer than this is almost certainly garbage -> omit

# Non-greedy capture of the WHOLE category token (snake_case/dashes kept intact, not truncated).
_HABIT_RE = re.compile(r"^<distracted_hide_(.+?) --> \[approved\]>$")

# Curated phrasing for the known sentinel buckets (sentinel.sensor.BUCKETS).
_FRIENDLY: dict[str, str] = {
    "comms": "chat/messaging",
    "media": "media",
    "web": "web",
    "dev": "developer",
    "productivity": "productivity",
    "utility": "utility",
}


def _expectation(freq: float, conf: float) -> float:
    return conf * (freq - 0.5) + 0.5


def _humanize(category: str) -> str:
    """Sanitize an unmapped/custom category for clean grammar in the prompt: lowercase, _/- -> space,
    drop non-alphanumeric noise, collapse whitespace. Empty if nothing usable remains."""
    s = re.sub(r"[_\-]+", " ", category.lower())
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def habit_directive(term: str, freq: float, conf: float) -> str | None:
    """Translate one persisted sentinel belief into a plain-English directive, or None to omit
    (not a habit term, an uncertain belief, or an un-sanitizable category)."""
    m = _HABIT_RE.match(term)
    if m is None:
        return None  # not a learned-autonomy habit (e.g. the steadiness baseline) -> skip
    cat = m.group(1)
    friendly = _FRIENDLY.get(cat) or _humanize(cat)
    if not friendly or len(friendly) > _MAX_CATEGORY_LEN:
        return None  # fail safe: never inject malformed/unbounded text into the prompt
    exp = _expectation(freq, conf)
    if exp >= EXP_FLOOR:
        return (f"When you're fragmenting between apps, you've authorized JARVIS to automatically "
                f"hide {friendly} apps.")
    if exp <= 1.0 - EXP_FLOOR:
        return f"You've told JARVIS NOT to auto-hide {friendly} apps."
    return None  # uncertain middle -> omit


def render_habits(beliefs: list[tuple[str, float, float]]) -> str:
    """The 'Learned habits' context block from persisted (term, freq, conf) beliefs; '' if none are
    confident enough to surface."""
    lines = [d for (term, freq, conf) in beliefs if (d := habit_directive(term, freq, conf))]
    if not lines:
        return ""
    return ("Learned habits (how the user prefers JARVIS to operate — respect these):\n"
            + "\n".join(f"- {ln}" for ln in lines))
