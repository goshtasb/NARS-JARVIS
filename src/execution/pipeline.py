"""C4 decision gate (M3 Phase A). Validate against the catalog, then evaluate the autonomy predicate.

A proposed (op_name, arg_name) is parsed against the closed enum catalog (unregistered -> fatal
UnregisteredOperationError + security log). A valid operation is wrapped in a Proposal whose
`autonomous` flag is the strict predicate result; False => Suggestion-Only Mode.
"""
from __future__ import annotations

from .autonomy import AutonomyPolicy, DecisionStats, is_autonomous
from .catalog import parse_operation
from .executor import Proposal


def decide(op_name: str, arg_name: str, stats: DecisionStats,
           policy: AutonomyPolicy | None = None) -> Proposal:
    operation = parse_operation(op_name, arg_name)  # raises if not in the closed catalog
    autonomous = is_autonomous(policy or AutonomyPolicy(), stats)
    return Proposal(operation=operation, autonomous=autonomous, stats=stats)
