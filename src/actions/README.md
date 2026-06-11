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
- **`web.py`** — read-only **web egress** (ADR-034/039, kind="query"): `web_lookup` (keyless DuckDuckGo
  search), `read_article` (readability-lxml main-text extraction), and `browse_page` (page text + its
  numbered links — the research loop's primitive; `internal=True`, never offered to the chat model).
  Runs as an isolated subprocess (the daemon stays network-free); SSRF guard, bounded read, fail-closed
  `[ERROR…]`, TLS via the OS Keychain (truststore). `read`/`browse` escalate from the static GET to the
  rendered fetch when extraction comes back thin; data-dense pages fall back from article extraction to
  whole-page text. Pure parsers (`parse_ddg`/`extract_article`/`extract_links`/`page_text`) are
  unit-tested offline.
- **`web_render.py`** — the **rendered (JS) escalation tier** (ADR-039): a transient headless Chromium
  via Playwright, launched only when static extraction yields nothing (JS-rendered data sites), dead
  seconds later. Lazy import — everything else works without Playwright installed.
- **`recipes.py`** — declarative self-navigation recipes (kind="nav"; ADR-022/023).

## Dependencies
`safespawn` (the subprocess seam). `documents.py` lazily imports `pypdf` for PDF text; `web.py` uses
stdlib `urllib` + `readability-lxml`/`beautifulsoup4` + `truststore`; `web_render.py` lazily imports
`playwright` (see `requirements.txt`). The LLM handle for `summarize_file` is injected by the daemon;
absent → an honest "no model" message. The only network egress is `web.py`'s read-only fetch
(ADR-034/039), in an isolated subprocess.

## Related ADRs
[ADR-019](../../docs/adrs/ADR-019-mac-actions.md) (the action model),
[ADR-040](../../docs/adrs/ADR-040-sensor-actuator-parity.md) (sensor–actuator parity: every actuator
family ships a read-only status sensor with its own intent gate; reports name their measured scope),
[ADR-044](../../docs/adrs/ADR-044-ax-actuation-intent-gate.md) (GUI actuation gated on UI-action intent —
no phantom clicks on chat turns),
[ADR-045](../../docs/adrs/ADR-045-report-verdict-on-health-intent.md) (the system report's "all clear"
verdict only on health questions, never on neutral data questions),
[ADR-046](../../docs/adrs/ADR-046-network-inspection-sensor.md) (the read-only network sensor —
per-process bandwidth + connections + Wi-Fi, intent-gated, no egress),
[ADR-047](../../docs/adrs/ADR-047-largest-apps-and-unified-inspection-decision.md) (largest_apps +
the decision to unify all read-only sensors into one inspect_system tool — read anything, mutate nothing),
[ADR-025](../../docs/adrs/ADR-025-file-search.md) (find_file),
[ADR-032](../../docs/adrs/ADR-032-work-primitives.md) (the work primitives),
[ADR-034](../../docs/adrs/ADR-034-web-search.md) (web search/read),
[ADR-039](../../docs/adrs/ADR-039-agentic-web-research.md) (agentic research + rendered fetch).
