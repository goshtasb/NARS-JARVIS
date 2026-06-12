# ADR-054: Hybrid Natural-Language Intent Router (+ `/` deterministic override)

## Status
**Accepted** (design ratified). Implementation in progress: the **headless core landed** (grammar
compiler + Interception Gate + `generate_json` seam, suite 536→547, live-verified against the real 7B).
**Remaining:** the daemon socket command that runs the pipeline + emits the intent to the client, the
client-side timing→epoch resolution + Canvas projection, and the `/` AppKit popover. Behind the Mirror
validation hold — no live wiring until that opens / is greenlit.

## Context
The Canvas (ADR-053) is a deterministic state machine, but reaching it still costs mechanical friction:
pick a verb from the palette, browse for a file, choose a schedule preset. Natural language removes that
friction — **but invisible NL execution is an anti-pattern**: a mis-heard word or mis-routed primitive
corrupts state with zero user awareness. The resolution is a router that **drives** the Canvas rather
than replacing it: NL → structured payload → projected as a live Canvas row the user sees and can correct.

A `/` typeahead (Cursor-style) was also proposed. It is **not** a competitor to NL — it is a
deterministic override of the router's most error-prone step (verb selection) plus a discoverability
surface over the closed catalog. It is explicitly **not** a context/file picker: that would require
indexing the user's files/tabs, violating the content-blind privacy invariant. `/` lists **verbs**;
natural language fills the arg + timing on the same line (`/summarize_file the PRD on my desktop tonight`).

## Decision — the enforcement pipeline (ratified)

| Layer | Mechanism | Failure mode prevented |
|---|---|---|
| **GBNF grammar** | Dynamically compiled `enum(catalog_schema ∪ {"none"})` + structural JSON tokens | Malformed JSON, trailing commas, **action-name hallucination** |
| **Interception Gate** | Daemon-side semantic validation (required args, bounded timing, capture `none`) | Incomplete params, out-of-scope, type mismatch |
| **Canvas Projection** | Reactive Canvas row (Now / Scheduled) before execution | Silent background runs, invisible mis-routing |
| **Change tool ▾ / editable arg** | Sibling-tool dropdown (shipped) + editable path on a not-found failure | Routing errors on valid args; **path typos** |

### Token-level determinism (not prompt-and-pray)
Prompt engineering alone is a house of cards on a quantized 7B. We compile a GBNF grammar per request
([`actions/intent.build_intent_grammar`](../../src/actions/intent.py)) and pass it through the proven
`create_chat_completion(grammar=…)` path ([`LocalLLM.generate_json`](../../src/language/llm.py)):
- **`action`** is an alternation of the live catalog names **plus a `"none"` sentinel** — the escape
  hatch. Without it a closed enum is a *forcing function for hallucination* (the model would map
  out-of-scope requests onto the nearest valid verb). With it, the model declines and the gate clarifies.
- **`arg`** uses the standard JSON body class `[^"\\]`, so paths and URLs (`/ . : ~ @ ? = &`) are
  permitted by construction — no explicit allowlist to get wrong.
- **`timing`** is a *relative classification* — `null` | `{kind: now|in_minutes|at_clock_hour, value}`
  — **never an absolute epoch**. The model does no arithmetic and never sees the timezone; the **client**
  resolves it to an absolute epoch against local time, preserving the timezone-free daemon contract
  (ADR-053). "11pm" → `{at_clock_hour, 23}`, not a doomed timestamp.

### The gate does NOT touch the filesystem (the existence-check decision)
A well-formed but nonexistent path **passes** to the Canvas as PENDING; it is not rejected at the gate.
Reasons: (1) **TOCTOU + scheduling** — a scheduled task's target may legitimately not exist yet, and the
only race-free check is at execution; (2) the **daemon's FS view ≠ the user's** (TCC/sandbox/unmounted
volumes); (3) keeping the gate **pure** (no I/O) keeps it deterministic and headless-testable. A missing
path then fails as a *visible, durable FAILED row* (`⚠ No such file`), recovered by an **editable-path**
affordance (the verb-swap dropdown and plain Retry don't fix a typo). The gate rejects only the
structurally doomed: missing arg, `none`, malformed/out-of-range timing.

## Validation (live, real 7B, this machine)
Four NL prompts through the compiled grammar + gate:
```
"summarize my PRD at /Users/me/PRD.pdf"        → {summarize_file, "/Users/me/PRD.pdf", null}        ACCEPT
"read this article …tonight at 11pm"            → {read_article, url, {at_clock_hour, 23}}            ACCEPT
"what's my cpu and memory"                      → {report_system, "", null}                           ACCEPT
"order me a …pizza"                             → {none, …}                                           CLARIFY
```
Valid JSON every time, paths/URLs intact, "11pm"→`at_clock_hour 23` (no arithmetic), `none` fired for
out-of-scope. 11 new unit tests cover the grammar compiler and every gate branch.

## Consequences
- Zero-friction NL activation with the rigid, trustworthy Canvas state machine and recovery tools intact.
- The router runs on the **foreground converse pipeline** — interactive, never burdening the ADR-052
  offload worker, so active summaries / sensor logging keep flowing.
- `/`'s value scales inversely with router accuracy: it is a precision-and-discovery layer, sequenced
  **after** we measure the bare router's verb-routing error rate against `action_alternatives`
  corrections — if the router rarely mis-routes, `/` is polish, not a must.
