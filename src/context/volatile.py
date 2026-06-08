"""Volatile-fact guard (ADR-010). Functional Core (S-02) — pure, the deterministic backstop that
keeps transient statements OUT of durable memory.

The bug this fixes: the user corrects JARVIS's guessed time ("it's 8pm"), the auto-memory pipeline
saves "the current time is 8 pm" as a permanent fact, and recall then reports 8pm forever. The
system prompt already discourages saving live facts, but — per the ADR-008/009 lesson — we never
rely on the 7B to self-police. `is_volatile` is the hard guard, applied before any memory write.

Posture is DEFAULT-ALLOW (opposite of the slot registry's default-deny): only statements that
clearly assert something ephemeral match. A missed novel phrasing fails SAFE — it gets saved,
shown via "(Saved: …)", and the user can `forget` it — because silently dropping a legitimate
memory is the worse error for a storage engine.
"""
from __future__ import annotations

import re

_VOLATILE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcurrent (?:time|date|day)\b", re.I),
    re.compile(r"\b(?:the )?time is\b", re.I),
    re.compile(r"\bit(?:'?s| is)\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)\b", re.I),  # it's/it is 8:30 pm
    re.compile(r"\b\d{1,2}\s*o'?clock\b", re.I),
    re.compile(r"\b(?:today|right now|currently|at the moment)\b", re.I),
    re.compile(r"\b(?:the )?date is\b", re.I),
    re.compile(r"\b(?:cpu|memory|mem)\s*(?:is|=|usage)\b", re.I),       # live system load
)


def is_volatile(text: str) -> bool:
    """True if `text` asserts an ephemeral fact (time/date/'right now'/live load) that must NOT be
    persisted as durable memory. Default-allow: no match → not volatile → safe to save."""
    return any(p.search(text) for p in _VOLATILE_PATTERNS)
