"""Air-gapped live wiring (M3 Phase B). The SINGLE place `authorized=True` is set for the engine.

Encodes the dated human authorization from the 2026-06-05 sandbox crucible (CONDITIONAL PASS):
authorize LIVE autonomous execution for the `disk_usage` saved command ONLY — the one operation
proven to run under the audited air-gapped profile while every attack vector stayed denied. The
operation is air-gapped (no network), runs under `sandbox-exec`, and the client's env-filter is
verified before any spawn. Every other operation remains Suggestion-Only.

See docs/audits/omniglass-v1.0.0-beta-local-RESULTS-2026-06-05.md and OmniGlass issues #12, #13.
"""
from __future__ import annotations

from typing import Callable

from .catalog import Operation, OpName, SavedCommandEnum
from .omniglass import OmniGlassExecutor
from .sandbox_client import AirGappedSandboxClient

# The ONLY operation cleared for live autonomous execution (df -h). Hardcoded, closed allowlist.
DISK_USAGE = Operation(OpName.RUN_SAVED_COMMAND, SavedCommandEnum.DISK_USAGE)
LIVE_OPERATIONS: frozenset[Operation] = frozenset({DISK_USAGE})


def build_air_gapped_executor(
    sink: Callable[[str], object] = print,
    on_feedback: Callable[[Operation, bool], object] | None = None,
) -> OmniGlassExecutor:
    """Construct the authorized, air-gapped OmniGlass executor (disk_usage only, sandboxed)."""
    return OmniGlassExecutor(
        client=AirGappedSandboxClient(),
        authorized=True,                 # dated human sign-off — crucible 2026-06-05 (conditional)
        live_operations=LIVE_OPERATIONS,  # nothing else can ever reach the live engine
        sink=sink,
        on_feedback=on_feedback,
    )
