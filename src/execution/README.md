# execution

## Overview
M3 **Phase A — the architecture of constraint** (C4). Codifies ADR-002: a closed, typed operation
catalog; the autonomy-eligibility predicate; and a `MockExecutor`. **No live execution** — the
OmniGlass binding is held behind a passed adversarial sandbox audit (PRD M3 prerequisite).

## The closed catalog (`catalog.py`)
Operations are `OpName` enum members; each takes one argument from a bound enum (`AppEnum`,
`SavedCommandEnum`). The only string → action path is `parse_operation()`, which validates against
the enums. Anything not registered raises `UnregisteredOperationError` and logs a **severe
security violation** (`execution.security` logger). There is no `^run_shell(string)` and no path to
one — the catalog is finite, human-authored, and closed.

## The autonomy predicate (`autonomy.py`)
Overrides ONA's permissive game-agent defaults: **`MOTOR_BABBLING_CHANCE = 0.0`** (applied to ONA
via `*motorbabbling=0.0` at wiring time) plus strict floors — `min_confidence`, `min_frequency`,
`min_observations`, `min_confirmations`. `is_autonomous()` is a strict AND; **False ⇒ Suggestion-Only
Mode** (the proposal is surfaced to the terminal for human approval).

## The mock executor (`executor.py`, `pipeline.py`)
`decide(op_name, arg_name, stats)` validates + evaluates → a `Proposal`. `MockExecutor.execute()`
prints `[EXECUTE PROPOSAL]: open_app(AppEnum.SLACK) - Autonomy: False (Awaiting User)` and feeds a
simulated success/failure back via `on_feedback`, driving ONA's reinforce/erode habit cycle.

## Phase B — gated, with the crucible constraints enforced in code (`omniglass.py`)
The local sandbox crucible (2026-06-05, **CONDITIONAL PASS** — see
[`docs/audits/omniglass-v1.0.0-beta-local-RESULTS-2026-06-05.md`](../../docs/audits/omniglass-v1.0.0-beta-local-RESULTS-2026-06-05.md))
mapped two gaps that `OmniGlassExecutor` now enforces structurally, independent of config/autonomy:
1. **Network egress = human-only.** `catalog.requires_network(op)` (default-deny) gates any
   network-requiring operation to human confirmation; it can never reach the live seam.
   Rationale: `sandbox-exec` cannot do domain-level egress filtering, so a network grant is an
   arbitrary-IP exfiltration vector.
2. **Env-filter must be verified.** The live seam is refused unless `client.env_filter_verified()`
   is True — secret-env protection is the `env_filter` layer, not the sandbox profile.

## Live, air-gapped (`disk_usage` only) — `sandbox_client.py`, `live.py`
`AirGappedSandboxClient` runs a **fixed argv tuple** under `sandbox-exec` with the
[`profiles/air_gapped.sb`](profiles/air_gapped.sb) profile (a strict subset of the audited profile:
deny-default, `/Users` walled off, **no network, no shell**, only `/bin/df` may exec) and a filtered
env. `build_air_gapped_executor()` is the **single** place `authorized=True` is set, with a hardcoded
`LIVE_OPERATIONS = {disk_usage}` allowlist — the one operation the crucible proved runs under the
sandbox with every attack still denied. `Jarvis.act(op, arg, stats)` routes a proposal through
`decide()` to the executor. Proof: `test_live_airgapped.py` runs real `df -h` under the sandbox.

**Still human-only (by design):** `OPEN_APP` (the audited profile blocks `open -a` — OmniGlass
issue #13), any future network operation (issue #12), and any op below the autonomy floors.
`authorized=True` for anything beyond `disk_usage` remains a dated human decision behind a new audit.

## Tests
From `src/`: `python3 -m execution.test_catalog | test_autonomy | test_executor`. They prove an
unregistered operation is violently rejected (+ logged), a low-confidence coincidence is trapped in
Suggestion Mode, and feedback reinforces then erodes a habit in real ONA.

## Related
ADR-002 (execution safety + trigger soundness); PRD C4 / M3 (prerequisites: sandbox audit).
