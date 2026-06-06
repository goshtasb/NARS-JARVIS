"""OmniGlass-backed executor — SCAFFOLD ONLY (M3 Phase B, NOT wired to a live engine).

Implements the `Executor` interface so it is a drop-in for `MockExecutor` the moment the OmniGlass
sandbox passes its adversarial audit. Until BOTH `authorized=True` AND a real `SandboxClient` are
injected, it refuses to execute — there is no path to a live engine in this file. The single live
seam is `client.run_sandboxed(argv)`, and the default client is None.
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from .catalog import Operation
from .executor import Proposal
from .templates import command_for


class ExecutionNotAuthorizedError(Exception):
    """Live execution attempted before the OmniGlass sandbox audit cleared / a client was wired."""


@runtime_checkable
class Executor(Protocol):
    def execute(self, proposal: Proposal, simulate_success: bool = True) -> None: ...


@runtime_checkable
class SandboxClient(Protocol):
    """The seam a real, audited OmniGlass client must implement: run a fixed argv, sandboxed."""

    def run_sandboxed(self, argv: tuple[str, ...]) -> bool: ...  # returns success


class OmniGlassExecutor:
    """Phase-B scaffold. GATED: refuses until the sandbox audit is signed off AND a client is wired."""

    def __init__(self, client: SandboxClient | None = None, authorized: bool = False,
                 sink: Callable[[str], object] = print,
                 on_feedback: Callable[[Operation, bool], object] | None = None) -> None:
        self._client = client
        self._authorized = authorized
        self._sink = sink
        self._on_feedback = on_feedback or (lambda operation, success: None)

    def execute(self, proposal: Proposal, simulate_success: bool = True) -> None:
        argv = command_for(proposal.operation)  # fixed argv template (no shell string)
        if not proposal.autonomous:
            self._sink(f"[SUGGEST]: {argv} - Awaiting User")  # Suggestion-Only: never touches engine
            return
        if not self._authorized or self._client is None:
            raise ExecutionNotAuthorizedError(
                "OmniGlass live execution is gated: requires a PASSED adversarial sandbox audit "
                "AND an injected SandboxClient. Phase B is not wired."
            )
        success = self._client.run_sandboxed(argv)  # the ONLY live seam — un-wired by default
        self._sink(f"[EXECUTED]: {argv} -> success={success}")
        self._on_feedback(proposal.operation, success)
