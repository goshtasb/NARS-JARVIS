"""File operations (ADR-025 — v1.0 breadth). Read-only for now: Spotlight file search.

`find_file` queries the macOS Spotlight index via `mdfind` — O(1)-fast (no recursive disk walk), and
the query is passed as a single argv element through `safespawn` (no shell, no injection). It mutates
nothing, so it is FRICTIONLESS.

Signal > noise (ADR-025 ranking): the OS does the index lookup (one spawn, full recall); Python applies
human context that `mdfind` can't express — a HARD blacklist of dev/system/cache paths (node_modules,
.git, Library, caches…) and a SOFT ranking boost for the user's primary folders (Desktop/Documents/
Downloads) and shallower paths. Whitelist is a boost, not a cage, so files elsewhere still surface.
Results are then HARD-CAPPED (top N + "…and M more") to protect the prompt budget.
"""
from __future__ import annotations

import os
from typing import Callable

import safespawn

_LIMIT = 5
_MAX_QUERY = 100
_HOME = os.path.expanduser("~")
_PREFERRED = tuple(os.path.join(_HOME, d) for d in ("Desktop", "Documents", "Downloads"))
# Slash-anchored path components a human never means. Lowercased for case-insensitive matching.
_NOISE = ("/node_modules/", "/.git/", "/library/", "/.trash/", "/__pycache__/", "/.cache/",
          "/caches/", "/.venv/", "/site-packages/", "/deriveddata/", "/.npm/", "/pods/", "/.cargo/",
          # system roots — never what a human means by "find my file"
          "/usr/", "/private/", "/system/", "/opt/", "/.bundle/")


def _is_noise(path: str) -> bool:
    low = path.lower()
    return any(marker in low for marker in _NOISE)


def _rank(path: str) -> tuple:
    """Sort key (higher = better, used with reverse=True): primary-folder hits win, then home, then
    shallower paths. A Desktop file beats one buried six folders deep in an archive."""
    in_preferred = any(path == p or path.startswith(p + os.sep) for p in _PREFERRED)
    under_home = path.startswith(_HOME + os.sep)
    return (in_preferred, under_home, -path.count(os.sep))


def find_file(query: str, spawn: Callable = safespawn.run, limit: int = _LIMIT) -> str:
    """Spotlight-search files by name, drop noise, rank by human context, cap. Never raises."""
    q = (query or "").strip()[:_MAX_QUERY]
    if not q:
        return "What file should I look for?"
    try:
        result = spawn(["mdfind", "-name", q], capture_output=True, text=True, timeout=10)
    except Exception as exc:  # noqa: BLE001 — a failed search reports, never crashes the turn
        return f"Couldn't search for files: {exc}"
    raw = [p for p in (getattr(result, "stdout", "") or "").splitlines() if p.strip()]
    paths = [p for p in raw if not _is_noise(p)]              # hard blacklist (the OS can't do this)
    if not paths:
        if raw:                                              # matches existed, but all were noise
            return f"Only system/cache files match {q!r} — nothing in your main folders."
        return f"No files found matching {q!r}."
    paths.sort(key=_rank, reverse=True)                       # soft ranking: user folders + shallow first
    head = f"Found {len(paths)} file{'s' if len(paths) != 1 else ''} matching {q!r}:"
    lines = [head] + [f"- {p}" for p in paths[:limit]]
    if len(paths) > limit:
        lines.append(f"…and {len(paths) - limit} more.")
    return "\n".join(lines)
