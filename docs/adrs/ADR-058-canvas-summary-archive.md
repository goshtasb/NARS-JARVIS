# ADR-058: The Canvas Summary Archive (durable text in the daemon, PDF in the client)

## Status
**Accepted** — implemented. New module `summaries/`, a runner archive hook, two read-only socket
commands, and a fourth Canvas tab. Python suite extended (archive store + the `on_summary` hook);
the full Swift UI type-checks.

## Context
A *briefed* document summary (the Canvas/overnight `summarize_file` path, ADR-052) lands **only** as
the `result` text on its `overnight_queue` row, shown transiently in the **Activity** tab's "Done"
card. There is no durable archive: once the row is purged via **Clear completed** (ADR-033) the
summary is gone, and there is no file the user can open and read later. The same heavy work — minutes
of Map-Reduce over the local 7B — produces a result that evaporates. The user asked for a dedicated
**Summary** tab where every briefed summary is archived as an openable PDF.

The honest constraint: an overnight summary completes **even while the macOS app is closed** (the
daemon drives the runner). So the archive's source of truth **cannot** live in the UI, and PDF
generation cannot be required at completion time — the client may not be running.

Scope is deliberately narrowed (user's call): **only briefed/Canvas summaries** are archived.
Interactive Chat summaries (`file_summarize` → `file_result`, an ephemeral in-conversation reply)
are **not** — they belong to the conversation, not the archive.

## Decision
Split durability from rendering along the existing daemon/client seam:

### 1. The durable archive is **text, owned by the daemon** — new module `summaries/`
A new cohesive module ([`src/summaries/`](../../src/summaries/)) mirrors `overnight/` and `habits/`:
a schema constant + a thin `SummaryArchive` over one `summaries` table on the shared `jarvis.db`
(`source_name, source_path, text, created_at`), with `add` / `list` (newest-first, body omitted) /
`get` / `has`. Append-only. Public interface via `__init__.py` + `__all__` (S-01). Because it is on
disk in the daemon, the archive survives both the app closing and a daemon restart.

### 2. The runner appends via an **`on_summary` hook** — no new coupling
[`OvernightRunner`](../../src/service/overnight_runner.py) takes an optional
`on_summary(source_path, text)` callback (dependency inversion — the runner stays ignorant of the
archive). When a `summarize_file` job completes (`tag == "result"`), it fires the hook right after
marking the row `done`. Since `summarize_file` is the sole `_OFFLOAD` action, this fires for exactly
the briefed-summary scope and nothing else. [`Session`](../../src/service/session.py) constructs
`SummaryArchive(db_path)` and passes `on_summary=…add(basename(path), path, text)`. A one-time,
idempotent **backfill** seeds the archive from any pre-existing `done` `summarize_file` rows so the
tab isn't empty on first launch (guarded by `has`).

### 3. The client **materializes a PDF** — native, dependency-free
Two read-only commands — `summary_list` and `summary_get {id}` — expose the archive. The Canvas gains
a fourth tab, **Summary**, listing each record (name · date · size) with an **Open PDF** button.
[`SummaryPDF`](../../src/ui/SummaryPDF.swift) renders the text into a paginated US-Letter PDF via
native CoreText and saves it to **`~/Documents/JARVIS Summaries/<name>-<id>.pdf`** — a real,
Finder-visible file. It is rendered **once per summary** (cached by id) and re-opened thereafter via
`NSWorkspace`. No Python PDF dependency (respects dependency-minimization); PDF rendering lives where
it is native (AppKit).

The Summary tab reuses the ADR-fix signature-skip so the 1 Hz Canvas poll never tears down its
Open-PDF buttons mid-click (the same defect fixed for the Activity tab's recovery buttons).

## Consequences
- Briefed summaries are now **permanent and portable** — real PDFs the user can open, read, and back
  up, independent of the app's transient task list.
- The privacy posture is unchanged: the text never left the Mac to be summarized (ADR-052), and the
  archive + PDFs are local files. No new network surface.
- One new small module + one callback + two read-only commands + one Swift file. No change to the
  summary engine, the queue, or the held-ledger.
- **Scope honesty:** Chat summaries are not archived. If that becomes desired, the same `on_summary`
  shape can be added to the chat `file_result` path — a deliberate, separate decision.

## Verification
- `PYTHONPATH=src python3 -m pytest summaries/test_store.py service/test_summary_offload.py` — archive
  add/list/get + restart-survival, and the `on_summary` hook firing exactly once with `(path, text)`
  on completion (silent on progress/eof).
- End-to-end (manual, needs an app build): brief a document → it appears in the **Summary** tab →
  **Open PDF** opens a real PDF from `~/Documents/JARVIS Summaries/`, also visible in Finder.

## Related
[ADR-052](./ADR-052-document-summary-offload-engine.md) (produces the summaries),
[ADR-053](./ADR-053-unified-canvas-ui.md) (the Canvas tabs),
[ADR-031](./ADR-031-overnight-batch-queue.md) (the queue that briefs them).
