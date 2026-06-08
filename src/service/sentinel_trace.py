"""Sentinel observability formatters (ADR-016). Functional Core (S-02) — pure, numeric/category only.

Surfaces values the SurpriseDetector already computes (and used to discard) so the expectation math
can be watched approaching the 0.85 gate in real time, and shows each category's distance to arming.
NEVER includes an app id, window title, or any content — only a coarse bucket name + numbers.
"""
from __future__ import annotations

from .autonomy import EXP_FLOOR


def format_observation(bucket: str, level: str, surprise: float, prior_exp: float | None,
                       actual_exp: float, prior_conf: float, armed: bool) -> str:
    """One per-observation trace line (logged to the daemon log behind NARS_JARVIS_TRACE)."""
    pe = "n/a" if prior_exp is None else f"{prior_exp:.2f}"
    return (f"[trace] cat={bucket} level={level} surprise={surprise:.2f} prior_exp={pe} "
            f"actual_exp={actual_exp:.2f} prior_conf={prior_conf:.2f} armed={armed}")


def format_gate_proximity(items: list[tuple[str, float]]) -> str:
    """Per-category expectation vs the EXP_FLOOR — how close each is to earning autonomy.
    `items` = (category, expectation). Returns a benign string when nothing is known yet."""
    if not items:
        return "gate: no learned categories yet"
    parts = []
    for cat, exp in items:
        delta = EXP_FLOOR - exp
        state = "ARMED" if exp >= EXP_FLOOR else f"Δ{delta:.2f}-to-arm"
        parts.append(f"{cat} E={exp:.2f} {state}")
    return "gate: " + " · ".join(parts)
