"""Phase-B scaffold tests: templates complete, live execution REFUSED while gated, protocol drop-in."""
from execution.autonomy import DecisionStats
from execution.catalog import AppEnum, OpName, Operation, SavedCommandEnum
from execution.executor import MockExecutor
from execution.omniglass import (
    ExecutionNotAuthorizedError,
    Executor,
    OmniGlassExecutor,
)
from execution.pipeline import decide
from execution.templates import command_for


def test_templates_cover_every_catalog_member() -> None:
    for app in AppEnum:
        assert command_for(Operation(OpName.OPEN_APP, app))  # no KeyError
    for cmd in SavedCommandEnum:
        assert command_for(Operation(OpName.RUN_SAVED_COMMAND, cmd))


def test_unauthorized_autonomous_execution_refused() -> None:
    executor = OmniGlassExecutor()  # authorized=False, client=None
    proposal = decide("open_app", "slack", DecisionStats(0.9, 0.95, 20, 8))  # autonomous=True
    try:
        executor.execute(proposal)
    except ExecutionNotAuthorizedError:
        return
    raise AssertionError("live execution MUST be refused while gated")


def test_suggestion_mode_never_touches_engine() -> None:
    lines: list[str] = []
    executor = OmniGlassExecutor(sink=lines.append)
    proposal = decide("open_app", "slack", DecisionStats(0.45, 1.0, 2, 0))  # not autonomous
    executor.execute(proposal)  # must NOT raise; just surfaces a suggestion
    assert any("Awaiting User" in line for line in lines)


def test_both_executors_satisfy_the_interface() -> None:
    assert isinstance(OmniGlassExecutor(), Executor)
    assert isinstance(MockExecutor(), Executor)  # drop-in interchangeable


def test_authorized_path_submits_fixed_argv_via_fake_client() -> None:
    # Proves the seam works with a FAKE client — still no real OmniGlass engine involved.
    calls: list[tuple[str, ...]] = []

    class FakeSandbox:
        def run_sandboxed(self, argv: tuple[str, ...]) -> bool:
            calls.append(argv)
            return True

    executor = OmniGlassExecutor(client=FakeSandbox(), authorized=True, sink=lambda line: None)
    proposal = decide("run_saved_command", "disk_usage", DecisionStats(0.9, 0.95, 20, 8))
    executor.execute(proposal)
    assert calls == [("df", "-h")]  # fixed argv template, never a generated string


if __name__ == "__main__":
    test_templates_cover_every_catalog_member()
    test_unauthorized_autonomous_execution_refused()
    test_suggestion_mode_never_touches_engine()
    test_both_executors_satisfy_the_interface()
    test_authorized_path_submits_fixed_argv_via_fake_client()
    print("execution/test_omniglass: OK")
