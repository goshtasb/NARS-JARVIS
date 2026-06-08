"""Action execution (ADR-019). Imperative Shell (S-02) — the ONLY place an action reaches the OS.

`perform(name, arg)` validates the proposed action against the closed `catalog` (an unknown name or a
rejected argument never spawns) and then either returns a diagnostics report or runs the vetted argv
through `safespawn` (argv-only, env-scrubbed — ADR-015). `spawn` is injectable so tests record the
argv with **no real side effects**. `ActionRunner` is the small object injected into `Jarvis`.
"""
from __future__ import annotations

from typing import Callable

import safespawn

from . import catalog
from .diagnostics import system_report

_TIMEOUT = 15  # seconds — these are quick system commands; never hang the converse turn


def perform(name: str, arg: str = "", *, spawn: Callable = safespawn.run) -> str:
    """Validate then run a single action. Returns a short user-facing result string; never raises."""
    action = catalog.resolve(name)
    if action is None:
        return f"I don't know how to do that ({name})."
    if action.kind == "diag":
        return system_report()
    argv = catalog.argv_for(action, arg)
    if argv is None:
        return f"I can't do that — {arg!r} isn't a safe argument for {action.name}."
    try:
        spawn(argv, capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — a failed command reports, never crashes the turn
        return f"Tried to {action.label}, but it failed: {exc}"
    if action.takes_arg:
        return f"(Done: {action.label} — {arg})"
    return f"(Done: {action.label})"


class ActionRunner:
    """The action surface injected into `Jarvis` (ADR-019). Thin wrapper over the closed catalog +
    `perform`, so the orchestrator depends only on `available()` + `perform()` and tests can stub it.
    `spawn` defaults to the sanctioned `safespawn.run`; inject a recorder to avoid OS side effects."""

    def __init__(self, spawn: Callable = safespawn.run) -> None:
        self._spawn = spawn

    def available(self) -> list[tuple[str, str]]:
        return catalog.available()

    def perform(self, name: str, arg: str = "") -> str:
        return perform(name, arg, spawn=self._spawn)
