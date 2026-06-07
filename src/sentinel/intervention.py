"""Flow-intervention rendering — closed-vocabulary, deterministic, NO LLM (temporal logic never
touches the categorical formatter). Plus the level->steadiness baseline mapping. Pure.
"""
from __future__ import annotations

_STEADY = ("focused", "light")          # calm attention
_UNSTEADY = ("fragmented", "thrashing")  # fragmenting

DEFAULT_MINUTES = 25


def is_steady(level: str) -> bool:
    return level in _STEADY


def steadiness_belief(level: str) -> str:
    """Binary baseline observation for ONA: steady=freq 1, unsteady=freq 0. The Sentinel learns
    'usually steady' (a sudden unsteady reading is the surprise).

    Each observation carries SINGLE-EVIDENCE confidence 0.5 (one unit of evidence, w=1) — NOT a
    high confidence. This is load-bearing for the epistemic burn-in: with c=w/(w+k), repeated
    observations pool by NAL revision (0.50, 0.67, 0.75, 0.80, 0.83, 0.857…), so the baseline
    reaches the 0.85 floor only after ~6 confirmations. A high per-observation confidence would slam
    the belief to ~0.9 in ONE step and there would be no burn-in at all (measured: armed at obs #2).
    """
    freq = 1.0 if is_steady(level) else 0.0
    return f"<attention --> [steady]>. {{{freq:.1f} 0.5}}"


def intervention_prompt(level: str, distraction_categories: list[str],
                        minutes: int = DEFAULT_MINUTES) -> str:
    """The one deterministic interruption line (offers a concrete, reversible action). Closed vocab."""
    cats = ", ".join(distraction_categories) if distraction_categories else "distraction"
    return (f"⚠ Fragmentation spike ({level}) — you're churning into {cats}. "
            f"Hide {cats} apps for {minutes}m? [y/n]")
