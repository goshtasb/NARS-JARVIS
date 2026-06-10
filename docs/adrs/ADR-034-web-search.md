# ADR-034: Keyless web search & article reading (read-only network egress)

## Status
Accepted & live-verified. Crosses the network "Rubicon" for the first time — adds a **guarded, read-only**
web egress so the overnight engine can fetch external facts. Suite 437 → **444**. Tagged **v1.9.0**.

## Context
The overnight engine (ADR-031/032) was stable but isolated — it could read local files, not the web. Paid
search APIs (Brave/Google) need a credit card + secrets, violating the zero-cost/keyless goal. The user
authorized opening a network path. This supersedes the spirit of ADR-015's "no network" stance for the
*conversational actions* tier (the README's "nothing leaves your machine" claim is retired and rewritten).

## Decision
Two read-only `kind="query"` primitives (so `overnight.safe_autonomous` auto-tags them **Autonomous /
green** — safe to run unattended):
- **`web_lookup <query>`** — keyless DuckDuckGo text search → top-5 `[{title,url,snippet}]`.
- **`read_article <url>`** — fetch a page and extract its **main article text** (readability-lxml).

**Lightweight HTTP, NOT a headless browser.** We rejected Playwright: the machine runs the 7B at ~88% RAM
and a per-call Chromium (~300–500 MB) is an OOM/swap risk overnight — the opposite of what we hardened. A
stdlib `urllib` GET to DuckDuckGo's *server-rendered text endpoint* returns the same HTML at a ~15 MB
footprint. (`src/actions/web.py`; deps: `readability-lxml` + `beautifulsoup4` + `lxml`, HTTP via stdlib.)

**Isolated subprocess (the brain stays network-free).** `run.perform`'s `query` branch spawns `web.py`
via the sanctioned **`safespawn`** seam (`[sys.executable, web.py, <mode>, <arg>]`, bounded by a timeout).
Network + readability load only in that short-lived child; the persistent daemon process never opens a
socket. (Note: `web.py` uses `urllib`, not `subprocess`, so the `test_no_raw_subprocess` AST guard holds.)

**Safety hardening (web content is treated as hostile):**
- **SSRF guard** — only `http(s)` to *public* hosts; loopback/private/link-local/reserved and `file:` are
  rejected (the model can emit any URL and this runs unattended).
- **Bounded read** — `response.read(3 MB)` cap + early `Content-Length` bail + a `Content-Type` guard that
  only accepts `text/html` (a 50 MB or PDF/binary payload can never blow up the subprocess; PDFs are
  `read_file`'s job).
- **Fail-closed** — bounded retry w/ backoff+jitter on 403/429, a same-provider alternate
  (`html.duckduckgo.com` ↔ `lite.duckduckgo.com`), then an explicit `[ERROR: …]` string. Never silent,
  never a faked blank, never auto-hops to Google (a worse scrape target).
- **TLS via the OS Keychain** — `truststore` makes `urllib` verify against the macOS trust store, so a
  corporate/proxy root the browser already trusts works **without disabling verification** (live repro:
  a TLS-intercepting proxy caused `CERTIFICATE_VERIFY_FAILED` until truststore was injected).
- **Prompt-injection containment** — scraped text only becomes a context string; an injected
  `[[DO: empty_trash]]` still hits the closed catalog → consent gate → (overnight) the Held ledger. The
  egress cannot escalate.

**We KEPT `execution/test_network_invariants.py`** (correcting the PRD, which proposed deleting it): it
scopes solely to the OmniGlass autonomous sandbox tier, which **stays air-gapped**. Web actions live in
`actions/`, trip no test, and the dangerous tier keeps its guarantee.

## Consequences
- **Gained:** the overnight engine can research the web, keyless and zero-cost, without threatening RAM.
- **Live-verified:** `web_lookup "opennars…"` → real parsed results (uddg redirects decoded);
  `read_article https://www.opennars.org/` → clean article text; `file://` + `127.0.0.1` → SSRF-blocked;
  TLS works through the proxy via truststore; output is clean JSON on stdout (warnings stay on stderr).
- **Tests:** +7 — SSRF guard (IP-literal, offline), `parse_ddg` (both layouts + redirect decode + DOM-change
  error), `extract_article` (keeps body, drops nav/script), error pass-through, run-routing via mocked
  spawn, classifier autonomy. Suite **444**.
- **Privacy regression (documented honestly):** search queries — possibly derived from local docs — now go
  to DuckDuckGo. README updated; "nothing leaves your machine" retired.
- **Honest limits:** scraping is brittle + ToS-grey (DDG can change its DOM / rate-limit → reported as
  `[ERROR…]`, never silent); the subprocess blocks the loop for the fetch (timeout-bounded; fine given the
  7B already blocks); composition is flat-list (no auto-piping `web_lookup`→`summarize_file`).

## Alternatives Considered
- **Playwright headless browser:** rejected — OOM risk on a RAM-constrained box; unnecessary for DDG's
  server-rendered text endpoint.
- **Paid search API (Brave/Google):** rejected — credit card + secrets management; violates keyless/zero-cost.
- **Auto-fallback to scraping Google on a DDG block:** rejected — Google blocks unauth scraping harder; two
  flaky scrapers ≠ reliability. Retry same-provider, then fail-closed.
- **Deleting the execution air-gap test:** rejected — it guards a different (still air-gapped) tier.
- **Disabling TLS verification to dodge the proxy cert:** rejected — insecure; truststore (OS trust store)
  is the correct fix.
