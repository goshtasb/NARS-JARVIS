# ADR-025: File search (`find_file`) — JARVIS sees the filesystem

## Status
Accepted & live-verified. First filesystem capability: read-only Spotlight search. Suite 363 → **368**.
Post-v1.0.0 feature (candidate v1.1.0).

## Context
A live request — "**find** a file called Jarvis" (Whisper mis-transcribed it as "define", but the root
cause was deeper) — exposed that JARVIS had **zero filesystem capability**: it couldn't find *or* create
files. The action catalog was entirely system/settings-focused. For an assistant, "where is my file?"
is table-stakes.

## Decision
Add a read-only **`find_file`** action backed by macOS Spotlight.
- **`actions/files.py`** `find_file(query, spawn, limit=5)`: `safespawn.run(["mdfind", "-name", query])`
  — the query is a single argv element (no shell, no injection); Spotlight is O(1)-fast (no recursive
  disk walk). Returns a short human-readable list.
- **Hard cap (token-budget guard):** return the top **5** paths and append "…and N more" — a generic
  query ("resume") returns hundreds of hits that would overflow the prompt; live test showed 215 →
  5 + "…and 210 more."
- **Catalog:** `Action("find_file", …, kind="query", takes_arg=True)` — read-only, mutates nothing →
  **FRICTIONLESS** (no consent), listed in the prompt. `run.perform` routes `kind="query"` to `find_file`.
- **No GUI interruption:** lists paths only — never pops a Finder window. If the user then says "open
  it," the existing `open_app`/`open_url` routing handles that as a separate, explicit step.

## Consequences
- **Gained:** JARVIS can locate files by name on request. The everyday-usefulness gap starts closing.
- **Tests:** +5 (`actions/test_files.py` — argv shape, top-N cap + remainder note, no-match, empty-query
  no-spawn; all via injected fake spawn → no real OS) + a catalog assertion. Suite **368** green.
  Live-verified end-to-end through the 7B.
- **Honest limits:**
  - **Spotlight returns noise** — `node_modules`, caches, system paths all match. v1 returns raw top-5
    by Spotlight's order; ranking/filtering (prefer ~/Desktop, ~/Documents; drop `node_modules`) is a
    clean follow-on.
  - **Name-only** (`-name`); content search (`mdfind "<text>"`) and richer filters are future rows.
  - **Read-only by design** — create/move/delete files are separate, more-sensitive capabilities (the
    latter two would be GATED), deliberately not in this ADR.
- **STT note:** "find"→"define" was a Whisper error; orthogonal to this ADR but worth logging as a
  voice-accuracy limit.

## Alternatives Considered
- **Recursive `find`:** rejected — slow, CPU/disk-thrashing; Spotlight's index is the right tool.
- **Auto-reveal the top hit in Finder:** rejected — unsolicited GUI takeover; data-in/data-out keeps
  the assistant unobtrusive and lets the user decide the next step.
