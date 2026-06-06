"""Pure builders for sentinel Narsese events. Functional Core (S-02)."""
from __future__ import annotations

from shared import atom


def signal_event(signal: str, level: str) -> str:
    """signal_event('cpu', 'pegged') -> '<cpu --> [pegged]>. :|:'"""
    return f"<{atom(signal)} --> [{atom(level)}]>. :|:"


def activity_event(directory: str, label: str) -> str:
    """activity_event('obj_dir', 'active') -> '<obj_dir --> [active]>. :|:'"""
    return f"<{atom(directory)} --> [{atom(label)}]>. :|:"
