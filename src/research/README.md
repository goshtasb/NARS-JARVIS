# research

## Overview
The bounded agentic web-research loop (ADR-039). When the chat model proposes `web_lookup` /
`read_article`, this module gives it the human move search snippets can't: *pick the relevant result,
open it, read the rendered page, follow a link deeper* — then synthesize an answer with sources.
Replaces ADR-035's single search→synthesize pass (which structurally could not answer live-data
questions: search engines return descriptions of pages, not their contents).

## Usage
```python
from research import run_research

answer, errors = run_research(
    question,                  # the user's question
    [("web_lookup", "...")],   # the model's seed [[DO:]] research directives
    generate,                  # (system, user, max_tokens) -> str   — the daemon's LLM
    perform,                   # (action, arg) -> str                — ActionRunner.perform
)
```
`answer` is the synthesized, source-naming reply (or `None` when every fetch failed — `errors` then
carries the honest `[ERROR: …]` strings). Never raises.

## Key Components
- `agent.run_research` — the loop: seed → decide (`OPEN <n>` / `SEARCH <q>` / `ANSWER`) → fetch via
  `browse_page` → merge that page's links into the menu → repeat → synthesize. Hard bounds:
  ≤3 opens, ≤2 searches, ≤8 steps, 120 s wall clock.
- `agent.parse_step` / `links_from_results` / `split_browse` — pure parsers (offline-tested).

## Safety model
The model **never types a URL** — it selects an index into a menu deterministic code extracted from
pages we already fetched (each fetch read-only + SSRF-guarded in the isolated egress subprocess,
`actions/web.py`). Hostile page text can at most nudge which *existing* link is opened next; it cannot
mint a URL, so it cannot encode data for exfiltration. An unparseable decision ends the loop — the
model can never free-run.

## Dependencies
None at import time. Both effects (`generate`, `perform`) are injected — the module is fully testable
with fakes (no model, no network).

## Related ADRs
ADR-039 (this loop), ADR-034 (egress hardening it rides on), ADR-035 (the pass it supersedes).
