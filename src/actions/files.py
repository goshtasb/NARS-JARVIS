"""File operations (ADR-025 — v1.0 breadth). Read-only for now: Spotlight file search.

`find_file` queries the macOS Spotlight index via `mdfind` — O(1)-fast (no recursive disk walk), and
the query is passed as a single argv element through `safespawn` (no shell, no injection). It mutates
nothing, so it is FRICTIONLESS. Results are HARD-CAPPED before returning to the LLM: a generic query
("image") can return hundreds of paths and overflow the prompt budget, so we return the top few and
note how many more matched.
"""
from __future__ import annotations

from typing import Callable

import safespawn

_LIMIT = 5          # top results returned to the model (protect the prompt budget)
_MAX_QUERY = 100    # ignore absurdly long queries


def find_file(query: str, spawn: Callable = safespawn.run, limit: int = _LIMIT) -> str:
    """Spotlight-search files by name; return a short, capped, human-readable result. Never raises."""
    q = (query or "").strip()[:_MAX_QUERY]
    if not q:
        return "What file should I look for?"
    try:
        result = spawn(["mdfind", "-name", q], capture_output=True, text=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 — a failed search reports, never crashes the turn
        return f"Couldn't search for files: {exc}"
    paths = [p for p in (getattr(result, "stdout", "") or "").splitlines() if p.strip()]
    if not paths:
        return f"No files found matching {q!r}."
    head = f"Found {len(paths)} file{'s' if len(paths) != 1 else ''} matching {q!r}:"
    lines = [head] + [f"- {p}" for p in paths[:limit]]
    if len(paths) > limit:
        lines.append(f"…and {len(paths) - limit} more.")
    return "\n".join(lines)
