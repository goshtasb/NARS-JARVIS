# ADR-039: Agentic web research — bounded link-following + rendered fetch

## Status
Accepted & live-verified. Supersedes ADR-035's single search→synthesize pass and narrows ADR-034's
"no browser" rejection (see Decision §2). Suite 468 → **479** (+1 opt-in live eval skip).

## Context
A live test exposed a structural blind spot: asked "how's the weather today?", JARVIS searched,
received five DuckDuckGo results whose snippets were pure navigational boilerplate (zero temperatures —
search engines return *descriptions of pages*, not their contents), and honestly answered that the
results contain no weather. The anti-hallucination contract held; the answer was still useless. Two
verified root causes:
1. **No second hop.** ADR-035 synthesized from snippets only — the loop couldn't *open* a result the
   way a human clicks one, let alone follow a link within the opened page.
2. **JS-rendered data invisible.** Even reading the result pages failed: weather.com renders its data
   with JavaScript, so the static urllib GET returns chrome without content (verified: readability
   extracted one unrelated editorial sentence); other results rate-limited or timed out.

## Decision
1. **A bounded research loop in a new `research/` domain module** (S-01), effects injected. Seeded by
   the chat model's own `[[DO: web_lookup/read_article]]` directives: search → the model picks a link
   **by number** from a deterministic menu → `browse_page` fetches it (text + that page's links, which
   join the menu) → repeat → synthesize an answer that names its sources. Hard bounds: ≤3 opens,
   ≤2 searches, ≤8 steps, 120 s wall clock; an unparseable decision ends the loop (never free-runs).
   Decision grammar: `OPEN <n>` / `SEARCH <q>` / `ANSWER`, strict line-anchored parse.
2. **A transient rendered-fetch tier** (`actions/web_render.py`): headless Chromium via Playwright,
   used ONLY when static extraction comes back thin (< 600 chars), launched per call and closed
   seconds later, fresh context (no profile/cookies persisted), only ever inside the already-isolated
   egress subprocess. ADR-034 rejected a *resident* browser as the primary fetch on RAM grounds; that
   reasoning stands — this is an on-demand escalation, not a resident engine. Lazy import: without
   Playwright the web layer degrades to the static tier.
3. **`browse_page`: an `internal=True` catalog action** — resolvable by code (the loop), never listed
   in the chat model's prompt. Output = page text + numbered links. Data-dense pages (weather/finance
   dashboards) fall back from readability article extraction to whole-page visible text, because the
   live numbers sit in widgets, not prose (verified: this is exactly where weather.com's temperatures
   were). `read_article` gains the same render escalation.
4. **Synthesis moves into the loop** (out of `jarvis.py`): same grounding contract — answer ONLY from
   findings, name sources, say plainly when findings don't contain the answer.

**Prompt-injection bound (the load-bearing design choice):** the model **never types a URL**. It
selects an index into a menu that deterministic code extracted from pages we already chose to fetch;
every fetch is read-only + SSRF-guarded. Hostile page text can at most nudge which *existing* link is
opened next — it cannot mint a URL, so it cannot encode data for exfiltration. The same closed-menu
philosophy as the action catalog (ADR-019) and the persona vocabulary (ADR-036).

## Consequences
- **Gained:** live-data questions become answerable — verified end-to-end on the motivating case: the
  rendered + whole-page-text browse of weather.com returns "As of 2:31 PM PDT, 85°, High 81°/Low 64°,
  rain 0%, humidity 48%…" where the static read returned one editorial sentence. General research depth
  (open → follow a link → open) for everything else.
- **Paid:** a research turn can take tens of seconds (each fetch ≤45 s subprocess cap, 120 s loop
  wall); ~300 MB transient RAM while Chromium lives; ~160 MB disk for the browser; `playwright` joins
  requirements as an optional dependency.
- **Risk accepted:** rendered fetches execute page JavaScript — inside Chromium's own sandbox, in the
  egress subprocess, with nothing of ours to read and no persistent state. Scraped text remains hostile
  input bounded by the closed action catalog + consent gate downstream (ADR-034 posture unchanged).
- `jarvis.py` shrinks (synthesis machinery moved out); the loop is offline-tested with fakes (9 tests:
  injection bound, every cap, error honesty, degradation paths).

## Alternatives Considered
- **A per-domain structured action (e.g. wttr.in for weather)** — rejected as the *primary* fix: it
  patches one query class; the user's directive was general research capability ("click what's
  relevant, research within the page"). A structured fast-path can still be added later.
- **Resident headless browser as the default fetch** — rejected (ADR-034's RAM reasoning stands):
  escalate on demand instead; most pages never need rendering.
- **Letting the model emit URLs to open** — rejected: an LLM reading hostile web text must not get a
  free-form fetch primitive (exfiltration channel). Closed-menu index selection costs nothing and
  removes the channel.
- **Unbounded research recursion ("keep clicking until done")** — rejected: a 7B can loop forever on
  an ambiguous question; hard caps + wall clock + strict parse make the worst case a bounded, honest
  "here's what I found".
