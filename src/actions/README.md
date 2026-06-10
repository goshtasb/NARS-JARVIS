# actions

## Overview
The closed set of things JARVIS can *do* on the Mac (ADR-019). The LLM proposes an action with a
`[[DO: <action>]]` directive; the **catalog** validates it (an unknown name or unsafe argument never
runs) and the **run** shell executes it through the sanctioned `safespawn` seam (ADR-015). There is no
generative execution path — only enumerated actions can run. "Model proposes, code disposes."

## Usage
```python
from actions import ActionRunner
runner = ActionRunner(llm=local_llm)        # llm optional; needed only for kind="work" (ADR-032)
runner.perform("find_file", "spec")          # read-only -> result string
runner.propose("empty_trash")                # destructive -> (None, ConsentSpec) for the consent gate
```

## Key Components
- **`catalog.py`** — the frozen `Action` registry. `kind ∈ {diag, query, work, argv, nav, ax, agent,
  habit}`; `confirm` marks destructive actions. `resolve`/`available`/`argv_for`/`render_action_prompt`.
- **`run.py`** — `perform(name, arg, *, spawn, llm)` (the only place an action reaches the OS) +
  `ActionRunner` (injected into `Jarvis`) + `ConsentSpec` (a destructive action's deferred thunk, ADR-020).
- **`diagnostics.py`** — `system_report` / `anomaly_flags` (kind="diag", read-only psutil).
- **`files.py`** — `find_file` (kind="query", read-only Spotlight search; ADR-025).
- **`documents.py`** — read-only document **work primitives** (ADR-032, kind="work"): `read_file_text`
  (text-family + PDF via lazy `pypdf`), pure `chunk_text`, and `summarize` (whole-document **Map-Reduce**,
  coverage-honest — never a silent truncation). Outputs go only to a `/tmp` scratchpad.
- **`recipes.py`** — declarative self-navigation recipes (kind="nav"; ADR-022/023).

## Dependencies
`safespawn` (the subprocess seam). `documents.py` lazily imports `pypdf` for PDF text (see
`requirements.txt`). The LLM handle for `summarize_file` is injected by the daemon; absent → an honest
"no model" message. No network.

## Related ADRs
[ADR-019](../../docs/adrs/ADR-019-mac-actions.md) (the action model),
[ADR-025](../../docs/adrs/ADR-025-file-search.md) (find_file),
[ADR-032](../../docs/adrs/ADR-032-work-primitives.md) (the work primitives).
