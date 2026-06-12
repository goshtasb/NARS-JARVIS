"""ADR-053: recovery routing for a FAILED task. Functional Core (S-02) — pure, no I/O.

Given a failed `(action, arg)`, suggest sibling actions that take the SAME argument so the Canvas can
offer a one-click "Change tool ▾" fix for the exact routing mistakes we already emit error strings for
(e.g. `read_article` on a local path → `summarize_file`). Scoped strictly to the file-path family per
the ratified ADR — no universal sibling map until a proven need.
"""
from __future__ import annotations

# Every member takes a document/file-path argument. Order = preference: summarize_file first because it
# is the tool our routing errors name for a local document.
_FILE_FAMILY = ("summarize_file", "read_file", "read_article")
_LOCAL_FILE_TOOLS = ("summarize_file", "read_file")   # read a path on disk
_WEB_TOOL = "read_article"                              # fetches a URL, never a local path


def _is_url(arg: str) -> bool:
    return arg.strip().lower().startswith(("http://", "https://"))


def alternatives(action: str, arg: str) -> list[str]:
    """Sibling actions to offer when `action` failed on `arg`. Empty unless `action` is in the
    file-path family. Routes by argument shape so the offered tool can actually consume the input:

    - local path + a local-file tool that failed → the OTHER local-file tool(s); never the web reader.
    - local path + the web reader that failed (the user's exact bug) → the local-file tools.
    - URL + a local-file tool that failed → the web reader.
    - URL + the web reader that failed (e.g. timeout) → none (Retry, don't change tool).
    """
    if action not in _FILE_FAMILY:
        return []
    if _is_url(arg):
        return [_WEB_TOOL] if action != _WEB_TOOL else []
    return [a for a in _LOCAL_FILE_TOOLS if a != action]
