"""execution — M3: the architecture of constraint.

Phase A (built): the closed typed Operation catalog (ADR-002), the autonomy-eligibility predicate
(overriding ONA's permissive defaults), and a MockExecutor with a feedback loop.
Phase B (scaffold, GATED): fixed-argv command templates and a gated OmniGlassExecutor, drop-in
ready but with no path to a live engine until a real SandboxClient is wired. The 2026-06-05
sandbox crucible constraints (network egress = human-only; env-filter must be verified) are
enforced in code (see `omniglass.py`).
Public interface (ADR-001).
"""
from .autonomy import MOTOR_BABBLING_CHANCE, AutonomyPolicy, DecisionStats, is_autonomous
from .catalog import (
    AppEnum,
    Operation,
    OpName,
    SavedCommandEnum,
    UnregisteredOperationError,
    parse_operation,
    requires_network,
)
from .executor import MockExecutor, Proposal, render_operation
from .live import LIVE_OPERATIONS, build_air_gapped_executor
from .omniglass import (
    Executor,
    ExecutionNotAuthorizedError,
    OmniGlassExecutor,
    SandboxClient,
)
from .pipeline import decide
from .sandbox_client import AirGappedSandboxClient
from .templates import command_for

__all__ = [
    "OpName",
    "AppEnum",
    "SavedCommandEnum",
    "Operation",
    "parse_operation",
    "requires_network",
    "UnregisteredOperationError",
    "MOTOR_BABBLING_CHANCE",
    "AutonomyPolicy",
    "DecisionStats",
    "is_autonomous",
    "Proposal",
    "MockExecutor",
    "render_operation",
    "decide",
    "command_for",
    "Executor",
    "SandboxClient",
    "OmniGlassExecutor",
    "ExecutionNotAuthorizedError",
    "AirGappedSandboxClient",
    "build_air_gapped_executor",
    "LIVE_OPERATIONS",
]
