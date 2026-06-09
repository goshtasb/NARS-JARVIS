# ADR-025: File search (`find_file`) — JARVIS sees the filesystem

## Status
Accepted & live-verified. First filesystem capability: read-only Spotlight search, with human-context
ranking (blacklist noise + boost user folders). Suite 363 → **371**. Tagged **v1.1.0**.

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
- **Signal > noise (implemented):** the OS does the index lookup (one spawn, full recall); Python
  applies context `mdfind` can't — a HARD blacklist of dev/system/cache paths (`node_modules`, `.git`,
  `Library`, caches, `/usr`, `/private`, …) and a SOFT rank boosting `~/Desktop|Documents|Downloads`
  and shallower paths. Live: "resume" went from 5 `node_modules` icons → an honest "only system/cache
  files match"; "Jarvis" → 3 clean user files. `-onlyin` was rejected (one dir per spawn + kills
  recall); whitelist is a boost, not a cage.
- **Honest limits:**
  - **Name-only** (`-name`); content search (`mdfind "<text>"`) and richer filters are future rows.
  - **Aggressive blacklist** — a file genuinely living under `/usr`/`/opt` won't surface (acceptable
    for "find my file"; rare for a normal user).
  - **Read-only by design** — create/move/delete files are separate, more-sensitive capabilities (the
    latter two would be GATED), deliberately not in this ADR.
- **STT note:** "find"→"define" was a Whisper error; orthogonal to this ADR but worth logging as a
  voice-accuracy limit.

## Alternatives Considered
- **Recursive `find`:** rejected — slow, CPU/disk-thrashing; Spotlight's index is the right tool.
- **Auto-reveal the top hit in Finder:** rejected — unsolicited GUI takeover; data-in/data-out keeps
  the assistant unobtrusive and lets the user decide the next step.
