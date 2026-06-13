# summaries

## Overview
The ADR-058 **summary archive**: a durable, append-only record of every *briefed* document summary
(the Canvas/overnight `summarize_file` path). The text is owned by the daemon so it survives the
macOS app being closed mid-run; the Swift client materializes each record into an openable PDF under
`~/Documents/JARVIS Summaries/`. Only briefed summaries are archived — interactive Chat summaries are
not.

## Usage
```python
from summaries import SummaryArchive

a = SummaryArchive("jarvis.db")
sid = a.add("Q3-PRD.pdf", "/Users/me/Q3-PRD.pdf", "the summary body…")
a.list()      # [{id, source_name, created_at, chars}]  — newest first, no body
a.get(sid)    # {id, source_name, source_path, text, created_at}
```
The `OvernightRunner` appends here via its `on_summary` callback when a `summarize_file` task
completes; clients read it through the `summary_list` / `summary_get` socket commands (see
`service/README.md`).

## Key Components
- **`store.py`** — `SummaryArchive` over one `summaries` table on the shared `jarvis.db`. `add` is
  append-only; `list` omits the body (name/date/size only); `get` returns the full record; `has`
  guards the one-time backfill against duplicates. New table, so `CREATE TABLE IF NOT EXISTS` is the
  whole migration story.

## Dependencies
`dbconn` (shared sqlite connection); stdlib `sqlite3`. No network.

## Related ADRs
[ADR-058](../../docs/adrs/ADR-058-canvas-summary-archive.md) (this module),
[ADR-052](../../docs/adrs/ADR-052-document-summary-offload-engine.md) (the offload engine that
produces the summaries),
[ADR-031](../../docs/adrs/ADR-031-overnight-batch-queue.md) (the queue that briefs them).
