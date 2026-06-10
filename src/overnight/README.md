# overnight

## Overview
The ADR-031 **overnight batch engine**: queue concrete catalog actions before sleep; a tick-driven
runner executes the **read-only** ones unattended and **holds** everything else (state changes, GUI
actuation, destructive ops) in a durable ledger for explicit morning approval. The whole point is a
*mechanism* for deferred work that is safe by construction — not new autonomy. Durable across daemon
restarts, unlike the in-memory ADR-020 consent ledger.

## Usage
```python
from overnight import OvernightQueue, HeldLedger, safe_autonomous
from actions import resolve

q = OvernightQueue("jarvis.db")
q.enqueue("find_file", "spec")        # read-only -> will run unattended
q.enqueue("empty_trash")              # destructive -> will be held

safe_autonomous(resolve("find_file"))   # True  (kind="query")
safe_autonomous(resolve("empty_trash")) # False (argv + confirm)
```
The daemon drives the runner from `session.tick()`; clients use the `overnight_*` / `briefing`
socket commands (see `service/README.md`). The `OvernightRunner` itself lives in `service/` (Imperative
Shell) since it orchestrates the `ActionRunner`.

## Key Components
- **`classify.py`** — pure `safe_autonomous(action)`: the single read-only safety boundary
  (`kind in {"diag","query"}`, non-confirm). Unknown/other → held (default-deny).
- **`store.py`** — `OvernightQueue` (incoming tasks: pending→running→done/held/failed, with
  `reset_running()` restart-safety and `purge_done()` for the ADR-033 Clear-Completed flush) and
  `HeldLedger` (outgoing actions awaiting approval: held→approved/denied). Both on the shared
  `jarvis.db`; new tables, so `CREATE TABLE IF NOT EXISTS` is the whole migration story.

The autonomous vocabulary is `safe_autonomous` kinds: `diag`, `query`, and `work` — the last being the
ADR-032 read-only document primitives (`read_file`, `summarize_file`), which is what gives an overnight
run something genuinely useful to do.

## Dependencies
`actions` (the closed catalog, for `Action.kind` + `resolve`); stdlib `sqlite3`. No network.

## Related ADRs
[ADR-031](../../docs/adrs/ADR-031-overnight-batch-queue.md) (this module),
[ADR-020](../../docs/adrs/ADR-020-unified-consent.md) (the consent model it complements),
[ADR-019](../../docs/adrs/ADR-019-mac-actions.md) (the action catalog it classifies).
