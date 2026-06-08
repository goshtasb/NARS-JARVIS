# ADR-011: Sentinel persistence — earned autonomy + baseline survive a restart

## Status
Accepted — completes the Flow Sentinel's durability (ADR-006 autonomy was non-persistent) and
advances ADR-007 open item (c). Mirrors the knowledge brain's L2 durability (PRD §6).

## Context
The Flow Sentinel's ONA beliefs lived only in its in-process `Brain`
([src/service/sentinel_loop.py](src/service/sentinel_loop.py)). `SentinelStore` persisted app→bucket
categories and KPIs but **not the belief truths**. So every daemon restart wiped the earned-autonomy
gate (`<distracted_hide_<cat> --> [approved]>`) and the steadiness baseline — the sentinel had to
re-earn ~6 approvals and re-burn-in from scratch before it could act. (We confirmed earlier that ONA
beliefs were fed in-process only, with no L2 write-back.)

## Decision
**Persist belief *truths* via event-driven write-through, and replay them into a fresh sentinel ONA
on `sentinel on`** — never snapshot ONA's binary memory (it has no save/load; replay is the only
durability path, exactly as `memory.reload_into_brain` does for the knowledge brain).

- **Store:** new `sentinel_beliefs(term PK, frequency, confidence, updated_at)` table in
  `SentinelStore` (a brand-new table → `CREATE TABLE IF NOT EXISTS` is the whole migration; no ALTER).
  `record_belief` (upsert, latest wins) + `beliefs()`.
- **Write-through (low-frequency, crash-safe):** `persist_belief(store, brain, term)` reads the
  current ONA truth and upserts it. Called from `_feed_consent` (per category, after a human y/n →
  the gate authorization) and from `_handle` (after each Schmitt-trigger steadiness observation → the
  baseline). These are discrete, infrequent events (consent is rare; baseline shifts are token-bucket
  rate-limited), so there is **no per-tick write and no SSD thrashing**, and each datum is durable the
  instant it happens — crash-safe without any shutdown-flush or periodic-snapshot machinery.
- **Replay on start:** `_start()` builds the store first, then the fresh brain, then
  `replay_beliefs(store, brain)` (inject every persisted `term. {freq conf}`) **before** the sensor
  streams — so the gate and baseline are restored up front. If burn-in calibration was already
  recorded, the sentinel reports as immediately armed.
- `persist_belief`/`replay_beliefs` are **module functions** (not methods) so they unit-test with a
  real `Brain` + `SentinelStore` without the macOS sensor.

## Consequences
- **Gained:** earned trust + baseline calibration persist across restarts; the sentinel no longer
  re-delays the epistemic gate on every reboot. A concrete step on ADR-007 (c) (sentinel state/learning
  injected/retained).
- **Crash-safety invariant:** a hard crash loses at most the single in-flight event; all prior
  consent/baseline is already on disk.
- **Accepted limitations:** replay restores belief **truth values** (what the gate reads:
  freq/conf), NOT ONA's ephemeral attention/priority buffers — the same trade-off the knowledge brain
  makes. Re-consent after a restart revises from the replayed truth rather than replaying raw events;
  the asymmetric earn-slow/lose-fast gate behavior is preserved. Terms only are stored — never an app
  id, window title, or content (privacy unchanged).

## Alternatives Considered
- **Binary snapshot of ONA memory:** impossible — the NAR shell exposes no save/load.
- **Flush only on clean shutdown:** rejected — loses hours of calibration on a crash/power loss.
- **Debounced periodic snapshot:** rejected — solves a thrashing problem that doesn't exist (events
  are already discrete/low-frequency); adds complexity and a data-loss window vs. write-through.
