"""Executor tests: suggestion vs autonomous rendering, and the ONA reinforce/erode feedback cycle."""
from brain import Brain
from execution.autonomy import DecisionStats
from execution.executor import MockExecutor
from execution.pipeline import decide


def test_coincidence_trapped_in_suggestion_mode() -> None:
    lines: list[str] = []
    proposal = decide("open_app", "slack", DecisionStats(0.45, 1.0, 2, 0))  # weak evidence
    MockExecutor(sink=lines.append).execute(proposal, simulate_success=True)
    assert proposal.autonomous is False
    assert any("open_app(AppEnum.SLACK)" in line and "Awaiting User" in line for line in lines)


def test_proven_habit_executes_autonomously() -> None:
    lines: list[str] = []
    proposal = decide("open_app", "slack", DecisionStats(0.9, 0.95, 20, 8))  # strong evidence
    MockExecutor(sink=lines.append).execute(proposal, simulate_success=True)
    assert proposal.autonomous is True
    assert any("Autonomous" in line for line in lines)


def test_feedback_reinforces_then_erodes_in_ona() -> None:
    with Brain(cycles_per_step=50) as brain:
        def feedback(operation: object, success: bool) -> None:
            freq = "1.0" if success else "0.0"
            brain.add_belief(f"<open_slack --> [good_habit]>. {{{freq} 0.9}}")

        executor = MockExecutor(on_feedback=feedback, sink=lambda line: None)
        proposal = decide("open_app", "slack", DecisionStats(0.9, 0.95, 20, 8))

        for _ in range(3):  # three successful outcomes -> reinforce
            executor.execute(proposal, simulate_success=True)
        reinforced = brain.ask("<open_slack --> [good_habit]>?")
        assert reinforced is not None and reinforced.truth is not None
        assert reinforced.truth.frequency > 0.5

        executor.execute(proposal, simulate_success=False)  # a rejection -> erode
        eroded = brain.ask("<open_slack --> [good_habit]>?")
        assert eroded is not None and eroded.truth is not None
        assert eroded.truth.frequency < reinforced.truth.frequency


if __name__ == "__main__":
    test_coincidence_trapped_in_suggestion_mode()
    test_proven_habit_executes_autonomously()
    test_feedback_reinforces_then_erodes_in_ona()
    print("execution/test_executor: OK")
