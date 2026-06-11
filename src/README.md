# src — module index

Modular documentation lives **next to the code it describes** (S-03): every package below has its own
`README.md` with overview, usage, key components, and related ADRs. This file is only the map — no
rules are duplicated here (Referral Over Repetition).

| Package | One-liner | Docs |
|---|---|---|
| `brain/` | ONA (C reasoner) wrapper: beliefs, questions, truth values | [README](brain/README.md) |
| `language/` | LLM channel: translation, grounding, generation, voice | [README](language/README.md) |
| `memory/` | Durable SQLite system-of-record + grounding store | [README](memory/README.md) |
| `contradiction/` | Pre-commit conflicting-fact guard | [README](contradiction/README.md) |
| `consent/` | Pure consent ledger (Approve/Deny data model) | [README](consent/README.md) |
| `actions/` | Closed action catalog + the one OS seam + web egress | [README](actions/README.md) |
| `research/` | Bounded agentic web-research loop (ADR-039/042) | [README](research/README.md) |
| `habits/` | Habit quantization + durable habit store | [README](habits/README.md) |
| `overnight/` | Overnight queue, held ledger, read-only classifier | [README](overnight/README.md) |
| `sentinel/` | Machine-watching second brain (surprise detection) | [README](sentinel/README.md) |
| `execution/` | Air-gapped sandboxed execution tier | [README](execution/README.md) |
| `context/` | Prompt context blocks: live state, habits, chat history | [README](context/README.md) |
| `persona/` | Closed-vocabulary persona learning (Cognitive Identity) | [README](persona/README.md) |
| `service/` | The daemon: select() server, dispatch plane, the loops | [README](service/README.md) |
| `shared/` | Cross-cutting utilities | [README](shared/README.md) |
| `ui/` | Swift/AppKit menu-bar app (the "body") | [README](ui/README.md) |

## Root-level files (not packages)
- **`jarvis.py`** — the conversational orchestrator: assembles the converse prompt (memory + live
  context + habits + persona + conversation history), parses `[[DO:]]`/`[[REMEMBER]]` directives,
  applies the deterministic intent gates (system/audio/browser, v1.8.2 + ADR-040/042), and hands
  research directives to `research/`. Deliberately a coordinator — capability lives in the modules.
- **`console.py`** — the terminal client (same socket protocol as the menu-bar app).
- **`safespawn.py`** — the single sanctioned subprocess seam (argv-only, env-scrubbed; ADR-015).
- **`test_converse.py`** — end-to-end conversational tests (directive parsing, gating, research wiring,
  conversation history) — the orchestrator's contract.

Engineering rules: [`../standards/00-manifest.md`](../standards/00-manifest.md) (binding) ·
Architecture history: [`../docs/adrs/`](../docs/adrs/) · Project intro: [`../README.md`](../README.md).
