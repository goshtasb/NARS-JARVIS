"""Habit quantization & Narsese mapping (ADR-026 Phase 1). Functional Core (S-02) — pure.

Turns a real action into a coarse, RECURRING habit key so NARS evidence accumulates instead of
fragmenting. The mechanical reason this matters: ONA confidence is c = w/(w+1); only if the *same*
term recurs across days does w climb (1,2,3…) and expectation cross the 0.85 gate. Quantizing time to
an hour-bucket makes "9am today" and "9am tomorrow" the same term. Dense/raw context (exact timestamp,
CPU%) would make every event a singleton (w=1, c=0.5, E=0.75) — a habit could never form.

Depends only on `actions` (for eligibility); no service-layer dependency.
"""
from __future__ import annotations

import re
from datetime import datetime

from actions import resolve as _resolve_action

# Asymmetric NAL evidence weights. MUST stay in lockstep with service/autonomy.py (the Sentinel's
# verified ramp): a YES climbs slowly (~6 confirmations to cross 0.85), a NO collapses fast. Duplicated
# (not imported) to keep the habits domain free of a service dependency.
YES: tuple[float, float] = (1.0, 0.5)
NO: tuple[float, float] = (0.0, 0.9)


def time_bucket(now: datetime) -> str:
    """Quantize a timestamp to a coarse recurring bucket — Phase 1: hour-of-day (`h09`). Coarse enough
    that the same daily moment maps to one term (weekday/part-of-day are Phase 2)."""
    return f"h{now.hour:02d}"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def habit_key(bucket: str, action: str, arg: str = "") -> str:
    """A canonical, term-safe key for a (context, action[, arg]) habit, e.g. 'h09_set_brightness_100'."""
    parts = [bucket, _slug(action)]
    arg_slug = _slug(arg)
    if arg_slug:
        parts.append(arg_slug)
    return "_".join(p for p in parts if p)


def habit_term(key: str) -> str:
    """The Narsese procedural belief: 'doing <key> is approved'."""
    return f"<habit_{key} --> [approved]>"


def habit_evidence(key: str, approved: bool) -> str:
    """The belief to feed on an action/decision, with asymmetric weights."""
    freq, conf = YES if approved else NO
    return f"{habit_term(key)}. {{{freq:.1f} {conf:.1f}}}"


def bucket_label(bucket: str) -> str:
    """Render an hour bucket as a human time, e.g. 'h09' -> '9:00 AM', 'h14' -> '2:00 PM' (ADR-027)."""
    try:
        h = int(bucket.lstrip("h"))
    except ValueError:
        return bucket
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:00 {suffix}"


def evidence_count(confidence: float) -> int:
    """Approximate confirmations behind a confidence: ONA c = w/(w+1) ⇒ w ≈ c/(1-c). For honest,
    probability-free progress reporting ('seen ~4×') — never shown as a percentage (ADR-027)."""
    if confidence >= 1.0:
        return 999
    return round(confidence / (1.0 - confidence))


def describe_habit(action: str, arg: str, bucket: str) -> str:
    """Human phrase for a habit, e.g. ('set_brightness','100','h09') -> 'set brightness 100 around 9:00 AM'."""
    verb = action.replace("_", " ").strip()
    arg = (arg or "").strip()
    phrase = f"{verb} {arg}".strip() if arg else verb
    return f"{phrase} around {bucket_label(bucket)}"


def eligible(action: str) -> bool:
    """Only safe, repeatable state-changers form habits: actuations (`argv`/`nav`) that are NOT
    destructive (`confirm`) and NOT read-only (`diag`/`query`). Auto-proposing a search (`find_file`) or
    `empty_trash` is nonsensical/unsafe, so they never become habits."""
    a = _resolve_action(action)
    return a is not None and a.kind in ("argv", "nav") and not a.confirm
