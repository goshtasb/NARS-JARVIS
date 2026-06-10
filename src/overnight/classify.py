"""Overnight safety classifier (ADR-031) — Functional Core (S-02).

The single, mathematically hard boundary deciding what may run UNATTENDED while you sleep. Only
read-only catalog actions are autonomous overnight; everything that changes state, touches the GUI, or
is destructive is HELD for explicit morning approval. The predicate keys off the CLOSED catalog's
`Action.kind`, so an action cannot become autonomous by omission — a kind outside the safe set (or any
`confirm` action) is held by default.
"""
from __future__ import annotations

from actions import Action

# Read-only kinds only: 'diag' (system report), 'query' (Spotlight find_file), and 'work' (ADR-032:
# read/summarize a local document, output only to a /tmp scratchpad). Everything else — 'argv'
# (system-config changes), 'nav'/'ax'/'agent' (GUI actuation), and any confirm action — is HELD.
_SAFE_KINDS = frozenset({"diag", "query", "work"})


def safe_autonomous(action: Action | None) -> bool:
    """True iff `action` may run unattended overnight: a known, read-only, non-confirm catalog action."""
    return action is not None and action.kind in _SAFE_KINDS and not action.confirm
