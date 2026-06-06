# memory

## Overview
The durable, pinnable **system-of-record (L2)** and the L1↔L2 sync (PRD §6). ONA (the L1
reasoning cache) aggressively self-forgets to protect its 40-slot buffer; this SQLite store is
the permanent safety net. Core user facts are **pinned** — immune to pruning and decay.

## Sync model (no eviction callback — ONA evicts silently)
- **write-through** — `store.upsert(...)` at ingestion persists the original truth *before* ONA
  could evict it, so eviction never loses it.
- **observe** — `observe(store, lines)` parses ONA's `Revised:`/`Derived:` output and upserts
  updated truth as reasoning happens.
- **snapshot** — `reconcile(store, concepts_lines)` parses ONA's `*concepts` dump to refresh
  usage + truth (eventually consistent for intermediate revisions — accepted boundary).
- **cache-miss reload** — `reload_into_brain(store, brain)` repopulates a (possibly fresh) L1
  from L2, pinned facts first.

## Schema (`facts`)
`narsese` (UNIQUE key) · `english` · `frequency`/`confidence` (truth) · `embedding` (BLOB) ·
`pinned`/`priority_tier` (eviction immunity) · `use_count` · `created_at`/`updated_at`/`last_used`.

## Usage
```python
from memory import MemoryStore, reload_into_brain
store = MemoryStore("jarvis.db")
store.upsert("<self --> [allergic_penicillin]>", 1.0, 0.9, english="I am allergic to penicillin")
store.pin("<self --> [allergic_penicillin]>")        # never evicted / decayed
reload_into_brain(store, brain)                      # cache-miss: L2 -> L1
```

## Tests
From `src/`: `python3 -m memory.test_store` and `python3 -m memory.test_sync`
(the latter drives a real ONA via the brain wrapper for the cache-miss reload).

## Related
ADR-001 (module boundaries); PRD §6 (two-tier memory), R5 (pinning core facts).
