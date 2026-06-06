"""Shared text utilities. Functional Core (S-02) — pure, cross-cutting."""
from __future__ import annotations

import re


def atom(name: str) -> str:
    """Sanitize a string into a valid Narsese atom: [a-z0-9_], spaces -> underscores. Pure.

    'CPU' -> 'cpu';  'Obj Dir!' -> 'obj_dir';  'penicillin safe' -> 'penicillin_safe'.
    Empty after sanitization -> '_' (never produces an invalid empty atom).
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_ ]", "", name).strip().lower().replace(" ", "_")
    return cleaned or "_"
