"""OmniGlass-backed executor — Phase-B scaffold, GATED, with the crucible constraints enforced.

Implements the `Executor` interface so it is a drop-in for `MockExecutor` the moment the OmniGlass
sandbox passes its adversarial audit. Until BOTH `authorized=True` AND a real `SandboxClient` are
injected, it refuses to execute — there is no path to a live engine in this file. The single live
seam is `client.run_sandboxed(argv)`, and the default client is None.

Two constraints proven necessary by the 2026-06-05 local sandbox crucible are enforced HERE, in
code, so they cannot be bypassed by configuration or autonomy state
(docs/audits/omniglass-v1.0.0-beta-local-RESULTS-2026-06-05.md):
  1. NETWORK: an operation that requires network egress is PERMANENTLY ineligible for autonomy —
     downgraded to human-confirmation, never reaching the live seam. sandbox-exec cannot do
     domain-level egress filtering, so a network grant = arbitrary-IP exfiltration.
  2. ENV-FILTER: live execution is refused unless the client confirms the env-filter pipeline is
     active. Secret-env protection is the env_filter layer, NOT the sandbox profile itself.
"""
from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from .catalog import Operation, requires_network
from .executor import Proposal
from .templates import command_for


class ExecutionNotAuthorizedError(Exception):
    """Live execution attempted before the OmniGlass sandbox audit cleared / a client was wired."""


@runtime_checkable
class Executor(Protocol):
    def execute(self, proposal: Proposal, simulate_success: bool = True) -> None: ...


@runtime_checkable
class SandboxClient(Protocol):
    """The seam a real, audited OmniGlass client must implement.

    `env_filter_verified()` MUST return True only when the env-filter pipeline (which strips
    secrets from the child environment — the sandbox profile does not) is active and verified.
    The executor calls it before every spawn and refuses if it is False.
    """

    def env_filter_verified(self) -> bool: ...  # env-filter pipeline active + verified
    def run_sandboxed(self, argv: tuple[str, ...]) -> bool: ...  # returns success


class OmniGlassExecutor:
    """Phase-B scaffold. GATED: refuses until the sandbox audit is signed off AND a client is wired."""

    def __init__(self, client: SandboxClient | None = None, authorized: bool = False,
                 live_operations: frozenset[Operation] = frozenset(),
                 sink: Callable[[str], object] = print,
                 on_feedback: Callable[[Operation, bool], object] | None = None) -> None:
        self._client = client
        self._authorized = authorized
        # The closed allowlist of operations cleared for LIVE autonomous execution. Default empty:
        # nothing is live unless explicitly authorized. Anything not here stays Suggestion-Only.
        self._live_operations = live_operations
        self._sink = sink
        self._on_feedback = on_feedback or (lambda operation, success: None)

    def execute(self, proposal: Proposal, simulate_success: bool = True) -> None:
        operation = proposal.operation
        argv = command_for(operation)  # fixed argv template (no shell string)
        # Constraint #1 — network egress can't be sandboxed: such ops are NEVER autonomous,
        # regardless of the predicate or `authorized`. They fall through to human-confirmation.
        # Constraint #1b — only operations on the explicit live allowlist may reach the engine.
        network = requires_network(operation)
        live_eligible = operation in self._live_operations
        if network or not live_eligible or not proposal.autonomous:
            if network:
                reason = "Awaiting User (network egress — human-only per crucible)"
            elif not live_eligible:
                reason = "Awaiting User (not cleared for live autonomy)"
            else:
                reason = "Awaiting User"
            self._sink(f"[SUGGEST]: {argv} - {reason}")  # Suggestion-Only: never touches engine
            return
        if not self._authorized or self._client is None:
            raise ExecutionNotAuthorizedError(
                "OmniGlass live execution is gated: requires a PASSED adversarial sandbox audit "
                "AND an injected SandboxClient. Phase B is not wired."
            )
        # Constraint #2 — refuse to spawn unless env-filter is verified active (the sandbox
        # profile alone does NOT strip secrets from the environment).
        if not self._client.env_filter_verified():
            raise ExecutionNotAuthorizedError(
                "env_filter pipeline not verified active — refusing to spawn. The sandbox profile "
                "alone does NOT strip secrets from the environment (see audit 2026-06-05)."
            )
        success = self._client.run_sandboxed(argv)  # the ONLY live seam — air-gapped ops only
        self._sink(f"[EXECUTED]: {argv} -> success={success}")
        self._on_feedback(operation, success)
