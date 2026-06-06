"""Mock executor (M3 Phase A). Imperative Shell — prints the validated, bound proposal and feeds a
simulated outcome back for the habit reinforce/erode cycle. NO real execution; the OmniGlass
wiring is held behind the adversarial sandbox audit (ADR-002, PRD M3 prerequisites).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .autonomy import DecisionStats
from .catalog import Operation


@dataclass(frozen=True)
class Proposal:
    operation: Operation
    autonomous: bool
    stats: DecisionStats


def render_operation(operation: Operation) -> str:
    """Operation(OpName.OPEN_APP, AppEnum.SLACK) -> 'open_app(AppEnum.SLACK)'."""
    return f"{operation.name.value}({type(operation.arg).__name__}.{operation.arg.name})"


class MockExecutor:
    def __init__(self, on_feedback: Callable[[Operation, bool], object] | None = None,
                 sink: Callable[[str], object] = print) -> None:
        self._on_feedback = on_feedback or (lambda operation, success: None)
        self._sink = sink

    def execute(self, proposal: Proposal, simulate_success: bool = True) -> None:
        mode = "True (Autonomous)" if proposal.autonomous else "False (Awaiting User)"
        self._sink(f"[EXECUTE PROPOSAL]: {render_operation(proposal.operation)} - Autonomy: {mode}")
        # Mock: feed the simulated outcome back into ONA for the reinforce/erode cycle.
        self._on_feedback(proposal.operation, simulate_success)
