"""ADR-015: freeze the 'no autonomous action can carry network' invariant. sandbox-exec egress is
coarse (all-or-nothing), so the only safe posture is that nothing live-eligible ever needs network —
these assertions fail the build if a future operation/template/profile breaks that."""
import re
from pathlib import Path

from execution.catalog import OpName, is_state_changing, parse_operation, requires_network
from execution.live import LIVE_OPERATIONS
from execution.omniglass import OmniGlassExecutor
from execution.templates import command_for

_PROFILE = Path(__file__).resolve().parent / "profiles" / "air_gapped.sb"
_NETWORK_TOKENS = re.compile(r"\b(curl|wget|nc|ncat|ssh|scp|telnet|http|https|ftp)\b|://", re.I)


def test_live_operations_are_all_air_gapped() -> None:
    assert LIVE_OPERATIONS, "expected at least one live operation to assert on"
    for op in LIVE_OPERATIONS:
        assert requires_network(op) is False, f"live op requires network: {op}"


def test_requires_network_is_default_deny() -> None:
    # An operation absent from the manifest must be treated as network-requiring (default-deny),
    # so a new op can't become autonomy-eligible by omission.
    class _Ghost:
        name = "ghost_op_not_in_manifest"
    assert requires_network(_Ghost()) is True


def test_live_operations_are_not_state_changing_or_are_gated() -> None:
    # disk_usage / list_processes are read-only; opening apps is state-changing. Live ones must be safe.
    for op in LIVE_OPERATIONS:
        assert is_state_changing(op) is False, f"live op is state-changing: {op}"


def test_no_catalog_template_contains_a_network_token() -> None:
    # Every concrete argv template is a local binary invocation — never a network tool.
    for op_name in OpName:
        # build one representative operation per op via its bound enum (first member)
        from execution.catalog import _ARG_ENUM  # closed, human-authored
        for arg in _ARG_ENUM[op_name]:
            argv = command_for(parse_operation(op_name.value, arg.value))
            joined = " ".join(argv)
            assert not _NETWORK_TOKENS.search(joined), f"network token in template: {argv}"


def test_executor_refuses_network_ops_for_live() -> None:
    ex = OmniGlassExecutor(live_operations=LIVE_OPERATIONS)
    for op in LIVE_OPERATIONS:
        # live-eligible only if air-gapped; a network op can never be live-eligible
        assert ex.is_live_eligible(op) is True
    # and the eligibility predicate is False for anything network-requiring (default-deny ghost)
    class _Ghost:
        name = "ghost"
    assert ex.is_live_eligible(_Ghost()) is False


def test_sandbox_profile_has_no_network_allow() -> None:
    text = _PROFILE.read_text()
    assert "allow network" not in text.lower(), "air_gapped.sb must not allow any network"
    assert "(deny default)" in text.lower().replace("  ", " ") or "deny default" in text.lower()


if __name__ == "__main__":
    test_live_operations_are_all_air_gapped()
    test_requires_network_is_default_deny()
    test_live_operations_are_not_state_changing_or_are_gated()
    test_no_catalog_template_contains_a_network_token()
    test_executor_refuses_network_ops_for_live()
    test_sandbox_profile_has_no_network_allow()
    print("execution/test_network_invariants: OK")
