"""NARS-gated autonomy — the procedural appropriateness belief, asymmetric evidence, and the
two-condition gate. Pure (no brain I/O here; SentinelLoop does the query/feed). See ADR-006.

The Sentinel learns, per distraction *category* (comms/media — never per bundle id, so the macOS
category ontology keeps doing its job), whether hiding it while the user is distracted is approved.
It learns by accumulating the human's y/n consent as NAL evidence:

  - YES = single-evidence {1.0 0.5}: confidence climbs the same curve as the burn-in
    (0.50, 0.67, 0.75, 0.80, 0.83, 0.857) — ~6 approvals to earn autonomy.
  - NO  = heavy-evidence {0.0 0.9}: one or two declines collapse the expectation below the gate.
    Trust is earned slowly and lost fast (the safety ratchet).

The gate is TWO conditions, because confidence measures the AMOUNT of evidence, not its polarity:
six rejections also reach confidence 0.857. So we require sufficient evidence AND favorable polarity,
fused via expectation = conf*(freq-0.5)+0.5.
"""
from __future__ import annotations

CONF_FLOOR = 0.85   # enough evidence (≈6 confirmations)
EXP_FLOOR = 0.85    # ...and the evidence points to YES (with conf≥0.85 this needs freq ≳ 0.91)

YES: tuple[float, float] = (1.0, 0.5)
NO: tuple[float, float] = (0.0, 0.9)


def approved_term(category: str) -> str:
    """The procedural belief key for 'hiding <category> while distracted is approved'."""
    return f"<distracted_hide_{category} --> [approved]>"


def evidence_belief(category: str, approved: bool) -> str:
    """The Narsese belief to feed on a human decision (asymmetric weights)."""
    freq, conf = YES if approved else NO
    return f"{approved_term(category)}. {{{freq:.1f} {conf:.1f}}}"


def expectation(freq: float, conf: float) -> float:
    return conf * (freq - 0.5) + 0.5


def gate_passes(freq: float, conf: float) -> bool:
    """Autonomy granted iff enough evidence AND it favors YES. Confidence alone is NOT sufficient."""
    return conf >= CONF_FLOOR and expectation(freq, conf) >= EXP_FLOOR
