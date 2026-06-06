"""execution — M3: the architecture of constraint.

Phase A (built): the closed typed Operation catalog (ADR-002), the autonomy-eligibility predicate
(overriding ONA's permissive defaults), and a MockExecutor with a feedback loop.
Phase B (scaffold only, NOT wired): fixed-argv command templates and a gated OmniGlassExecutor,
drop-in ready but with no path to a live engine until the sandbox audit clears.
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
)
from .executor import MockExecutor, Proposal, render_operation
from .omniglass import (
    Executor,
    ExecutionNotAuthorizedError,
    OmniGlassExecutor,
    SandboxClient,
)
from .pipeline import decide
from .templates import command_for

__all__ = [
    "OpName",
    "AppEnum",
    "SavedCommandEnum",
    "Operation",
    "parse_operation",
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
]
