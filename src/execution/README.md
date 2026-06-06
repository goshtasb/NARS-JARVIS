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

## Tests
From `src/`: `python3 -m execution.test_catalog | test_autonomy | test_executor`. They prove an
unregistered operation is violently rejected (+ logged), a low-confidence coincidence is trapped in
Suggestion Mode, and feedback reinforces then erodes a habit in real ONA.

## Related
ADR-002 (execution safety + trigger soundness); PRD C4 / M3 (prerequisites: sandbox audit).
