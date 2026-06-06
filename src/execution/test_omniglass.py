"""Phase-B scaffold tests: templates complete, live execution REFUSED while gated, protocol drop-in,
plus the 2026-06-05 crucible constraints (network egress = human-only; env-filter verification)."""
from execution import catalog
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


class FakeSandbox:
    """A non-live test double for the SandboxClient seam — records argv, never runs anything."""

    def __init__(self, env_ok: bool = True) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._env_ok = env_ok

    def env_filter_verified(self) -> bool:
        return self._env_ok

    def run_sandboxed(self, argv: tuple[str, ...]) -> bool:
        self.calls.append(argv)
        return True


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
    sandbox = FakeSandbox()
    executor = OmniGlassExecutor(client=sandbox, authorized=True, sink=lambda line: None)
    proposal = decide("run_saved_command", "disk_usage", DecisionStats(0.9, 0.95, 20, 8))
    executor.execute(proposal)
    assert sandbox.calls == [("df", "-h")]  # fixed argv template, never a generated string


def test_network_operation_is_never_autonomous() -> None:
    # Crucible constraint #1: a network-requiring op is hard-gated to human-confirmation even
    # when authorized + autonomous + a client is wired. It must NEVER reach the live seam.
    saved = dict(catalog._REQUIRES_NETWORK)
    catalog._REQUIRES_NETWORK[OpName.OPEN_APP] = True  # flag open_app as network-requiring
    try:
        sandbox = FakeSandbox()
        lines: list[str] = []
        executor = OmniGlassExecutor(client=sandbox, authorized=True, sink=lines.append)
        proposal = decide("open_app", "slack", DecisionStats(0.99, 1.0, 50, 30))  # autonomous=True
        executor.execute(proposal)
        assert sandbox.calls == []  # live seam never touched
        assert any("network egress" in line for line in lines)
    finally:
        catalog._REQUIRES_NETWORK.clear()
        catalog._REQUIRES_NETWORK.update(saved)


def test_live_execution_refused_when_env_filter_unverified() -> None:
    # Crucible constraint #2: refuse to spawn an air-gapped op if env-filter isn't verified.
    executor = OmniGlassExecutor(client=FakeSandbox(env_ok=False), authorized=True,
                                 sink=lambda line: None)
    proposal = decide("run_saved_command", "disk_usage", DecisionStats(0.9, 0.95, 20, 8))
    try:
        executor.execute(proposal)
    except ExecutionNotAuthorizedError:
        return
    raise AssertionError("must refuse to spawn while env_filter is unverified")


if __name__ == "__main__":
    test_templates_cover_every_catalog_member()
    test_unauthorized_autonomous_execution_refused()
    test_suggestion_mode_never_touches_engine()
    test_both_executors_satisfy_the_interface()
    test_authorized_path_submits_fixed_argv_via_fake_client()
    test_network_operation_is_never_autonomous()
    test_live_execution_refused_when_env_filter_unverified()
    print("execution/test_omniglass: OK")
