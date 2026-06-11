"""Action execution (ADR-019). Imperative Shell (S-02) — the ONLY place an action reaches the OS.

`perform(name, arg)` validates the proposed action against the closed `catalog` (an unknown name or a
rejected argument never spawns) and then either returns a diagnostics report or runs the vetted argv
through `safespawn` (argv-only, env-scrubbed — ADR-015). `spawn` is injectable so tests record the
argv with **no real side effects**. `ActionRunner` is the small object injected into `Jarvis`.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Callable

import safespawn

from . import catalog
from . import documents
from . import orchestrate
from .diagnostics import audio_report, largest_apps_report, net_report, system_report
from .files import find_file

_TIMEOUT = 15  # seconds — these are quick system commands; never hang the converse turn
_WEB_TIMEOUT = 45  # seconds — read/browse may escalate to a rendered (headless Chromium) fetch
                   # (ADR-039: static GET ~8s + render cap 12s + settle + parse); bounded regardless
_WEB_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web.py")
_WEB_MODES = {"web_lookup": "search", "read_article": "read", "browse_page": "browse"}


def _web(name: str, arg: str, spawn: Callable) -> str:
    """Run a read-only web action (ADR-034/039) in an isolated subprocess via the sanctioned safespawn
    seam — keeps network egress, readability, AND the transient headless browser out of the daemon
    process. Returns the child's stdout (an `[ERROR: …]` string on the child's side) or an error if
    the fetch times out. Never raises."""
    mode = _WEB_MODES[name]
    try:
        result = spawn([sys.executable, _WEB_PY, mode, arg],
                       capture_output=True, text=True, timeout=_WEB_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — timeout/spawn failure reports, never crashes the turn
        return f"[ERROR: web request timed out or failed to start: {exc}]"
    out = (getattr(result, "stdout", "") or "").strip()
    return out or f"[ERROR: web request produced no output ({(getattr(result, 'stderr', '') or '').strip()[:120]})]"


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
        if action.name == "audio_status":
            return audio_report(spawn)
        if action.name == "network_status":
            return net_report(spawn)
        if action.name == "largest_apps":
            return largest_apps_report(spawn)
        return system_report()
    if action.kind == "query":           # read-only lookups (Spotlight search / web egress, ADR-034)
        if action.name in _WEB_MODES:
            return _web(action.name, arg, spawn)
        return find_file(arg, spawn=spawn)
    if action.kind == "orchestrate":     # ADR-049: verified actuation (actuate -> read-back -> report)
        return orchestrate.dispatch(action.name, arg, spawn)
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
