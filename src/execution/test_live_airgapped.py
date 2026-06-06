"""M3 Phase B — air-gapped LIVE proof.

Proves the end-to-end decide -> execute loop actually runs `df -h` under `sandbox-exec` (real
process, real sandbox), that ONLY `disk_usage` is live-eligible, and that env-filter is verified.
macOS-only (needs `sandbox-exec`); skips cleanly elsewhere.
"""
import shutil

from execution.autonomy import DecisionStats
from execution.live import build_air_gapped_executor
from execution.pipeline import decide
from execution.sandbox_client import AirGappedSandboxClient

_HAVE_SANDBOX = shutil.which("sandbox-exec") is not None


def test_env_filter_is_verified_true() -> None:
    assert AirGappedSandboxClient().env_filter_verified() is True


def test_disk_usage_runs_live_under_sandbox() -> None:
    if not _HAVE_SANDBOX:
        print("SKIP: sandbox-exec unavailable"); return
    lines: list[str] = []
    fed: list[tuple[object, bool]] = []
    executor = build_air_gapped_executor(sink=lines.append,
                                         on_feedback=lambda op, ok: fed.append((op, ok)))
    proposal = decide("run_saved_command", "disk_usage", DecisionStats(0.95, 0.97, 30, 12))
    assert proposal.autonomous  # clears the autonomy floors
    executor.execute(proposal)  # REAL: sandbox-exec -f air_gapped.sb df -h
    assert any("[EXECUTED]" in ln and "success=True" in ln for ln in lines), lines
    assert fed and fed[0][1] is True  # success fed back for the reinforce/erode cycle


def test_non_allowlisted_op_never_runs_live() -> None:
    # open_app is autonomous here but NOT on the live allowlist => Suggestion-Only, never executed.
    lines: list[str] = []
    executor = build_air_gapped_executor(sink=lines.append)
    proposal = decide("open_app", "slack", DecisionStats(0.99, 1.0, 50, 30))
    executor.execute(proposal)
    assert any("[SUGGEST]" in ln for ln in lines)
    assert not any("[EXECUTED]" in ln for ln in lines)


def test_jarvis_act_routes_to_live_executor() -> None:
    if not _HAVE_SANDBOX:
        print("SKIP: sandbox-exec unavailable"); return
    from jarvis import Jarvis
    lines: list[str] = []
    executor = build_air_gapped_executor(sink=lines.append)
    # act() routes decide->executor; learn/ask deps are unused for this path.
    jarvis = Jarvis(translator=None, store=None, brain=None, executor=executor)  # type: ignore[arg-type]
    proposal = jarvis.act("run_saved_command", "disk_usage", DecisionStats(0.95, 0.97, 30, 12))
    assert proposal is not None and proposal.autonomous
    assert any("[EXECUTED]" in ln and "success=True" in ln for ln in lines), lines


if __name__ == "__main__":
    test_env_filter_is_verified_true()
    test_disk_usage_runs_live_under_sandbox()
    test_non_allowlisted_op_never_runs_live()
    test_jarvis_act_routes_to_live_executor()
    print("execution/test_live_airgapped: OK")
