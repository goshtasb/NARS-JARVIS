"""Action execution (ADR-019). Imperative Shell (S-02) — the ONLY place an action reaches the OS.

`perform(name, arg)` validates the proposed action against the closed `catalog` (an unknown name or a
rejected argument never spawns) and then either returns a diagnostics report or runs the vetted argv
through `safespawn` (argv-only, env-scrubbed — ADR-015). `spawn` is injectable so tests record the
argv with **no real side effects**. `ActionRunner` is the small object injected into `Jarvis`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import safespawn

from . import catalog
from . import documents
from .diagnostics import system_report
from .files import find_file

_TIMEOUT = 15  # seconds — these are quick system commands; never hang the converse turn


def _work(name: str, arg: str, llm) -> str:
    """Run a read-only document work action (ADR-032). `llm` (or None) is the model handle for
    summarize_file; read_file needs no model. Wraps the LLM's generate_text into the injected callable."""
    if name == "read_file":
        return documents.do_read_file(arg)
    generate = None
    if llm is not None and hasattr(llm, "generate_text"):
        generate = lambda system, user, max_tokens: llm.generate_text(system, user, max_tokens=max_tokens)
    return documents.do_summarize_file(arg, generate)


@dataclass(frozen=True)
class ConsentSpec:
    """A destructive action's deferred execution (ADR-020): a human label + the on-approve thunk that
    actually runs it. `propose` returns one instead of executing; the consent gate runs the thunk only
    on the user's explicit approval. The thunk is held server-side — never serialized to a client."""
    label: str
    on_approve: Callable[[], str]


def perform(name: str, arg: str = "", *, spawn: Callable = safespawn.run, llm=None) -> str:
    """Validate then run a single action. Returns a short user-facing result string; never raises."""
    action = catalog.resolve(name)
    if action is None:
        return f"I don't know how to do that ({name})."
    if action.kind == "diag":
        return system_report()
    if action.kind == "query":           # read-only search (e.g. find_file via Spotlight)
        return find_file(arg, spawn=spawn)
    if action.kind == "work":            # read-only document work (ADR-032): read / summarize
        return _work(action.name, arg, llm)
    argv = catalog.argv_for(action, arg)
    if argv is None:
        return f"I can't do that — {arg!r} isn't a safe argument for {action.name}."
    try:
        result = spawn(argv, capture_output=True, text=True, timeout=_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — a failed command reports, never crashes the turn
        return f"Tried to {action.label}, but it failed: {exc}"
    # Honesty (ADR-019 follow-up): a child that exits non-zero does NOT raise — we must check the
    # return code, or we'd report "(Done:)" for a command that actually failed (e.g. `open -a
    # Accessibility` exits 1, "Unable to find application named 'Accessibility'"). Report the truth.
    rc = getattr(result, "returncode", 0) or 0
    if rc != 0:
        stderr = (getattr(result, "stderr", "") or "").strip().splitlines()
        why = stderr[-1] if stderr else f"exit code {rc}"
        return f"Couldn't {action.label}{f' ({arg})' if action.takes_arg and arg else ''} — {why}"
    if action.takes_arg:
        return f"(Done: {action.label} — {arg})"
    return f"(Done: {action.label})"


class ActionRunner:
    """The action surface injected into `Jarvis` (ADR-019). Thin wrapper over the closed catalog +
    `perform`, so the orchestrator depends only on `available()` + `perform()` and tests can stub it.
    `spawn` defaults to the sanctioned `safespawn.run`; inject a recorder to avoid OS side effects."""

    def __init__(self, spawn: Callable = safespawn.run, llm=None) -> None:
        self._spawn = spawn
        self._llm = llm           # ADR-032: model handle for kind="work" (summarize_file); may be None

    def available(self) -> list[tuple[str, str]]:
        return catalog.available()

    def perform(self, name: str, arg: str = "") -> str:
        return perform(name, arg, spawn=self._spawn, llm=self._llm)

    def propose(self, name: str, arg: str = "") -> tuple[str | None, ConsentSpec | None]:
        """Policy layer over `perform` (ADR-020): a reversible action runs immediately and returns
        `(result, None)`; a `confirm` action validates its argument now, then returns `(None,
        ConsentSpec)` WITHOUT executing — the caller routes the spec through the consent gate. An
        unknown name or unsafe argument returns `(refusal, None)` and never opens a consent."""
        action = catalog.resolve(name)
        if action is None:
            return (f"I don't know how to do that ({name}).", None)
        if not action.confirm:
            return (self.perform(name, arg), None)
        if action.kind == "argv" and catalog.argv_for(action, arg) is None:
            return (f"I can't do that — {arg!r} isn't a safe argument for {action.name}.", None)
        label = action.label + (f": {arg}" if action.takes_arg and arg else "")
        return (None, ConsentSpec(label=label, on_approve=lambda: self.perform(name, arg)))
